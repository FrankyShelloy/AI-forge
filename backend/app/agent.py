"""LangGraph Agentic RAG orchestrator with a 7-node conditional StateGraph and strictly MAX 2 LLM calls per turn.

Conditional StateGraph topology:
  START -> `guard` --(blocked)--> END (0 LLM calls: RBAC/anti-injection & general-AI fast filter)
                   --(proceed)--> `retrieve` (Glossary + Qdrant Dense + BM25 + RRF over Regulations prio 1 & FAQ prio 2)
                                      |
                                      v
                                  `analyze` (Call 1 JSON: Intent + Cross-Directorate Ambiguity + Chain-of-Verification + Conflict)
                                      |
          +---------------------------+--------------------+--------------------+
          |                           |                    |                    |
          v                           v                    v                    v
    `short_reply`                 `clarify`            `escalate`      `generate_and_verify`
  (off_topic / out_of_scope      (<=3 rounds +       (real SMTP +       (Call 2 SSE stream +
   / find_doc: 1 LLM call)        option chips)       CC employee)       tag verification)
          |                           |                    |                    |
          +---------------------------+--------------------+--------------------+
                                      |
                                      v
                                     END
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from typing import Any, AsyncGenerator, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, field_validator

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

    @field_validator(
        "missing_facts",
        "directorate",
        "clarification_question",
        "short_response",
        "conflict_note",
        "topic_key",
        "reasoning",
        mode="before",
    )
    @classmethod
    def _coerce_str(cls, v: Any) -> str:
        if v is None:
            return ""
        if isinstance(v, list):
            return "; ".join(str(x) for x in v if x is not None)
        return str(v)

    @field_validator(
        "selected_source_ids",
        "clarification_options",
        "missing_external_docs",
        mode="before",
    )
    @classmethod
    def _coerce_list(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            return [v] if v.strip() else []
        if isinstance(v, list):
            return [str(x) for x in v if x is not None]
        return []


class AgentState(TypedDict, total=False):
    turn_id: str
    session_id: str
    user_email: str
    user_message: str
    history: list[dict[str, Any]]
    clarification_round: int
    active_directorate: str | None

    guard_blocked: bool
    effective_query: str
    directorate_filter: str | None
    matched_glossary: list[str]
    reg_items: list[RetrievedItem]
    faq_items: list[RetrievedItem]
    source_map: dict[str, RetrievedItem]
    decision: RouteDecision
    trace: list[dict[str, Any]]

    final_text: str
    outcome: str
    citations: list[dict[str, Any]]
    clarification_options: list[str]
    ticket_code: str | None

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
    is_multi = (
        bool(re.search(r"комплексн|нескольк\w*\s+дирекц|все\s+вариант|покаж\w*\s+вариант|не\s+знаю|для\s+всех", low))
        or len(dirs) >= 2
    )
    return dirs, is_multi


def _build_queries(user_message: str, history: list[dict[str, Any]]) -> tuple[str, str]:
    """Return (clean_search_query, full_effective_query), walking back across multi-turn clarifications."""
    msg = user_message.strip()
    if not history:
        return msg, msg
    last_assistant = next((m for m in reversed(history) if m["role"] == "assistant"), None)
    last_user = next((m for m in reversed(history) if m["role"] == "user"), None)
    dirs_in_msg, is_multi_msg = _detect_mentioned_directorates(msg)
    is_short_dir_reply = ("?" not in msg) and len(msg.split()) <= 10 and (bool(dirs_in_msg) or is_multi_msg)

    if last_user and ((last_assistant and last_assistant.get("outcome") == "clarify") or is_short_dir_reply):
        # Walk backward past all consecutive clarification turns to recover the original substantive question
        idx = len(history) - 1
        orig_q = last_user["content"].strip()
        clar_notes: list[str] = []
        while idx >= 0:
            m = history[idx]
            if m["role"] == "assistant" and m.get("outcome") == "clarify":
                idx -= 1
                if idx >= 0 and history[idx]["role"] == "user":
                    prev_u = history[idx]["content"].strip()
                    # Check if there is an even earlier clarify before prev_u
                    earlier_clarify = (
                        idx - 1 >= 0
                        and history[idx - 1]["role"] == "assistant"
                        and history[idx - 1].get("outcome") == "clarify"
                    )
                    if earlier_clarify:
                        clar_notes.append(prev_u)
                        idx -= 1
                        continue
                    orig_q = prev_u
                    break
            else:
                idx -= 1
        clar_notes.reverse()
        clar_notes.append(msg)
        return orig_q, f"{orig_q} (Уточнения сотрудника: {'; '.join(clar_notes)})"

    if (
        len(msg.split()) <= 4
        and last_user
        and (not last_assistant or last_assistant.get("outcome") not in {"off_topic", "out_of_scope", "escalated"})
    ):
        return last_user["content"].strip(), f"{last_user['content']} {msg}"
    return msg, msg


def _format_sources_for_prompt(
    reg_items: list[RetrievedItem], faq_items: list[RetrievedItem], max_chars_per_item: int = 580
) -> tuple[str, dict[str, RetrievedItem]]:
    source_map: dict[str, RetrievedItem] = {}
    blocks: list[str] = []

    if reg_items:
        blocks.append("=== ОФИЦИАЛЬНЫЕ РЕГЛАМЕНТЫ (Приоритет 1 — высший приоритет) ===")
        for idx, item in enumerate(reg_items, 1):
            sid = f"S{idx}"
            source_map[sid] = item
            ext = f" | Внешние док-ты: {', '.join(item.external_doc_refs)}" if item.external_doc_refs else ""
            body = item.parent_text[:max_chars_per_item] + ("..." if len(item.parent_text) > max_chars_per_item else "")
            blocks.append(
                f"[{sid}] ({item.directorate_label} | {item.doc_title} | Р.{item.section_num} п.{item.clause_num} | rel={item.score}{ext})\n{body}"
            )

    if faq_items:
        blocks.append("\n=== FAQ СЛУЖБЫ КАЧЕСТВА (Приоритет 2 — дополняет регламент; при противоречии регламент [S*] главнее!) ===")
        for idx, item in enumerate(faq_items, 1):
            fid = f"F{idx}"
            source_map[fid] = item
            blocks.append(
                f"[{fid}] (Тикет {item.ticket_code} | {item.directorate_label} | {item.verified_by} | rel={item.score})\n{item.parent_text[:600]}"
            )

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
        self._stream_queues: dict[str, asyncio.Queue[dict[str, Any] | None]] = {}
        self.graph = self._build_graph()

    def _emit(self, state: AgentState, event: str, data: dict[str, Any]) -> None:
        q = self._stream_queues.get(state.get("turn_id") or "")
        if q is not None:
            q.put_nowait({"event": event, "data": data})

    def _build_graph(self):
        sg = StateGraph(AgentState)
        sg.add_node("guard", self._node_guard)
        sg.add_node("retrieve", self._node_retrieve)
        sg.add_node("analyze", self._node_analyze)
        sg.add_node("short_reply", self._node_short_reply)
        sg.add_node("clarify", self._node_clarify)
        sg.add_node("escalate", self._node_escalate)
        sg.add_node("generate_and_verify", self._node_generate_and_verify)

        sg.add_edge(START, "guard")
        sg.add_conditional_edges(
            "guard",
            self._route_after_guard,
            {"blocked": END, "proceed": "retrieve"},
        )
        sg.add_edge("retrieve", "analyze")
        sg.add_conditional_edges(
            "analyze",
            self._route_after_analyze,
            {
                "short_reply": "short_reply",
                "clarify": "clarify",
                "escalate": "escalate",
                "generate_and_verify": "generate_and_verify",
            },
        )
        sg.add_edge("short_reply", END)
        sg.add_edge("clarify", END)
        sg.add_edge("escalate", END)
        sg.add_edge("generate_and_verify", END)
        return sg.compile()

    @staticmethod
    def _route_after_guard(state: AgentState) -> Literal["blocked", "proceed"]:
        return "blocked" if state.get("guard_blocked") else "proceed"

    @staticmethod
    def _route_after_analyze(
        state: AgentState,
    ) -> Literal["short_reply", "clarify", "escalate", "generate_and_verify"]:
        action = state["decision"].action
        if action in {"off_topic", "out_of_scope", "find_doc"}:
            return "short_reply"
        if action == "clarify":
            return "clarify"
        if action == "escalate":
            return "escalate"
        return "generate_and_verify"

    # --- Node 1: Security & Scope Fast Guard (0 LLM calls) ---
    async def _node_guard(self, state: AgentState) -> dict[str, Any]:
        t0 = time.perf_counter()
        user_message = state["user_message"]

        if KB_TAMPER_RE.search(user_message):
            self.db.log_security_event(
                event_type="chat_kb_tamper_attempt",
                actor=state["user_email"],
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
            self._emit(state, "delta", {"text": refusal})
            return {
                "guard_blocked": True,
                "effective_query": user_message,
                "final_text": refusal,
                "outcome": "off_topic",
                "citations": [],
                "clarification_options": [],
                "ticket_code": None,
                "trace": trace,
                "decision": RouteDecision(action="off_topic", directorate="UNKNOWN", topic_key="защита модели прав"),
            }

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
            self._emit(state, "delta", {"text": refusal})
            return {
                "guard_blocked": True,
                "effective_query": user_message,
                "final_text": refusal,
                "outcome": "off_topic",
                "citations": [],
                "clarification_options": [],
                "ticket_code": None,
                "trace": trace,
                "decision": RouteDecision(action="off_topic", directorate="UNKNOWN", topic_key="посторонний запрос общего ии"),
            }

        return {"guard_blocked": False}

    # --- Node 2: Glossary Expansion + Hybrid Retrieval ---
    async def _node_retrieve(self, state: AgentState) -> dict[str, Any]:
        docs_meta = self.db.list_indexed_docs()
        total_chunks = sum(int(d["chunk_count"]) for d in docs_meta) or 611
        self._emit(
            state,
            "status",
            {
                "action": "retrieving",
                "directorate": "",
                "reasoning": f"Гибридный поиск по {total_chunks} пунктам регламентов ({len(docs_meta)} док.) и FAQ...",
            },
        )
        t0 = time.perf_counter()
        search_query, effective_query = _build_queries(state["user_message"], state.get("history") or [])
        dirs, is_multi = _detect_mentioned_directorates(effective_query)
        active_dir = state.get("active_directorate")

        dir_filter: str | None = None
        dirs_list: list[str] | None = None
        if len(dirs) >= 2:
            dirs_list = dirs
        elif is_multi:
            all_dirs = list(dict.fromkeys(["DBRCT", "TECH_DIR", "IB", *(d["directorate"] for d in docs_meta)]))
            dirs_list = all_dirs
        elif len(dirs) == 1:
            dir_filter = dirs[0]
        elif active_dir and active_dir not in {"MULTI", "ALL", "UNKNOWN"} and state.get("clarification_round", 0) > 0:
            dir_filter = active_dir

        reg_items, faq_items, emb_tokens, emb_cost, matched_glossary = await asyncio.to_thread(
            self.retriever.search,
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

    # --- Node 3: CALL 1 — Unified Intent Router + CoV + Ambiguity + Conflict Analyzer ---
    async def _node_analyze(self, state: AgentState) -> dict[str, Any]:
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
- "faq_answer": ТОЛЬКО когда в официальных регламентах [S*] НЕТ прямой нормы по предмету вопроса, но отвечает проверенный [F*] → direct_answer_exists=true, selected_source_ids=["F1"]. Если в [S*] норма по вопросу ЕСТЬ — НИКОГДА не выбирай "faq_answer", выбирай "answer"!
- "clarify": ОБЯЗАТЕЛЬНО если вопрос по регламентам, дирекция НЕ определена (и нет слов «комплексный»/«не знаю»), в [S*] ЕСТЬ прямые ответы по теме, но правила/сроки/исключения ОТЛИЧАЮТСЯ между дирекциями, и раунд ({clar_round}) < {max_rounds}. Задай краткий clarification_question и 4–5 кнопок clarification_options. НЕ выбирай "clarify", если дирекция уже указана ИЛИ прямого ответа по теме вопроса вообще нет в [S*] и [F*] (тогда сразу "escalate"!).
- "complex_answer": сотрудник указал ≥2 дирекций / комплексный проект → selected_source_ids из КАЖДОЙ применимой дирекции (сроки И условия/исключения).
- "answer_all_variants": сотрудник явно ответил «не знаю подразделение / покажите варианты» → показать правила каждой дирекции отдельно.
- "answer" / "partial_answer": есть прямой подтверждённый ответ в [S*]. Укажи missing_external_docs, если регламент ссылается на отсутствующий документ. ПРИОРИТЕТ И КОНФЛИКТЫ: официальный регламент [S*] ВСЕГДА главнее FAQ [F*] (и проверяй противоречия между самими пунктами). Если [F*] противоречит явному правилу/запрету/сроку в [S*] по той же теме — ОБЯЗАТЕЛЬНО выбери "answer", поставь has_conflict=true, опиши расхождение в conflict_note и включи в selected_source_ids И пункт регламента [S*], И конфликтующий [F1]!
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
                    max_tokens=460,
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
                action="escalate",
                direct_answer_exists=False,
                topic_key=state["user_message"][:40],
            )

        source_map = state.get("source_map") or {}
        valid_ids = [sid for sid in decision.selected_source_ids if sid in source_map]
        if not valid_ids and decision.action in {"answer", "complex_answer", "partial_answer", "answer_all_variants"}:
            valid_ids = [f"S{i}" for i in range(1, min(5, len(reg_items) + 1)) if f"S{i}" in source_map]
        if not valid_ids and decision.action == "faq_answer":
            valid_ids = [f"F{i}" for i in range(1, min(3, len(faq_items) + 1)) if f"F{i}" in source_map]
        decision.selected_source_ids = valid_ids

        # Conflict priority safeguard: Official Regulation [S*] ALWAYS outranks Quality FAQ [F*]
        if decision.has_conflict and reg_items:
            if decision.action == "faq_answer":
                decision.action = "answer"
            has_s = any(sid.startswith("S") for sid in decision.selected_source_ids)
            has_f = any(sid.startswith("F") for sid in decision.selected_source_ids)
            if not has_s and "S1" in source_map:
                decision.selected_source_ids.insert(0, "S1")
            if not has_f and "F1" in source_map:
                decision.selected_source_ids.append("F1")

        # Deterministic cross-directorate ambiguity & 3-round clarification circuit breaker (FR-07):
        # If the user still hasn't identified an indexed directorate (nor chosen 'комплексный'/'не знаю')
        # while retrieved/selected regulation norms span >=2 competing directorates:
        if (
            state.get("directorate_filter") is None
            and decision.action in {"answer", "complex_answer", "answer_all_variants", "clarify"}
        ):
            selected_dirs = {
                source_map[sid].directorate
                for sid in decision.selected_source_ids
                if sid.startswith("S") and sid in source_map
            }
            top_reg_score = reg_items[0].score if reg_items else 0.0
            competing_dirs = {
                it.directorate
                for it in reg_items
                if it.score >= max(0.45, top_reg_score - 0.18)
            }
            ambiguous_dirs = selected_dirs | competing_dirs

            if len(ambiguous_dirs) >= 2 and (decision.direct_answer_exists or clar_round > 0):
                if clar_round >= max_rounds:
                    decision.action = "escalate"
                    decision.reasoning = f"Достигнут лимит уточняющих вопросов ({max_rounds} итерации) без определения дирекции."
                else:
                    dir_labels_map: dict[str, str] = {
                        "DBRCT": "ДБРЦТ",
                        "TECH_DIR": "Техническая дирекция",
                        "IB": "Дирекция ИБ",
                    }
                    for ddoc in docs_list:
                        dir_labels_map.setdefault(ddoc["directorate"], ddoc["directorate_label"])
                    present_labels = [dir_labels_map[d] for d in dir_labels_map if d in ambiguous_dirs]
                    decision.action = "clarify"
                    decision.clarification_question = decision.clarification_question or (
                        f"Правила и сроки по этому вопросу различаются между подразделениями ({', '.join(present_labels)}). "
                        f"Пожалуйста, уточните применимую дирекцию (шаг уточнения {clar_round + 1} из {max_rounds}):"
                    )
                    decision.clarification_options = decision.clarification_options or [
                        *present_labels,
                        f"Комплексный проект ({' + '.join(present_labels[:2])})",
                        "Не знаю подразделение — покажите варианты",
                    ]

        # Hard circuit breaker if LLM chose clarify on round >= max_rounds for any reason
        if decision.action == "clarify" and clar_round >= max_rounds:
            decision.action = "escalate"
            decision.reasoning = f"Достигнут лимит уточняющих вопросов ({max_rounds} итерации), передача в Службу качества."

        # Deterministic multi-directorate guarantee for complex projects / "не знаю"
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
            merged_ids: list[str] = list(decision.selected_source_ids)
            for dcode, tags in by_dir.items():
                present = [t for t in merged_ids if source_map[t].directorate == dcode]
                for t in tags[:3]:
                    if t not in merged_ids and len(present) < 3:
                        merged_ids.append(t)
                        present.append(t)
            decision.selected_source_ids = merged_ids[:8]

        # Deterministic anti-hallucination guardrail: NEVER generate an answer without verified direct evidence
        if (
            decision.action in {"answer", "complex_answer", "answer_all_variants"}
            and not decision.direct_answer_exists
            and not decision.missing_external_docs
        ):
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
                "title": "LangGraph Router (Intent + CoV + Ambiguity + Conflict)",
                "duration_ms": ms,
                "detail": (
                    f"Узел → {self._route_after_analyze({'decision': decision})} ({decision.action})"
                    f" · Дирекция: {decision.directorate} · Источников: {len(decision.selected_source_ids)}"
                    + (" · Конфликт!" if decision.has_conflict else "")
                ),
            }
        )

        self._emit(
            state,
            "status",
            {
                "action": decision.action,
                "directorate": decision.directorate,
                "reasoning": decision.reasoning,
            },
        )

        return {
            "decision": decision,
            "trace": trace,
            "prompt_tokens": int(state.get("prompt_tokens") or 0) + pt,
            "completion_tokens": int(state.get("completion_tokens") or 0) + ct,
            "cost_rub": float(state.get("cost_rub") or 0.0) + cost,
        }

    # --- Node 4: Single-Call Short Reply (off_topic / out_of_scope / find_doc) ---
    async def _node_short_reply(self, state: AgentState) -> dict[str, Any]:
        decision = state["decision"]
        source_map = state.get("source_map") or {}
        citations: list[dict[str, Any]] = []
        outcome = decision.action

        if decision.action == "off_topic":
            final_text = decision.short_response or (
                "Я специализированный ИИ-агент по корпоративным регламентам и процессам Софтлайн. "
                "Посторонние задачи общего ИИ (подготовка follow-up, курсы валют, произвольные тексты) не входят в мою область работы. "
                "Задайте, пожалуйста, вопрос по проектным или пресейловым регламентам."
            )
        elif decision.action == "out_of_scope":
            final_text = decision.short_response or (
                "Этот вопрос выходит за рамки корпоративных регламентов проектной и пресейловой деятельности. "
                "По вопросам личного вознаграждения, премии или маржинальности конкретной сделки обратитесь, пожалуйста, к вашему непосредственному руководителю или аккаунт-менеджеру."
            )
        else:  # find_doc
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
            self.db.update_session_state(state["session_id"], clarification_round=0)

        self._emit(state, "delta", {"text": final_text})
        return {
            "final_text": final_text,
            "outcome": outcome,
            "citations": citations,
            "clarification_options": [],
            "ticket_code": None,
        }

    # --- Node 5: Clarifying Dialogue (<= 3 rounds) ---
    async def _node_clarify(self, state: AgentState) -> dict[str, Any]:
        decision = state["decision"]
        new_round = int(state.get("clarification_round") or 0) + 1
        self.db.update_session_state(state["session_id"], clarification_round=new_round)

        final_text = decision.clarification_question or (
            "Правила и сроки по этому вопросу различаются между подразделениями. "
            "Уточните, пожалуйста, к какой дирекции относится ваш проект?"
        )
        options = decision.clarification_options or [
            "ДБРЦТ",
            "Техническая дирекция",
            "Дирекция ИБ",
            "Комплексный проект (несколько дирекций)",
            "Не знаю подразделение — покажите варианты",
        ]
        self._emit(state, "delta", {"text": final_text})
        return {
            "final_text": final_text,
            "outcome": "clarify",
            "citations": [],
            "clarification_options": options,
            "ticket_code": None,
        }

    # --- Node 6: Real SMTP Escalation to Quality Service ---
    async def _node_escalate(self, state: AgentState) -> dict[str, Any]:
        t_esc = time.perf_counter()
        decision = state["decision"]
        sid = state["session_id"]
        history = state.get("history") or []
        self.db.update_session_state(sid, clarification_round=0)

        hist_summary = " | ".join(
            f"{'Сотрудник' if m['role']=='user' else 'Агент'}: {m['content'][:160]}"
            for m in history[-4:]
        )
        ticket = await self.email_service.send_escalation(
            session_id=sid,
            user_email=state["user_email"],
            question=state["effective_query"],
            context_summary=hist_summary,
            directorate=None if decision.directorate == "UNKNOWN" else decision.directorate,
        )
        ticket_code = ticket["ticket_code"]
        trace = list(state.get("trace") or [])
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
        reason_prefix = (
            f"За {self.settings.max_clarification_rounds} шага уточнений не удалось однозначно определить применимый регламент, поэтому обращение передано в Службу качества.\n\n"
            if int(state.get("clarification_round") or 0) >= self.settings.max_clarification_rounds
            else (
                "В действующих корпоративных регламентах и проверенной базе знаний нет подтверждённого правила для ответа на этот вопрос. "
                "Чтобы исключить недостоверную информацию, я передал обращение в Службу качества.\n\n"
            )
        )
        final_text = (
            f"{reason_prefix}"
            f"- **Номер обращения:** `{ticket_code}`\n"
            f"- **Статус доставки:** {smtp_note}\n\n"
            f"После ответа эксперта Службы качества по электронной почте агент автоматически сохранит разъяснение в проверенную базу знаний (FAQ) и уведомит вас прямо в этом чате."
        )
        self._emit(state, "delta", {"text": final_text})
        return {
            "final_text": final_text,
            "outcome": "escalated",
            "citations": [],
            "clarification_options": [],
            "ticket_code": ticket_code,
            "trace": trace,
        }

    # --- Node 7: CALL 2 — Streaming Grounded Answer Generator + Programmatic Citation Verifier ---
    async def _node_generate_and_verify(self, state: AgentState) -> dict[str, Any]:
        t_gen = time.perf_counter()
        decision = state["decision"]
        source_map = state.get("source_map") or {}
        sid = state["session_id"]

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
            glabel = "FAQ Службы качества (Приоритет 2)" if it.source_type == "quality_faq" else it.directorate_label
            if glabel not in grouped_blocks:
                grouped_blocks[glabel] = []
                group_order.append(glabel)
            if it.source_type == "quality_faq":
                grouped_blocks[glabel].append(
                    f"[{tag}] (РАЗЪЯСНЕНИЕ FAQ СЛУЖБЫ КАЧЕСТВА — Приоритет 2, уступает официальному регламенту! | Тикет {it.ticket_code} | {it.verified_by})\n"
                    f"{body_em}"
                )
            else:
                grouped_blocks[glabel].append(
                    f"[{tag}] (ОФИЦИАЛЬНЫЙ РЕГЛАМЕНТ — Приоритет 1 | {it.directorate_label} | {it.doc_title} | Раздел {it.section_num}. {it.section_title} | п. {it.clause_num})\n"
                    f"{body_em}"
                )

        if len(group_order) >= 2:
            selected_context = "\n\n".join(
                f"=== ИСТОЧНИКИ ДЛЯ БЛОКА: {gl} ===\n" + "\n\n".join(grouped_blocks[gl])
                for gl in group_order
            )
        else:
            selected_context = "\n\n".join(b for gl in group_order for b in grouped_blocks[gl])

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
                f"ВНИМАНИЕ — ОБНАРУЖЕН КОНФЛИКТ ИСТОЧНИКОВ ({decision.conflict_note}): официальный регламент [S*] имеет ВЫСШИЙ ПРИОРИТЕТ над разъяснением FAQ [F*]! Сформулируй правило СТРОГО по официальному регламенту [S*], а затем отдельным абзацем «⚠️ Внимание: расхождение источников» явно укажи, в чём запись FAQ [F*] противоречит регламенту, и порекомендуй уточнить актуальность разъяснения в Службе качества."
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

        prompt_tokens = int(state.get("prompt_tokens") or 0)
        completion_tokens = int(state.get("completion_tokens") or 0)
        total_cost = float(state.get("cost_rub") or 0.0)

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
                self._emit(state, "delta", {"text": piece})

        raw_answer = "".join(chunks_buf).strip()

        # Programmatic Citation Verification (0 extra LLM calls!)
        referenced_tags = CITATION_TAG_RE.findall(raw_answer)
        unique_ref_tags = list(dict.fromkeys(referenced_tags))
        valid_referenced = [t for t in unique_ref_tags if t in source_map]
        invalid_tags = [t for t in unique_ref_tags if t not in source_map]
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

        if unique_ref_tags:
            cite_detail = (
                f"Верифицировано цитат в тексте: {len(valid_referenced)}/{len(unique_ref_tags)}"
                + (f" (удалено неподтв.: {len(invalid_tags)})" if invalid_tags else " · Галлюцинаций ссылок: 0")
            )
        else:
            cite_detail = f"Привязано опорных источников: {len(citations)} · Галлюцинаций ссылок: 0"

        trace = list(state.get("trace") or [])
        trace.append(
            {
                "step": "grounded_generation_and_verify",
                "title": "Потоковая генерация + Верификация цитат",
                "duration_ms": int((time.perf_counter() - t_gen) * 1000),
                "detail": cite_detail,
            }
        )

        outcome = {
            "complex_answer": "complex_answered",
            "faq_answer": "faq_answered",
            "partial_answer": "partial_answer",
        }.get(decision.action, "answered")

        return {
            "final_text": final_text,
            "outcome": outcome,
            "citations": citations,
            "clarification_options": [],
            "ticket_code": None,
            "trace": trace,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost_rub": total_cost,
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
        client_id: str = "default",
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Run the full 7-node LangGraph turn and yield SSE events: session, status, delta, done."""
        t0 = time.perf_counter()
        session = self.db.get_or_create_session(
            session_id,
            user_email or self.settings.default_employee_email,
            title=user_message[:64],
            client_id=client_id,
        )
        sid = session["id"]
        history = self.db.get_messages(sid, limit=20)
        if not history:
            self.db.update_session_state(sid, title=user_message[:64])

        self.db.add_message(sid, "user", user_message)
        yield {"event": "session", "data": {"session_id": sid, "title": session["title"]}}

        turn_id = str(uuid.uuid4())
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._stream_queues[turn_id] = queue

        initial_state: AgentState = {
            "turn_id": turn_id,
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

        async def _execute_graph() -> AgentState:
            try:
                return await self.graph.ainvoke(initial_state)
            finally:
                await queue.put(None)

        graph_task = asyncio.create_task(_execute_graph())
        ttft_ms = 0
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                if item.get("event") == "delta" and ttft_ms == 0:
                    ttft_ms = int((time.perf_counter() - t0) * 1000)
                yield item
            state = await graph_task
        finally:
            self._stream_queues.pop(turn_id, None)

        decision: RouteDecision = state["decision"]
        final_text = state.get("final_text") or ""
        outcome = state.get("outcome") or decision.action
        citations = state.get("citations") or []
        clarification_options = state.get("clarification_options") or []
        ticket_code = state.get("ticket_code")
        trace = list(state.get("trace") or [])
        prompt_tokens = int(state.get("prompt_tokens") or 0)
        completion_tokens = int(state.get("completion_tokens") or 0)
        embedding_tokens = int(state.get("embedding_tokens") or 0)
        total_cost = float(state.get("cost_rub") or 0.0)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        if ttft_ms == 0:
            ttft_ms = latency_ms

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
            ttft_ms=ttft_ms,
        )

        primary_doc = citations[0]["doc_id"] if citations else None
        primary_sec = (
            f"{citations[0]['directorate_label']} — Раздел {citations[0]['section_num']}. {citations[0]['section_title']}"
            if citations and citations[0]["source_type"] == "official_regulation"
            else None
        )
        self.db.log_analytics(
            session_id=sid,
            question=state.get("effective_query") or user_message,
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
            has_conflict=decision.has_conflict,
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
                    "ttft_ms": ttft_ms,
                },
            },
        }

    async def answer_turn_sync(
        self,
        *,
        session_id: str | None,
        user_email: str,
        user_message: str,
        client_id: str = "default",
    ) -> dict[str, Any]:
        """Non-streaming JSON wrapper for DION / corporate bot webhook integrations."""
        done_payload: dict[str, Any] = {}
        async for ev in self.stream_turn(
            session_id=session_id,
            user_email=user_email,
            user_message=user_message,
            client_id=client_id,
        ):
            if ev["event"] == "done":
                done_payload = ev["data"]
        return done_payload
