"""FastAPI application serving SSE + REST Chat API (DION-ready), Regulation Explorer, Quality Analytics, Email HITL, RBAC, and React SPA."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agent import RegulationAgent
from .analytics import build_quality_analytics, export_quality_report_md
from .config import get_settings
from .db import Database
from .email_service import EmailHITLService
from .retrieval import HybridRetriever

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

settings = get_settings()
db = Database(settings.sqlite_path)
retriever = HybridRetriever(settings, db)
email_service = EmailHITLService(settings, db, retriever)
agent = RegulationAgent(settings, db, retriever, email_service)

# Lightweight in-memory sliding-window rate limiter for SMTP/IMAP endpoints
_rate_buckets: dict[str, deque[float]] = defaultdict(deque)


def _check_rate_limit(bucket_key: str, max_calls: int, window_sec: float = 60.0) -> None:
    now = time.monotonic()
    q = _rate_buckets[bucket_key]
    while q and (now - q[0]) > window_sec:
        q.popleft()
    if len(q) >= max_calls:
        raise HTTPException(
            status_code=429,
            detail=f"Превышен лимит запросов ({max_calls} за {int(window_sec)} с). Повторите чуть позже.",
        )
    q.append(now)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        stats = await asyncio.to_thread(retriever.bootstrap_index, False)
        logger.info("Knowledge base ready: %s", stats)
    except Exception as exc:
        logger.error("Initial KB bootstrap error (will retry on demand): %s", exc)
    email_service.start_background_worker()
    yield
    await email_service.stop_background_worker()


app = FastAPI(
    title="Softline Quality AI Agent",
    description="ИИ-агент поиска ответов по корпоративным регламентам (Web UI + DION/Webhook JSON API)",
    version="2.3.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str
    user_email: str | None = None


class ManualEscalateRequest(BaseModel):
    session_id: str
    question: str
    user_email: str | None = None
    directorate: str | None = None


class FaqToggleRequest(BaseModel):
    status: str  # active | disabled


class QualityPinRequest(BaseModel):
    pin: str


def _require_quality_role(x_quality_key: str, action_desc: str) -> None:
    if (x_quality_key or "").strip() != settings.quality_admin_pin:
        db.log_security_event(
            event_type="rbac_access_blocked",
            actor="employee_ui",
            detail=f"Отклонена попытка «{action_desc}» без ключа роли Службы качества (RBAC: все читают регламенты, управляет и видит аналитику только Качество).",
        )
        raise HTTPException(
            status_code=403,
            detail="Доступ разрешён только Службе качества. Переключите роль в верхней панели.",
        )


def _check_session_access(session_id: str, client_id: str) -> None:
    sess = db.get_session(session_id)
    if not sess:
        return
    cid = (client_id or "default").strip()[:64]
    owner = sess.get("client_id") or "default"
    if owner != cid:
        raise HTTPException(
            status_code=403,
            detail="Приватный диалог 1-на-1 принадлежит другой пользовательской сессии.",
        )


@app.api_route("/robots.txt", methods=["GET", "HEAD"], include_in_schema=False)
async def robots_txt() -> PlainTextResponse:
    return PlainTextResponse("User-agent: *\nDisallow: /\n", media_type="text/plain")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    docs = db.list_indexed_docs()
    return {
        "status": "ok",
        "version": "2.3.0",
        "llm_model": settings.llm_model,
        "embedding_model": settings.embedding_model,
        "docs_indexed": len(docs),
        "chunks_indexed": sum(int(d["chunk_count"]) for d in docs),
        "faq_active": len(db.list_faqs(active_only=True)),
        "webmail_url": settings.webmail_url,
        "default_employee_email": settings.default_employee_email,
        "quality_email": settings.quality_email,
    }


@app.post("/api/auth/verify-quality")
async def verify_quality_pin(body: QualityPinRequest) -> dict[str, Any]:
    if body.pin.strip() != settings.quality_admin_pin:
        db.log_security_event(
            event_type="invalid_quality_pin",
            actor="web_ui",
            detail="Неверная попытка ввода PIN-кода роли Службы качества.",
        )
        raise HTTPException(status_code=403, detail="Неверный ключ доступа Службы качества")
    return {"ok": True, "role": "quality"}


@app.get("/api/system/models")
async def system_models() -> dict[str, Any]:
    """Return open-weight model governance & isolated deployment metadata."""
    return {
        "perimeter_mode": "Open-Weight Apache 2.0 (100% on-prem portable via --profile local)",
        "vram_limit_gb": 128,
        "estimated_local_vram_gb": 23,
        "models": [
            {
                "role": "LLM (Маршрутизация + Генерация)",
                "id": settings.llm_model,
                "hf_repo": "Qwen/Qwen3-30B-A3B-Instruct-2507",
                "hf_url": "https://huggingface.co/Qwen/Qwen3-30B-A3B-Instruct-2507",
                "license": "Apache 2.0",
                "architecture": "MoE (30B всего / 3B активных, без thinking-режима)",
                "vram_q4_gb": 19,
            },
            {
                "role": "Embeddings (Гибридный поиск)",
                "id": settings.embedding_model,
                "hf_repo": "Qwen/Qwen3-Embedding-4B",
                "hf_url": "https://huggingface.co/Qwen/Qwen3-Embedding-4B",
                "license": "Apache 2.0",
                "architecture": "Dense 2560 dim, контекст 32k + BM25 + RRF",
                "vram_q4_gb": 4,
            },
        ],
        "integrations": {
            "dion_webhook_endpoint": "/api/chat",
            "openapi_docs": "/docs",
            "mail_whitelist": sorted(settings.whitelist_set),
            "rbac_policy": "Чтение регламентов — все сотрудники (строго изолированные сессии X-Client-Id); аналитика, журнал тикетов, управление FAQ и загрузка .docx — только Служба качества (X-Quality-Key + Email Whitelist).",
            "official_regulation_priority": "Официальный регламент всегда главнее FAQ; при расхождении подсвечивается конфликт.",
        },
    }


@app.post("/api/chat")
async def chat_sync(
    req: ChatRequest,
    x_client_id: str = Header(default="default"),
) -> dict[str, Any]:
    """Synchronous JSON endpoint ready for DION / Telegram / corporate webhook bots."""
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Пустой запрос")
    return await agent.answer_turn_sync(
        session_id=req.session_id,
        user_email=req.user_email or settings.default_employee_email,
        user_message=req.message.strip(),
        client_id=x_client_id,
    )


@app.post("/api/chat/stream")
async def chat_stream(
    req: ChatRequest,
    x_client_id: str = Header(default="default"),
) -> StreamingResponse:
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Пустой запрос")

    async def event_gen():
        try:
            async for ev in agent.stream_turn(
                session_id=req.session_id,
                user_email=req.user_email or settings.default_employee_email,
                user_message=req.message.strip(),
                client_id=x_client_id,
            ):
                payload = json.dumps(ev["data"], ensure_ascii=False)
                yield f"event: {ev['event']}\ndata: {payload}\n\n"
        except Exception as exc:
            logger.exception("Chat stream error")
            err = json.dumps({"detail": str(exc)}, ensure_ascii=False)
            yield f"event: error\ndata: {err}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/sessions")
async def list_sessions(x_client_id: str = Header(default="default")) -> list[dict[str, Any]]:
    return db.list_sessions(client_id=x_client_id, limit=40)


@app.get("/api/sessions/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    x_client_id: str = Header(default="default"),
) -> list[dict[str, Any]]:
    _check_session_access(session_id, x_client_id)
    return db.get_messages(session_id, limit=100)


@app.delete("/api/sessions/{session_id}")
async def delete_session(
    session_id: str,
    x_client_id: str = Header(default="default"),
) -> dict[str, str]:
    _check_session_access(session_id, x_client_id)
    db.delete_session(session_id, client_id=x_client_id)
    return {"status": "deleted"}


@app.post("/api/escalate")
async def manual_escalate(
    req: ManualEscalateRequest,
    request: Request,
    x_client_id: str = Header(default="default"),
) -> dict[str, Any]:
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(f"esc:{x_client_id}:{client_ip}", max_calls=5, window_sec=60.0)
    _check_session_access(req.session_id, x_client_id)
    ticket = await email_service.send_escalation(
        session_id=req.session_id,
        user_email=req.user_email or settings.default_employee_email,
        question=req.question,
        context_summary="Инициировано сотрудником вручную из интерфейса чата",
        directorate=req.directorate,
    )
    db.add_message(
        session_id=req.session_id,
        role="assistant",
        content=(
            f"Обращение передано в Службу качества вручную по вашей инициативе.\n\n"
            f"- **Номер обращения:** `{ticket['ticket_code']}`\n"
            f"- **Кому:** `{settings.quality_email}` (вы в копии: `{ticket['user_email']}`)"
        ),
        outcome="escalated",
        ticket_code=ticket["ticket_code"],
    )
    return ticket


@app.post("/api/mail/sync")
async def sync_mail_now(
    request: Request,
    x_client_id: str = Header(default="default"),
) -> dict[str, Any]:
    """Immediately poll IMAP inbox for verified Quality Service replies."""
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(f"sync:{x_client_id}:{client_ip}", max_calls=15, window_sec=60.0)
    return await email_service.sync_inbox_now()


@app.get("/api/tickets")
async def list_tickets(x_quality_key: str = Header(default="")) -> list[dict[str, Any]]:
    _require_quality_role(x_quality_key, "просмотр журнала обращений сотрудников (tickets)")
    return db.list_tickets(limit=100)


@app.get("/api/faqs")
async def list_faqs(x_quality_key: str = Header(default="")) -> list[dict[str, Any]]:
    is_quality = (x_quality_key or "").strip() == settings.quality_admin_pin
    return db.list_faqs(active_only=not is_quality)


@app.patch("/api/faqs/{faq_id}")
async def toggle_faq(
    faq_id: str,
    body: FaqToggleRequest,
    x_quality_key: str = Header(default=""),
) -> dict[str, str]:
    _require_quality_role(x_quality_key, f"изменение статуса FAQ {faq_id}")
    if body.status not in {"active", "disabled"}:
        raise HTTPException(status_code=400, detail="Invalid status")
    db.toggle_faq_status(faq_id, body.status)
    if body.status == "disabled":
        await asyncio.to_thread(retriever.remove_faq_entry, faq_id)
    else:
        faqs = [f for f in db.list_faqs(active_only=False) if f["id"] == faq_id]
        if faqs:
            await asyncio.to_thread(retriever.index_faq_entry, faqs[0])
    return {"status": body.status}


@app.get("/api/analytics")
async def get_analytics(x_quality_key: str = Header(default="")) -> dict[str, Any]:
    _require_quality_role(x_quality_key, "просмотр аналитики Службы качества")
    return build_quality_analytics(db)


@app.get("/api/analytics/export")
async def export_analytics_report(x_quality_key: str = Header(default="")) -> PlainTextResponse:
    _require_quality_role(x_quality_key, "экспорт отчёта Службы качества")
    md = export_quality_report_md(db)
    return PlainTextResponse(
        content=md,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="softline_quality_report.md"'},
    )


@app.get("/api/documents")
async def list_documents() -> list[dict[str, Any]]:
    return db.list_indexed_docs()


@app.get("/api/documents/{doc_id}")
async def get_document_detail(doc_id: str) -> dict[str, Any]:
    """Return full structured sections & clauses of an indexed regulation plus linked Quality FAQs."""
    doc = db.get_doc_with_clauses(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Регламент не найден")
    all_faqs = db.list_faqs(active_only=True)
    doc["linked_faqs"] = [
        f for f in all_faqs if f.get("directorate") in {doc["directorate"], "ALL", "MULTI"}
    ]
    return doc


@app.post("/api/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    x_quality_key: str = Header(default=""),
) -> dict[str, Any]:
    _require_quality_role(x_quality_key, f"загрузка регламента {file.filename}")
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="Поддерживаются только файлы .docx")
    content = await file.read()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Размер файла превышает допустимый лимит {settings.max_upload_mb} МБ",
        )
    if len(content) < 100:
        raise HTTPException(status_code=400, detail="Файл повреждён или пуст")
    docs_dir = Path(settings.docs_dir)
    docs_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename).name
    dest = docs_dir / safe_name
    dest.write_bytes(content)
    try:
        return await asyncio.to_thread(retriever.index_uploaded_docx, dest)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/documents/{doc_id}")
async def delete_document(
    doc_id: str,
    x_quality_key: str = Header(default=""),
) -> dict[str, str]:
    """Delete a custom uploaded regulation (.docx) cleanly from Qdrant, BM25, SQLite, and disk."""
    _require_quality_role(x_quality_key, f"удаление регламента {doc_id}")
    core_ids = {"dbrct_project", "tech_dir_project", "ib_presale", "ib_project"}
    if doc_id in core_ids:
        raise HTTPException(
            status_code=400,
            detail="Базовые 4 регламента хакатона защищены от случайного удаления.",
        )
    deleted = db.delete_indexed_doc(doc_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Регламент не найден")
    await asyncio.to_thread(retriever.delete_doc_chunks, doc_id)
    doc_file = Path(settings.docs_dir) / Path(deleted["filename"]).name
    if doc_file.is_file():
        try:
            doc_file.unlink()
        except Exception as exc:
            logger.warning("Could not unlink %s: %s", doc_file, exc)
    return {"status": "deleted", "doc_id": doc_id}


# Serve built React SPA if present (strictly confined inside STATIC_DIR to prevent path traversal)
STATIC_DIR = Path(__file__).parent / "static_dist"
STATIC_ROOT = STATIC_DIR.resolve()
if STATIC_DIR.exists():
    assets_dir = STATIC_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        rel = full_path.lstrip("/")
        if rel:
            try:
                candidate = (STATIC_ROOT / rel).resolve()
                if candidate.is_relative_to(STATIC_ROOT) and candidate.is_file():
                    return FileResponse(candidate)
            except Exception:
                pass
        return FileResponse(STATIC_ROOT / "index.html")
