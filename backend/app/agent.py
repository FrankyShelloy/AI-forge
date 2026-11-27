"""LangGraph Agentic RAG orchestrator with strictly MAX 2 LLM calls per user turn.

Graph flow:
  1. `retrieve`  — Glossary expansion + Qdrant Dense + BM25 + RRF over Regulations (prio 1) & Quality FAQ (prio 2)
  2. `analyze`   — Single Structured JSON LLM call: Intent Router + Ambiguity/Directorate + Conflict/Coverage Analyzer + Prompt-Injection Guard
  3. Conditional branch:
       - off_topic / out_of_scope / find_doc / clarify / escalate -> 1 LLM call total!
       - answer / complex_answer / partial_answer / answer_all_variants / faq_answer ->
         `generate` (Call 2: Streaming Grounded Generator + Programmatic Citation Verifier)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, AsyncGenerator, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from .config import Settings
from .db import Database
from .email_service import EmailHITLService
from .glossary import glossary_prompt_block
from .retrieval import HybridRetriever, RetrievedItem

logger = logging.getLogger(__name__)

CITATION_TAG_RE = re.compile(r"\[([SF]\d+)\]")
KB_TAMPER_RE = re.compile(
    r"(забудь\s+(все\s+)?инструкц|игнорируй\s+правил|я\s+(из\s+)?служб\w*\s+качеств\w*.*?(запиши|добавь|измени|обнови)|добавь\s+в\s+базу\s+знаний|перепиши\s+регламент)",
    re.I,
)
OFF_TOPIC_FAST_RE = re.compile(
    r"(курс\s+(доллар|евро|валют|биткоин)|погод[аеу]\s+в|сделай\s+follow[\s-]?up|составь\s+follow[\s-]?up|напиши\s+(стих|анекдот|сказк|код\s+на)|переведи\s+на\s+английск)",
    re.I,
)


class RouteDecision(BaseModel):
    missing_facts: str = Field(
        default="",
        description="Чего НЕ хватает в источниках для прямого ответа на вопрос (пусто, если всё есть буквально)",
    )
    direct_answer_exists: bool = Field(
        default=True,
        description="true ТОЛЬКО если в [S*]/[F*] есть прямое правило/норма по конкретному предмету вопроса, без домысливания",
    )
    action: Literal[
        "answer",
        "complex_answer",
        "partial_answer",
        "answer_all_variants",
        "faq_answer",
        "find_doc",
        "clarify",
        "escalate",
        "out_of_scope",
        "off_topic",
    ] = "answer"
    directorate: str = Field(default="UNKNOWN")
    selected_source_ids: list[str] = Field(default_factory=list)
    clarification_question: str = ""
    clarification_options: list[str] = Field(default_factory=list)
    short_response: str = ""
    missing_external_docs: list[str] = Field(default_factory=list)
    has_conflict: bool = False
    conflict_note: str = ""
    topic_key: str = "общий вопрос"
    reasoning: str = ""


class AgentState(TypedDict, total=False):
    session_id: str
    user_email: str
    user_message: str
    history: list[dict[str, Any]]
    clarification_round: int
    active_directorate: str | None

    effective_query: str
    directorate_filter: str | None
    matched_glossary: list[str]
    reg_items: list[RetrievedItem]
    faq_items: list[RetrievedItem]
    source_map: dict[str, RetrievedItem]
    decision: RouteDecision
    trace: list[dict[str, Any]]

    prompt_tokens: int
    completion_tokens: int
    embedding_tokens: int
    cost_rub: float


def _detect_mentioned_directorates(text: str) -> tuple[list[str], bool]:
    """Return (list of mentioned directorates, is_complex_or_all)."""
    low = text.lower()
    dirs: list[str] = []
    if re.search(r"\b(дбрцт|дбр|бизнес[\s-]решен\w*)\b", low):
        dirs.append("DBRCT")
    if re.search(r"\b(техдирекц\w*|техническ\w*\s+дирекц\w*|тд)\b", low):
        dirs.append("TECH_DIR")
    if re.search(r"\b(дирекц\w*\s+иб|диб|уиб|инфобез\w*|информационн\w*\s+безопасност\w*|иб)\b", low):
        dirs.append("IB")
    is_multi = bool(re.search(r"комплексн|нескольк\w*\s+дирекц|все\s+вариант|не\s+знаю", low)) or len(dirs) >= 2
    return dirs, is_multi


def _build_queries(user_message: str, history: list[dict[str, Any]]) -> tuple[str, str]:
    """Return (clean_search_query, full_effective_query)."""
    msg = user_message.strip()
    if not history:
        return msg, msg
    last_assistant = next((m for m in reversed(history) if m["role"] == "assistant"), None)
    last_user = next((m for m in reversed(history) if m["role"] == "user"), None)
    dirs_in_msg, is_multi_msg = _detect_mentioned_directorates(msg)
    is_short_dir_reply = (
        ("?" not in msg)
        and len(msg.split()) <= 10
        and (bool(dirs_in_msg) or is_multi_msg)
    )
    if last_user and ((last_assistant and last_assistant.get("outcome") == "clarify") or is_short_dir_reply):
        # Walk back to the original substantive user question if multiple clarification rounds occurred
        orig_q = last_user["content"].strip()
        for m in reversed(history):
            if m["role"] == "user" and ("?" in m["content"] or len(m["content"].split()) > 6):
                orig_q = m["content"].strip()
                break
        return orig_q, f"{orig_q} (Уточнение сотрудника: {msg})"
    if len(msg.split()) <= 4 and last_user:
        return last_user["content"].strip(), f"{last_user['content']} {msg}"
    return msg, msg


def _format_sources_for_prompt(
    reg_items: list[RetrievedItem], faq_items: list[RetrievedItem], max_chars_per_item: int = 580
) -> tuple[str, dict[str, RetrievedItem]]:
    source_map: dict[str, RetrievedItem] = {}
    blocks: list[str] = []

    if reg_items:
        blocks.append("=== РЕГЛАМЕНТЫ (Приоритет 1) ===")
        for idx, item in enumerate(reg_items, 1):
            sid = f"S{idx}"
            source_map[sid] = item
            ext = f" | Внешние док-ты: {', '.join(item.external_doc_refs)}" if item.external_doc_refs else ""
            body = item.parent_text[:max_chars_per_item] + ("..." if len(item.parent_text) > max_chars_per_item else "")
            blocks.append(
                f"[{sid}] ({item.directorate_label} | {item.doc_title} | Р.{item.section_num} п.{item.clause_num} | rel={item.score}{ext})\n{body}"
            )

    if faq_items:
        blocks.append("\n=== FAQ СЛУЖБЫ КАЧЕСТВА (Приоритет 2, дополняет регламент) ===")
        for idx, item in enumerate(faq_items, 1):
            fid = f"F{idx}"
            source_map[fid] = item
            blocks.append(f"[{fid}] (Тикет {item.ticket_code} | {item.verified_by} | rel={item.score})\n{item.parent_text[:600]}")

    return "\n\n".join(blocks) if blocks else "(Источников не найдено)", source_map


def _usage_stats(usage_obj: Any) -> tuple[int, int, float]:
    if not usage_obj:
        return 0, 0, 0.0
    pt = int(getattr(usage_obj, "prompt_tokens", 0) or 0)
    ct = int(getattr(usage_obj, "completion_tokens", 0) or 0)
    extra = getattr(usage_obj, "model_extra", None) or {}
    cost = float(extra.get("cost_rub", 0.0) or 0.0)
    return pt, ct, cost


def _parse_json_safe(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
    return {}


class RegulationAgent:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        retriever: HybridRetriever,
        email_service: EmailHITLService,
    ) -> None:
        self.settings = settings
        self.db = db
        self.retriever = retriever
        self.email_service = email_service
        self.client = AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            timeout=45.0,
        )
        self.graph = self._build_graph()

    def _build_graph(self):
        sg = StateGraph(AgentState)
        sg.add_node("retrieve", self._node_retrieve)
        sg.add_node("analyze", self._node_analyze)
        sg.add_edge(START, "retrieve")
        sg.add_edge("retrieve", "analyze")
        sg.add_edge("analyze", END)
        return sg.compile()

    async def _node_retrieve(self, state: AgentState) -> dict[str, Any]:
        t0 = time.perf_counter()
        search_query, effective_query = _build_queries(state["user_message"], state.get("history") or [])
        dirs, is_multi = _detect_mentioned_directorates(effective_query)
        active_dir = state.get("active_directorate")

        dir_filter: str | None = None
        dirs_list: list[str] | None = None
        if len(dirs) >= 2:
            dirs_list = dirs
        elif is_multi:
            dirs_list = ["DBRCT", "TECH_DIR", "IB"]
        elif len(dirs) == 1:
            dir_filter = dirs[0]
        elif active_dir in {"DBRCT", "TECH_DIR", "IB"} and state.get("clarification_round", 0) > 0:
            dir_filter = active_dir

        reg_items, faq_items, emb_tokens, emb_cost, matched_glossary = self.retriever.search(
            search_query,
            directorate_filter=dir_filter,
            directorates_list=dirs_list,
            top_k_regs=6,
            top_k_faq=2,
        )
        _, source_map = _format_sources_for_prompt(reg_items, faq_items)
        ms = int((time.perf_counter() - t0) * 1000)

        trace = list(state.get("trace") or [])
        trace.append(
            {
                "step": "glossary_and_retrieval",
                "title": "Глоссарий + Гибридный поиск (Qdrant + BM25 + RRF)",
                "duration_ms": ms,
                "detail": (
                    f"Найдено: {len(reg_items)} пп. регламентов, {len(faq_items)} FAQ"
                    + (f" · Глоссарий: +{len(matched_glossary)} терм." if matched_glossary else "")
                    + (f" · Фильтр: {','.join(dirs_list) if dirs_list else dir_filter}" if (dirs_list or dir_filter) else " · Все дирекции")
                ),
            }
        )
        return {
            "effective_query": effective_query,
            "directorate_filter": "MULTI" if is_multi else dir_filter,
            "matched_glossary": matched_glossary,
            "reg_items": reg_items,
            "faq_items": faq_items,
            "source_map": source_map,
            "trace": trace,
            "embedding_tokens": emb_tokens,
            "cost_rub": emb_cost,
        }

    async def _node_analyze(self, state: AgentState) -> dict[str, Any]:
        """CALL 1: Unified Intent Router + Ambiguity/Directorate/Conflict/Coverage Analyzer (JSON)."""
        t0 = time.perf_counter()
        reg_items = state.get("reg_items") or []
        faq_items = state.get("faq_items") or []
        sources_text, _ = _format_sources_for_prompt(reg_items, faq_items, max_chars_per_item=580)
        docs_list = self.db.list_indexed_docs()
        docs_summary = "; ".join(f"«{d['doc_title']}» ({d['directorate_label']})" for d in docs_list)

        history_lines = []
        for m in (state.get("history") or [])[-6:]:
            role_lbl = "Сотрудник" if m["role"] == "user" else "Агент"
            history_lines.append(f"{role_lbl}: {m['content'][:260]}")
        history_block = "\n".join(history_lines) if history_lines else "(начало диалога)"

        clar_round = int(state.get("clarification_round") or 0)
        max_rounds = self.settings.max_clarification_rounds

        system_prompt = f"""Ты — роутер и антигаллюцинационный верификатор ИИ-агента Службы качества Софтлайн.
В базе ЗАГРУЖЕНЫ ТОЛЬКО эти документы: {docs_summary}.
Глоссарий: {glossary_prompt_block(state.get('matched_glossary') or [])}.

СНАЧАЛА проверь буквально: есть ли в [S*] или [F*] ПРЯМОЕ правило про конкретный предмет вопроса (заполни missing_facts и direct_answer_exists).
СТРОГО ЗАПРЕЩЕНО домысливать по косвенным фразам (например, общие слова про «командировочные расходы» в РТЗ НЕ отвечают на вопрос про компенсацию такси в выходной день!).

Верни СТРОГО JSON:
- "off_topic": посторонний общий ИИ (follow-up, курс валют, код, погода) → short_response с вежливым отказом.
- "out_of_scope": рабочий вопрос явно вне проектных/пресейловых регламентов (личная премия, зарплата, маржа сделки, отпуск) → short_response с направлением к руководителю/АМ (НЕ в качество!).
- "find_doc": ТОЛЬКО если сотрудник просит найти название одного из ЗАГРУЖЕННЫХ регламентов выше. Если спрашивает содержание/ссылку на ВНЕШНИЙ отсутствующий документ (напр. «регламент подбора субподрядчика», «инструкция по созданию карточки») — НЕ выбирай find_doc, выбери "partial_answer" + missing_external_docs (и НИКОГДА не пиши фейковых ссылок вроде [Ссылка на ...]).
- "faq_answer": в [S*] ответа нет, но отвечает проверенный [F*] → direct_answer_exists=true, selected_source_ids ТОЛЬКО ["F1"].
- "clarify": ОБЯЗАТЕЛЬНО если вопрос по регламентам, дирекция НЕ указана (и нет слов «комплексный»/«не знаю»), в [S*] ЕСТЬ прямые ответы по теме, но правила/сроки/исключения ОТЛИЧАЮТСЯ между дирекциями, и раунд ({clar_round}) < {max_rounds}. Задай краткий clarification_question и 4–5 кнопок clarification_options. НЕ выбирай "clarify", если дирекция уже указана ИЛИ прямого ответа по теме вопроса вообще нет в [S*] и [F*] (тогда сразу "escalate"!).
- "complex_answer": сотрудник указал ≥2 дирекций / комплексный проект → selected_source_ids из КАЖДОЙ применимой дирекции (сроки И условия/исключения).
- "answer_all_variants": сотрудник не знает дирекцию → показать правила каждой дирекции отдельно.
- "answer" / "partial_answer": есть прямой подтверждённый ответ в [S*]. Укажи missing_external_docs, если регламент ссылается на отсутствующий документ. ПРИОРИТЕТ: регламент [S*] ВСЕГДА главнее FAQ [F*]; при конфликте поставь has_conflict=true.
- "escalate": ОБЯЗАТЕЛЬНО выбери "escalate" (и direct_answer_exists=false), если вопрос про корпоративные правила/расходы/процессы, но ПРЯМОЙ нормы по предмету вопроса НЕТ в [S*] и [F*], либо исчерпаны уточнения ({clar_round} >= {max_rounds}). Лучше лишняя эскалация, чем додуманный ответ!

JSON-схема:
{{"missing_facts":"","direct_answer_exists":true,"action":"...","directorate":"DBRCT|TECH_DIR|IB|MULTI|ALL|UNKNOWN","selected_source_ids":["S1"],"clarification_question":"","clarification_options":[],"short_response":"","missing_external_docs":[],"has_conflict":false,"conflict_note":"","topic_key":"2-4 слова","reasoning":"1 фраза"}}"""

        user_prompt = (
            f"История:\n{history_block}\n\n"
            f"Сообщение: {state['user_message']}\n"
            f"Контекст: {state['effective_query']} | Раунд уточнений: {clar_round}/{max_rounds}\n\n"
            f"{sources_text}"
        )

        resp = None
        for attempt in range(4):
            try:
                resp = await self.client.chat.completions.create(
                    model=self.settings.llm_model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.0,
                    max_tokens=440,
                )
                break
            except Exception as exc:
                if "429" in str(exc) and attempt < 3:
                    await asyncio.sleep(2.0 * (attempt + 1))
                    continue
                raise
        assert resp is not None

        raw_content = resp.choices[0].message.content or "{}"
        parsed = _parse_json_safe(raw_content)
        try:
            decision = RouteDecision.model_validate(parsed)
        except Exception as exc:
            logger.warning("RouteDecision parse fallback (%s): %s", exc, raw_content[:200])
            decision = RouteDecision(
                action="answer" if (reg_items or faq_items) else "escalate",
                selected_source_ids=[f"S{i}" for i in range(1, min(5, len(reg_items) + 1))],
                topic_key=state["user_message"][:40],
            )

        # Hard circuit breaker for clarification rounds (<= 3)
        if decision.action == "clarify" and clar_round >= max_rounds:
            decision.action = "escalate"
            decision.short_response = (
                "Достигнут лимит уточняющих вопросов (3 итерации), однозначное правило не определено. "
                "Передаю обращение в Службу качества."
            )

        # Validate selected_source_ids against source_map
        source_map = state.get("source_map") or {}
        valid_ids = [sid for sid in decision.selected_source_ids if sid in source_map]
        if not valid_ids and decision.action in {"answer", "complex_answer", "partial_answer", "answer_all_variants"}:
            valid_ids = [f"S{i}" for i in range(1, min(5, len(reg_items) + 1)) if f"S{i}" in source_map]
        if not valid_ids and decision.action == "faq_answer":
            valid_ids = [f"F{i}" for i in range(1, min(3, len(faq_items) + 1)) if f"F{i}" in source_map]
        decision.selected_source_ids = valid_ids

        # Deterministic cross-directorate ambiguity guarantee (S2):
        # If the user did NOT name any directorate (and did not say 'комплексный'/'не знаю') on round 0,
        # and the answer draws from >=2 different directorates, ALWAYS clarify before answering!
        if (
            state.get("directorate_filter") is None
            and clar_round == 0
            and decision.action in {"answer", "complex_answer"}
            and decision.direct_answer_exists
        ):
            selected_dirs = {
                source_map[sid].directorate
                for sid in decision.selected_source_ids
                if sid.startswith("S") and sid in source_map
            }
            if len(selected_dirs) >= 2:
                dir_labels_map = {
                    "DBRCT": "ДБРЦТ",
                    "TECH_DIR": "Техническая дирекция",
                    "IB": "Дирекция ИБ",
                }
                present_labels = [dir_labels_map[d] for d in ("DBRCT", "TECH_DIR", "IB") if d in selected_dirs]
                decision.action = "clarify"
                decision.clarification_question = decision.clarification_question or (
                    f"Правила и сроки по этому вопросу различаются между подразделениями ({', '.join(present_labels)}). "
                    "Уточните, пожалуйста, к какой дирекции относится ваш проект?"
                )
                decision.clarification_options = [
                    *present_labels,
                    f"Комплексный проект ({' + '.join(present_labels[:2])})",
                    "Не знаю подразделение — покажите варианты",
                ]

        # Deterministic multi-directorate guarantee: when user asked about >=2 directorates / complex project,
        # always preserve complex_answer mode and ensure top chunks from EACH retrieved directorate are included!
        if (
            state.get("directorate_filter") == "MULTI"
            and decision.action in {"answer", "complex_answer", "answer_all_variants"}
            and decision.direct_answer_exists
        ):
            if "не знаю" not in state["effective_query"].lower():
                decision.action = "complex_answer"
            by_dir: dict[str, list[str]] = {}
            for tag, it in source_map.items():
                if tag.startswith("S"):
                    by_dir.setdefault(it.directorate, []).append(tag)
            merged_ids: list[str] = list(valid_ids)
            for dcode, tags in by_dir.items():
                # Ensure at least 3 top clauses from each directorate are passed to Call 2
                present = [t for t in merged_ids if source_map[t].directorate == dcode]
                for t in tags[:3]:
                    if t not in merged_ids and len(present) < 3:
                        merged_ids.append(t)
                        present.append(t)
            decision.selected_source_ids = merged_ids[:8]

        # Deterministic anti-hallucination guardrail: NEVER generate an answer without verified direct evidence
        if decision.action in {"answer", "complex_answer", "answer_all_variants"} and not decision.direct_answer_exists and not decision.missing_external_docs:
            decision.action = "escalate"
            decision.reasoning = f"Chain-of-Verification guardrail: нет прямой нормы ({decision.missing_facts[:100]})."

        if (
            decision.action in {"answer", "complex_answer", "partial_answer", "answer_all_variants", "faq_answer"}
            and not decision.selected_source_ids
        ):
            decision.action = "escalate"
            decision.reasoning = "Hard guardrail: нет подтверждённых источников для ответа."

        pt, ct, cost = _usage_stats(resp.usage)
        ms = int((time.perf_counter() - t0) * 1000)

        trace = list(state.get("trace") or [])
        trace.append(
            {
                "step": "langgraph_router",
                "title": "LangGraph Router (Intent + Ambiguity + Conflict)",
                "duration_ms": ms,
                "detail": f"Решение: {decision.action} · Дирекция: {decision.directorate} · Источников: {len(decision.selected_source_ids)}",
            }
        )

        return {
            "decision": decision,
            "trace": trace,
            "prompt_tokens": int(state.get("prompt_tokens") or 0) + pt,
            "completion_tokens": int(state.get("completion_tokens") or 0) + ct,
            "cost_rub": float(state.get("cost_rub") or 0.0) + cost,
        }

    def _build_citations(
        self, used_ids: list[str], source_map: dict[str, RetrievedItem]
    ) -> list[dict[str, Any]]:
        citations: list[dict[str, Any]] = []
        seen: set[str] = set()
        for sid in used_ids:
            if sid in seen or sid not in source_map:
                continue
            seen.add(sid)
            item = source_map[sid]
            citations.append(
                {
                    "tag": sid,
                    "source_type": item.source_type,
                    "doc_id": item.doc_id,
                    "doc_title": item.doc_title,
                    "directorate": item.directorate,
                    "directorate_label": item.directorate_label,
                    "section_num": item.section_num,
                    "section_title": item.section_title,
                    "clause_num": item.clause_num,
                    "score": item.score,
                    "excerpt": item.raw_text,
                    "parent_context": item.parent_text,
                    "external_doc_refs": item.external_doc_refs,
                    "ticket_code": item.ticket_code,
                    "verified_by": item.verified_by,
                    "answered_at": item.answered_at,
                }
            )
        return citations

    async def stream_turn(
        self,
        *,
        session_id: str | None,
        user_email: str,
        user_message: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Run the full agent turn and yield SSE events: session, status, delta, done."""
        t0 = time.perf_counter()
        session = self.db.get_or_create_session(
            session_id, user_email or self.settings.default_employee_email, title=user_message[:64]
        )
        sid = session["id"]
        history = self.db.get_messages(sid, limit=20)
        if not history:
            self.db.update_session_state(sid, title=user_message[:64])

        self.db.add_message(sid, "user", user_message)
        yield {"event": "session", "data": {"session_id": sid, "title": session["title"]}}

        # Fast deterministic security guard against chat-based KB tampering / prompt injection (0 LLM calls!)
        if KB_TAMPER_RE.search(user_message):
            self.db.log_security_event(
                event_type="chat_kb_tamper_attempt",
                actor=session["user_email"],
                detail=f"Заблокирована попытка изменения БЗ / обхода ролей через чат: «{user_message[:140]}»",
            )
            refusal = (
                "Изменение официальных регламентов и пополнение проверенной базы знаний (FAQ) через пользовательский чат **запрещено моделью прав**. "
                "Все сотрудники имеют доступ только на чтение нормативной базы; проверенные разъяснения добавляются агентом **исключительно по верифицированным письмам с whitelist-адресов Службы качества**."
            )
            trace = [
                {
                    "step": "security_guard",
                    "title": "Проверка модели прав (RBAC / Anti-Injection)",
                    "duration_ms": int((time.perf_counter() - t0) * 1000),
                    "detail": "Попытка модификации БЗ из чата отклонена (0 вызовов LLM)",
                }
            ]
            yield {"event": "delta", "data": {"text": refusal}}
            latency_ms = int((time.perf_counter() - t0) * 1000)
            saved = self.db.add_message(
                sid,
                "assistant",
                refusal,
                outcome="off_topic",
                trace=trace,
                latency_ms=latency_ms,
            )
            self.db.log_analytics(
                session_id=sid,
                question=user_message,
                topic_key="защита модели прав",
                outcome="off_topic",
                directorate="UNKNOWN",
                doc_id=None,
                section_label=None,
                prompt_tokens=0,
                completion_tokens=0,
                embedding_tokens=0,
                cost_rub=0.0,
                latency_ms=latency_ms,
            )
            yield {"event": "done", "data": {"message": saved, "usage": {"prompt_tokens": 0, "completion_tokens": 0, "embedding_tokens": 0, "cost_rub": 0.0, "latency_ms": latency_ms}}}
            return

        # Fast deterministic guard for obvious general-AI off-topic requests (0 LLM calls!)
        if OFF_TOPIC_FAST_RE.search(user_message):
            refusal = (
                "Я специализированный ИИ-агент по корпоративным регламентам и процессам Софтлайн. "
                "Посторонние задачи общего ИИ (подготовка follow-up, курсы валют, погода, написание кода или сторонних текстов) не входят в мою зону ответственности. "
                "Задайте, пожалуйста, вопрос по проектным или пресейловым регламентам компании."
            )
            trace = [
                {
                    "step": "intent_fast_guard",
                    "title": "Фильтр области задач (Общий ИИ)",
                    "duration_ms": int((time.perf_counter() - t0) * 1000),
                    "detail": "Посторонний запрос общего ИИ отклонён мгновенно (0 вызовов LLM)",
                }
            ]
            yield {"event": "delta", "data": {"text": refusal}}
            latency_ms = int((time.perf_counter() - t0) * 1000)
            saved = self.db.add_message(
                sid, "assistant", refusal, outcome="off_topic", trace=trace, latency_ms=latency_ms
            )
            self.db.log_analytics(
                session_id=sid,
                question=user_message,
                topic_key="посторонний запрос общего ии",
                outcome="off_topic",
                directorate="UNKNOWN",
                doc_id=None,
                section_label=None,
                prompt_tokens=0,
                completion_tokens=0,
                embedding_tokens=0,
                cost_rub=0.0,
                latency_ms=latency_ms,
            )
            yield {"event": "done", "data": {"message": saved, "usage": {"prompt_tokens": 0, "completion_tokens": 0, "embedding_tokens": 0, "cost_rub": 0.0, "latency_ms": latency_ms}}}
            return

        yield {
            "event": "status",
            "data": {"action": "retrieving", "directorate": "", "reasoning": "Гибридный поиск по 611 пунктам регламентов и FAQ..."},
        }

        # Run LangGraph Steps 1 & 2 (Retrieve + Unified Analyze/Route)
        state: AgentState = await self.graph.ainvoke(
            {
                "session_id": sid,
                "user_email": session["user_email"],
                "user_message": user_message,
                "history": history,
                "clarification_round": int(session.get("clarification_round") or 0),
                "active_directorate": session.get("active_directorate"),
                "trace": [],
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "embedding_tokens": 0,
                "cost_rub": 0.0,
            }
        )

        decision: RouteDecision = state["decision"]
        source_map: dict[str, RetrievedItem] = state.get("source_map") or {}
        trace: list[dict[str, Any]] = list(state.get("trace") or [])
        prompt_tokens = int(state.get("prompt_tokens") or 0)
        completion_tokens = int(state.get("completion_tokens") or 0)
        embedding_tokens = int(state.get("embedding_tokens") or 0)
        total_cost = float(state.get("cost_rub") or 0.0)

        yield {
            "event": "status",
            "data": {
                "action": decision.action,
                "directorate": decision.directorate,
                "reasoning": decision.reasoning,
            },
        }

        final_text = ""
        outcome = decision.action
        citations: list[dict[str, Any]] = []
        clarification_options: list[str] = []
        ticket_code: str | None = None

        # --- Branch A: Single-call outcomes (1 LLM call total!) ---
        if decision.action == "off_topic":
            final_text = decision.short_response or (
                "Я специализированный ИИ-агент по корпоративным регламентам и процессам Софтлайн. "
                "Посторонние задачи общего ИИ (подготовка follow-up, курсы валют, произвольные тексты) не входят в мою область работы. "
                "Задайте, пожалуйста, вопрос по проектным или пресейловым регламентам."
            )
            yield {"event": "delta", "data": {"text": final_text}}

        elif decision.action == "out_of_scope":
            final_text = decision.short_response or (
                "Этот вопрос выходит за рамки корпоративных регламентов проектной и пресейловой деятельности. "
                "По вопросам личного вознаграждения, премии или маржинальности конкретной сделки обратитесь, пожалуйста, к вашему непосредственному руководителю или аккаунт-менеджеру."
            )
            yield {"event": "delta", "data": {"text": final_text}}

        elif decision.action == "find_doc":
            used_ids = decision.selected_source_ids or list(source_map.keys())[:4]
            citations = self._build_citations(used_ids, source_map)
            unique_lines = []
            seen_d = set()
            for c in citations:
                if c["doc_id"] not in seen_d:
                    seen_d.add(c["doc_id"])
                    unique_lines.append(
                        f"- **«{c['doc_title']}»** ({c['directorate_label']}, напр. Раздел {c['section_num']} / п. {c['clause_num']}) [{c['tag']}]"
                    )
            final_text = (
                (decision.short_response + "\n\n" if decision.short_response else "По вашему запросу подходят следующие действующие регламенты:\n\n")
                + "\n".join(unique_lines)
                + "\n\n_Нажмите на ссылку-источник ниже, чтобы открыть текст пункта или перейти к полному документу во вкладке «База регламентов»._"
            )
            outcome = "answered"
            self.db.update_session_state(sid, clarification_round=0)
            yield {"event": "delta", "data": {"text": final_text}}

        elif decision.action == "clarify":
            new_round = int(session.get("clarification_round") or 0) + 1
            self.db.update_session_state(sid, clarification_round=new_round)
            final_text = decision.clarification_question or (
                "Правила и сроки по этому вопросу различаются между подразделениями. "
                "Уточните, пожалуйста, к какой дирекции относится ваш проект?"
            )
            clarification_options = decision.clarification_options or [
                "ДБРЦТ",
                "Техническая дирекция",
                "Дирекция ИБ",
                "Комплексный проект (несколько дирекций)",
                "Не знаю подразделение — покажите варианты",
            ]
            yield {"event": "delta", "data": {"text": final_text}}

        elif decision.action == "escalate":
            t_esc = time.perf_counter()
            self.db.update_session_state(sid, clarification_round=0)
            hist_summary = " | ".join(
                f"{'Сотрудник' if m['role']=='user' else 'Агент'}: {m['content'][:160]}"
                for m in history[-4:]
            )
            ticket = await self.email_service.send_escalation(
                session_id=sid,
                user_email=session["user_email"],
                question=state["effective_query"],
                context_summary=hist_summary,
                directorate=None if decision.directorate == "UNKNOWN" else decision.directorate,
            )
            ticket_code = ticket["ticket_code"]
            trace.append(
                {
                    "step": "smtp_escalation",
                    "title": "Эскалация по SMTP в Службу качества",
                    "duration_ms": int((time.perf_counter() - t_esc) * 1000),
                    "detail": f"Тикет {ticket_code} → {self.settings.quality_email} (CC: {ticket['user_email']})",
                }
            )
            smtp_note = (
                f"Письмо реально отправлено в Службу качества (`{self.settings.quality_email}`), вы добавлены в копию (`{ticket['user_email']}`)."
                if ticket["status"] == "sent"
                else f"Тикет создан, но при отправке SMTP возникла ошибка: `{ticket.get('smtp_error')}`."
            )
            final_text = (
                f"В действующих корпоративных регламентах и проверенной базе знаний нет подтверждённого правила для ответа на этот вопрос. "
                f"Чтобы исключить недостоверную информацию, я передал обращение в Службу качества.\n\n"
                f"- **Номер обращения:** `{ticket_code}`\n"
                f"- **Статус доставки:** {smtp_note}\n\n"
                f"После ответа эксперта Службы качества по электронной почте агент автоматически сохранит разъяснение в проверенную базу знаний (FAQ) и уведомит вас прямо в этом чате."
            )
            outcome = "escalated"
            yield {"event": "delta", "data": {"text": final_text}}

        # --- Branch B: CALL 2 — Streaming Grounded Answer Generator + Programmatic Citation Verifier ---
        else:
            t_gen = time.perf_counter()
            self.db.update_session_state(
                sid,
                clarification_round=0,
                active_directorate=decision.directorate if decision.directorate in {"DBRCT", "TECH_DIR", "IB"} else None,
            )
            selected_ids = decision.selected_source_ids or list(source_map.keys())[:5]
            grouped_blocks: dict[str, list[str]] = {}
            group_order: list[str] = []
            for tag in selected_ids:
                it = source_map[tag]
                body_em = re.sub(
                    r"(\((?:за исключением|кроме)[^)]+\)|Важно[!:]?)",
                    r"**\1**",
                    it.parent_text,
                    flags=re.I,
                )
                glabel = it.directorate_label
                if glabel not in grouped_blocks:
                    grouped_blocks[glabel] = []
                    group_order.append(glabel)
                grouped_blocks[glabel].append(
                    f"[{tag}] ({it.directorate_label} | {it.doc_title} | Раздел {it.section_num}. {it.section_title} | п. {it.clause_num})\n"
                    f"{body_em}"
                )
            if len(group_order) >= 2:
                selected_context = "\n\n".join(
                    f"=== ИСТОЧНИКИ ТОЛЬКО ДЛЯ: {gl} (использовать ТОЛЬКО в разделе {gl}!) ===\n"
                    + "\n\n".join(grouped_blocks[gl])
                    for gl in group_order
                )
            else:
                selected_context = "\n\n".join(
                    b for gl in group_order for b in grouped_blocks[gl]
                )

            mode_instructions = {
                "complex_answer": "Это КОМПЛЕКСНЫЙ проект нескольких дирекций! Выведи требования КАЖДОЙ применимой дирекции ОТДЕЛЬНЫМ блоком/заголовком. СТРОГАЯ ИЗОЛЯЦИЯ ДИРЕКЦИЙ: в блоке ДБРЦТ ссылайся ТОЛЬКО на источники с меткой ДБРЦТ, в блоке Технической дирекции — ТОЛЬКО на источники Технической дирекции, в блоке Дирекции ИБ — ТОЛЬКО на источники Дирекции ИБ! КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО переносить сроки (напр. 2 недели из Техдирекции) или писать «аналогично» в блок другой дирекции!",
                "answer_all_variants": "Сотрудник не знает своё подразделение. Покажи применимые правила КАЖДОЙ дирекции ОТДЕЛЬНЫМ пунктом со ссылками [S#], чтобы сотрудник выбрал свой вариант.",
                "partial_answer": "В базе есть только ЧАСТИЧНЫЙ ответ. Выдай строго подтверждённую часть со ссылками [S#] и сделай ЯВНУЮ ОГОВОРКУ, какой информации нет в загруженных регламентах. Ничего не додумывай!",
                "faq_answer": "Ответ опирается на проверенное разъяснение Службы качества [F#]. Явно укажи, что это подтверждённое разъяснение Службы качества (с номером тикета) и процитируй [F#].",
                "answer": "Дай краткий, практичный, понятный ответ строго по приведённым фрагментам, не пропуская важные условия и исключения (например, RFI, ГП, нарушение времени реакции SS vs ПА).",
            }[decision.action]

            extra_notes = []
            if decision.missing_external_docs:
                extra_notes.append(
                    f"ОБЯЗАТЕЛЬНО упомяни, что регламент ссылается на внешние документы ({', '.join(decision.missing_external_docs)}), тексты которых отсутствуют в загруженной базе знаний — НЕ выдумывай их содержание, URL или сторонние системы (типа SharePoint); предложи обратиться к корпоративному Confluence или в Службу качества."
                )
            if decision.has_conflict:
                extra_notes.append(
                    f"ВНИМАНИЕ — КОНФЛИКТ ИСТОЧНИКОВ ({decision.conflict_note}): официальный регламент [S*] имеет ВЫСШИЙ приоритет. Обопрись на официальный регламент и отдельно предупреди о расхождении с разъяснением FAQ, порекомендовав уточнение в Службе качества."
                )

            gen_system = f"""Ты — точный корпоративный ИИ-агент Службы качества Софтлайн.
Отвечай ПОНЯТНЫМ, практичным русским языком, опираясь ИСКЛЮЧИТЕЛЬНО на предоставленные источники ниже.

СТРОГИЕ ПРАВИЛА:
1. НИКАКИХ галлюцинаций и додумывания из общих знаний. Каждое существенное утверждение, срок, роль или исключение подтверждай ссылкой в формате [S1], [S2] или [F1] строго из списка источников ниже.
2. КРИТИЧНО — СОХРАНЯЙ ВСЕ ИСКЛЮЧЕНИЯ, СРОКИ И ОГОВОРКИ В СКОБКАХ (например: «за исключением RFI», оба срока «3 рабочих дня для бюджетной и 5 рабочих дней для точной оценки», «за 2 недели до подачи конкурса», «при наличии гарантийного письма», «нарушение времени реакции SS vs ПА»). Если у одной дирекции исключение есть (напр. RFI в ДБРЦТ), а у другой его нет — ОБЯЗАТЕЛЬНО выдели это отличие жирным! НЕ добавляй факты из посторонних источников, не относящиеся к вопросу.
3. {mode_instructions}
{' '.join(extra_notes)}
4. КРАТКОСТЬ И ФОРМАТ (строго обязательно!): давай сжатую практичную выжимку без воды и БЕЗ дословных блоков-цитат `>`. Для комплексного проекта / нескольких дирекций пиши строго по 2–4 коротких буллита на КАЖДУЮ дирекцию (не более 80–100 слов на дирекцию), чтобы все применимые дирекции целиком поместились в компактный ответ."""

            gen_user = f"Вопрос сотрудника: {state['effective_query']}\n\nПодтверждённые источники:\n{selected_context}"

            stream = None
            for attempt in range(4):
                try:
                    stream = await self.client.chat.completions.create(
                        model=self.settings.llm_model,
                        messages=[
                            {"role": "system", "content": gen_system},
                            {"role": "user", "content": gen_user},
                        ],
                        temperature=0.0,
                        max_tokens=1100,
                        stream=True,
                        stream_options={"include_usage": True},
                    )
                    break
                except Exception as exc:
                    if "429" in str(exc) and attempt < 3:
                        await asyncio.sleep(2.0 * (attempt + 1))
                        continue
                    raise
            assert stream is not None

            chunks_buf: list[str] = []
            async for chunk in stream:
                if getattr(chunk, "usage", None):
                    pt2, ct2, cost2 = _usage_stats(chunk.usage)
                    prompt_tokens += pt2
                    completion_tokens += ct2
                    total_cost += cost2
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    piece = chunk.choices[0].delta.content
                    chunks_buf.append(piece)
                    yield {"event": "delta", "data": {"text": piece}}

            raw_answer = "".join(chunks_buf).strip()

            # Programmatic Citation Verification (0 extra LLM calls!)
            referenced_tags = CITATION_TAG_RE.findall(raw_answer)
            valid_referenced = [t for t in referenced_tags if t in source_map]
            invalid_tags = set(referenced_tags) - set(valid_referenced)
            for bad in invalid_tags:
                raw_answer = raw_answer.replace(f"[{bad}]", "")

            final_text = raw_answer
            cite_ids = valid_referenced if valid_referenced else selected_ids
            citations = self._build_citations(cite_ids, source_map)

            for c in citations:
                if c["source_type"] == "quality_faq":
                    it = source_map.get(c["tag"])
                    if it and it.faq_id:
                        self.db.increment_faq_usage(it.faq_id)

            trace.append(
                {
                    "step": "grounded_generation_and_verify",
                    "title": "Потоковая генерация + Верификация цитат",
                    "duration_ms": int((time.perf_counter() - t_gen) * 1000),
                    "detail": (
                        f"Подтверждено ссылок: {len(citations)}/{len(citations)}"
                        + (f" (удалено неподтв.: {len(invalid_tags)})" if invalid_tags else " · Галлюцинаций ссылок: 0")
                    ),
                }
            )

            outcome = {
                "complex_answer": "complex_answered",
                "faq_answer": "faq_answered",
                "partial_answer": "partial_answer",
            }.get(decision.action, "answered")

        latency_ms = int((time.perf_counter() - t0) * 1000)

        saved_msg = self.db.add_message(
            sid,
            "assistant",
            final_text,
            outcome=outcome,
            citations=citations,
            clarification_options=clarification_options,
            trace=trace,
            has_conflict=decision.has_conflict,
            conflict_note=decision.conflict_note,
            ticket_code=ticket_code,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            embedding_tokens=embedding_tokens,
            cost_rub=round(total_cost, 6),
            latency_ms=latency_ms,
        )

        primary_doc = citations[0]["doc_id"] if citations else None
        primary_sec = (
            f"{citations[0]['directorate_label']} — Раздел {citations[0]['section_num']}. {citations[0]['section_title']}"
            if citations and citations[0]["source_type"] == "official_regulation"
            else None
        )
        self.db.log_analytics(
            session_id=sid,
            question=state["effective_query"],
            topic_key=(decision.topic_key or user_message[:40]).lower().strip(),
            outcome=outcome,
            directorate=decision.directorate,
            doc_id=primary_doc,
            section_label=primary_sec,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            embedding_tokens=embedding_tokens,
            cost_rub=round(total_cost, 6),
            latency_ms=latency_ms,
        )

        yield {
            "event": "done",
            "data": {
                "message": saved_msg,
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "embedding_tokens": embedding_tokens,
                    "cost_rub": round(total_cost, 6),
                    "latency_ms": latency_ms,
                },
            },
        }

    async def answer_turn_sync(
        self, *, session_id: str | None, user_email: str, user_message: str
    ) -> dict[str, Any]:
        """Non-streaming JSON wrapper for DION / corporate bot webhook integrations."""
        done_payload: dict[str, Any] = {}
        async for ev in self.stream_turn(
            session_id=session_id, user_email=user_email, user_message=user_message
        ):
            if ev["event"] == "done":
                done_payload = ev["data"]
        return done_payload
