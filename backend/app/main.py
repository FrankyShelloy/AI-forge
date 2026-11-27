"""FastAPI application serving SSE Chat API, Quality Analytics, Email HITL sync, and React SPA."""

from __future__ import annotations

import json
import logging
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agent import RegulationAgent
from .analytics import build_quality_analytics
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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        stats = retriever.bootstrap_index(force_reindex=False)
        logger.info("Knowledge base ready: %s", stats)
    except Exception as exc:
        logger.error("Initial KB bootstrap error (will retry on demand): %s", exc)
    email_service.start_background_worker()
    yield
    await email_service.stop_background_worker()


app = FastAPI(title="Softline Quality AI Agent", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
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


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "llm_model": settings.llm_model,
        "embedding_model": settings.embedding_model,
        "docs_indexed": len(db.list_indexed_docs()),
        "faq_active": len(db.list_faqs(active_only=True)),
        "webmail_url": settings.webmail_url,
        "default_employee_email": settings.default_employee_email,
        "quality_email": settings.quality_email,
    }


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Пустой запрос")

    async def event_gen():
        try:
            async for ev in agent.stream_turn(
                session_id=req.session_id,
                user_email=req.user_email or settings.default_employee_email,
                user_message=req.message.strip(),
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
async def list_sessions() -> list[dict[str, Any]]:
    return db.list_sessions(limit=40)


@app.get("/api/sessions/{session_id}/messages")
async def get_session_messages(session_id: str) -> list[dict[str, Any]]:
    return db.get_messages(session_id, limit=100)


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str) -> dict[str, str]:
    db.delete_session(session_id)
    return {"status": "deleted"}


@app.post("/api/escalate")
async def manual_escalate(req: ManualEscalateRequest) -> dict[str, Any]:
    ticket = await email_service.send_escalation(
        session_id=req.session_id,
        user_email=req.user_email or settings.default_employee_email,
        question=req.question,
        context_summary="Инициировано сотрудником вручную из интерфейса чата",
        directorate=req.directorate,
    )
    return ticket


@app.post("/api/mail/sync")
async def sync_mail_now() -> dict[str, Any]:
    """Immediately poll IMAP inbox for verified Quality Service replies (ideal for live demo)."""
    return await email_service.sync_inbox_now()


@app.get("/api/tickets")
async def list_tickets() -> list[dict[str, Any]]:
    return db.list_tickets(limit=100)


@app.get("/api/faqs")
async def list_faqs() -> list[dict[str, Any]]:
    return db.list_faqs(active_only=False)


@app.patch("/api/faqs/{faq_id}")
async def toggle_faq(faq_id: str, body: FaqToggleRequest) -> dict[str, str]:
    if body.status not in {"active", "disabled"}:
        raise HTTPException(status_code=400, detail="Invalid status")
    db.toggle_faq_status(faq_id, body.status)
    if body.status == "disabled":
        retriever.remove_faq_entry(faq_id)
    else:
        faqs = [f for f in db.list_faqs(active_only=False) if f["id"] == faq_id]
        if faqs:
            retriever.index_faq_entry(faqs[0])
    return {"status": body.status}


@app.get("/api/analytics")
async def get_analytics() -> dict[str, Any]:
    return build_quality_analytics(db)


@app.get("/api/documents")
async def list_documents() -> list[dict[str, Any]]:
    return db.list_indexed_docs()


@app.post("/api/documents/upload")
async def upload_document(file: UploadFile = File(...)) -> dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="Поддерживаются только файлы .docx")
    docs_dir = Path(settings.docs_dir)
    docs_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename).name
    dest = docs_dir / safe_name
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    try:
        return retriever.index_uploaded_docx(dest)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# Serve built React SPA if present
STATIC_DIR = Path(__file__).parent / "static_dist"
if STATIC_DIR.exists():
    assets_dir = STATIC_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        candidate = STATIC_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
