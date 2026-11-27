"""LangGraph Agentic RAG orchestrator with strictly MAX 2 LLM calls per user turn.

Graph flow:
  1. `retrieve`  — Glossary expansion + Qdrant Dense + BM25 + RRF over Regulations (prio 1) & Quality FAQ (prio 2)
  2. `analyze`   — Single Structured JSON LLM call: Intent Router + Ambiguity/Directorate + Conflict/Coverage Analyzer
  3. Conditional branch:
       - off_topic / out_of_scope / find_doc / clarify / escalate -> 1 LLM call total!
       - answer / complex_answer / partial_answer / answer_all_variants / faq_answer ->
         `generate` (Call 2: Streaming Grounded Generator + Programmatic Citation Verifier)
"""

from __future__ import annotations

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


class RouteDecision(BaseModel):
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
    directorate: str = Field(
        default="UNKNOWN",
        description="DBRCT | TECH_DIR | IB | MULTI | ALL | UNKNOWN",
    )
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

    prompt_tokens: int
    completion_tokens: int
    embedding_tokens: int
    cost_rub: float


def _detect_explicit_directorate(text: str) -> str | None:
    """Detect if the current message explicitly specifies a single directorate or complex project."""
    low = text.lower()
    has_dbr = bool(re.search(r"\b(дбрцт|дбр|бизнес[\s-]решен\w*)\b", low))
    has_tech = bool(re.search(r"\b(техдирекц\w*|техническ\w*\s+дирекц\w*|тд)\b", low))
    has_ib = bool(re.search(r"\b(дирекц\w*\s+иб|диб|уиб|инфобез\w*|информационн\w*\s+безопасност\w*|\bиб\b)\b", low))
    has_multi = bool(re.search(r"комплексн|нескольк\w*\s+дирекц|все\s+вариант|не\s+знаю", low))

    count = int(has_dbr) + int(has_tech) + int(has_ib)
    if has_multi or count >= 2:
        return "MULTI"
    if has_dbr:
        return "DBRCT"
    if has_tech:
        return "TECH_DIR"
    if has_ib:
        return "IB"
    return None


def _build_effective_query(user_message: str, history: list[dict[str, Any]]) -> str:
    """If the user replied to a clarifying question with a short answer, fuse with the prior user question."""
    msg = user_message.strip()
    if not history:
        return msg
    last_assistant = next((m for m in reversed(history) if m["role"] == "assistant"), None)
    last_user = next((m for m in reversed(history) if m["role"] == "user"), None)
    if last_assistant and last_assistant.get("outcome") == "clarify" and last_user:
        # Combine original question + user's clarification
        return f"{last_user['content']} (Уточнение сотрудника: {msg})"
    if len(msg.split()) <= 4 and last_user:
        return f"{last_user['content']} {msg}"
    return msg


def _format_sources_for_prompt(
    reg_items: list[RetrievedItem], faq_items: list[RetrievedItem]
) -> tuple[str, dict[str, RetrievedItem]]:
    source_map: dict[str, RetrievedItem] = {}
    blocks: list[str] = []

    if reg_items:
        blocks.append("=== ОФИЦИАЛЬНЫЕ РЕГЛАМЕНТЫ (Приоритет 1 — утверждённые документы) ===")
        for idx, item in enumerate(reg_items, 1):
            sid = f"S{idx}"
            source_map[sid] = item
            ext = f" | Упомянутые внешние док-ты: {', '.join(item.external_doc_refs)}" if item.external_doc_refs else ""
            blocks.append(
                f"[{sid}] ({item.directorate_label} | {item.doc_title} | Раздел {item.section_num}. {item.section_title} | п. {item.clause_num} | rel={item.score}{ext})\n"
                f"{item.parent_text}"
            )

    if faq_items:
        blocks.append("\n=== ПРОВЕРЕННЫЕ РАЗЪЯСНЕНИЯ СЛУЖБЫ КАЧЕСТВА / FAQ (Приоритет 2 — дополняют регламент, но не отменяют его) ===")
        for idx, item in enumerate(faq_items, 1):
            fid = f"F{idx}"
            source_map[fid] = item
            blocks.append(f"[{fid}] (Тикет {item.ticket_code} | Эксперт: {item.verified_by} | rel={item.score})\n{item.parent_text}")

    return "\n\n".join(blocks) if blocks else "(Подходящих фрагментов в базе знаний не найдено)", source_map


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
        effective_query = _build_effective_query(state["user_message"], state.get("history") or [])
        explicit_dir = _detect_explicit_directorate(effective_query)
        active_dir = state.get("active_directorate")

        dir_filter: str | None = None
        if explicit_dir in {"DBRCT", "TECH_DIR", "IB"}:
            dir_filter = explicit_dir
        elif explicit_dir == "MULTI":
            dir_filter = None
        elif active_dir in {"DBRCT", "TECH_DIR", "IB"} and state.get("clarification_round", 0) > 0:
            dir_filter = active_dir

        reg_items, faq_items, emb_tokens, emb_cost, matched_glossary = self.retriever.search(
            effective_query,
            directorate_filter=dir_filter,
            top_k_regs=9,
            top_k_faq=3,
        )
        _, source_map = _format_sources_for_prompt(reg_items, faq_items)
        return {
            "effective_query": effective_query,
            "directorate_filter": dir_filter or explicit_dir,
            "matched_glossary": matched_glossary,
            "reg_items": reg_items,
            "faq_items": faq_items,
            "source_map": source_map,
            "embedding_tokens": emb_tokens,
            "cost_rub": emb_cost,
        }

    async def _node_analyze(self, state: AgentState) -> dict[str, Any]:
        """CALL 1: Unified Intent Router + Ambiguity/Directorate/Conflict/Coverage Analyzer (JSON)."""
        reg_items = state.get("reg_items") or []
        faq_items = state.get("faq_items") or []
        sources_text, _ = _format_sources_for_prompt(reg_items, faq_items)
        docs_list = self.db.list_indexed_docs()
        docs_summary = ", ".join(f"«{d['doc_title']}» ({d['directorate_label']})" for d in docs_list)

        history_lines = []
        for m in (state.get("history") or [])[-6:]:
            role_lbl = "Сотрудник" if m["role"] == "user" else "Агент"
            history_lines.append(f"{role_lbl}: {m['content'][:350]}")
        history_block = "\n".join(history_lines) if history_lines else "(начало диалога)"

        clar_round = int(state.get("clarification_round") or 0)
        max_rounds = self.settings.max_clarification_rounds

        system_prompt = f"""Ты — ядро принятия решений корпоративного ИИ-агента Службы качества Softline.
В базе загружены действующие регламенты: {docs_summary}.
Глоссарий терминов: {glossary_prompt_block(state.get('matched_glossary') or [])}.

Твоя задача — за ОДИН шаг проанализировать вопрос сотрудника, историю диалога и найденные фрагменты [S1..S9] и [F1..F3], и вернуть СТРОГО JSON (без пояснений вокруг).

ЖЁСТКИЕ ПРАВИЛА ПРИНЯТИЯ РЕШЕНИЙ:
1. "off_topic": запрос на посторонние задачи общего ИИ (сделай follow-up встречи, курс доллара, напиши код, переведи текст, погода, анекдот). Заполни short_response вежливым отказом.
2. "out_of_scope": рабочий вопрос сотрудника, но ЯВНО вне области регламентов проектной/пресейловой деятельности (например: размер личной премии, зарплата, маржа конкретной сделки, график отпуска, личные HR-вопросы). Заполни short_response: объясни, что это вне базы регламентов, и направь к непосредственному руководителю / АМ / HR. НЕ отправляй такое в Службу качества!
3. "find_doc": пользователь просит именно найти/дать название регламента, а не пересказывать его содержание. Заполни selected_source_ids и short_response со списком подходящих регламентов.
4. "faq_answer": если в официальных регламентах [S*] ответа нет, НО есть подходящее проверенное разъяснение Службы качества [F*] — выбери "faq_answer" и укажи [F*] в selected_source_ids!
5. "clarify": выбери ТОЛЬКО если:
   - вопрос относится к регламентам,
   - сотрудник НЕ указал подразделение (и НЕ указал «комплексный проект» / «не знаю подразделение»),
   - в найденных регламентах [S*] правила/сроки/роли РЕАЛЬНО ОТЛИЧАЮТСЯ между дирекциями (ДБРЦТ, Техническая дирекция, Дирекция ИБ),
   - и текущий раунд уточнений ({clar_round}) < {max_rounds}.
   Тогда сформулируй конкретный clarification_question и 3–5 коротких кнопок в clarification_options (обязательно включи варианты применимых дирекций + «Комплексный проект (несколько дирекций)» + «Не знаю подразделение — покажите варианты»).
   ВАЖНО: если вопрос касается ТОЛЬКО одной дирекции или пользователь уже уточнил дирекцию — НЕ уточняй повторно, отвечай сразу!
6. "complex_answer": если сотрудник указал несколько дирекций / комплексный проект (например, ИБ + Техдирекция или ДБРЦТ + Техдирекция) — выбери "complex_answer" и включи в selected_source_ids источники КАЖДОЙ применимой дирекции!
7. "answer_all_variants": если сотрудник ответил, что не знает подразделение — покажи варианты по каждой применимой дирекции отдельно.
8. "answer" или "partial_answer": если в [S*] (и/или [F*]) есть прямой подтверждённый ответ.
   - Если регламент ссылается на внешний документ, которого нет в базе (например, «регламент подбора субподрядчика», «инструкция по созданию карточки проекта», «матрица документооборота») — укажи его в missing_external_docs и используй "partial_answer" или "answer".
   - ПРИОРИТЕТ: Официальный регламент [S*] ВСЕГДА главнее разъяснения Службы качества [F*]. Если [F*] противоречит [S*], поставь has_conflict=true и опиши конфликт в conflict_note.
9. "escalate": выбери ОБЯЗАТЕЛЬНО если:
   - вопрос относится к корпоративным процессам/регламентам (например: компенсация такси в командировке/выходной, особые правила оплаты, процедура при болезни РП и т.п.), НО в [S*] и [F*] НЕТ подтверждённого ответа (нельзя выдумывать из общих знаний!),
   - ИЛИ исчерпан лимит уточнений ({clar_round} >= {max_rounds}) и ясности нет,
   - ИЛИ есть неразрешимое противоречие в одном и том же регламенте/контексте.
   ПРИОРИТЕТ ЗАКАЗЧИКА: лучше лишняя эскалация ("escalate"), чем выдуманное правило!

Верни JSON точно со следующими полями:
{{
  "action": "answer|complex_answer|partial_answer|answer_all_variants|faq_answer|find_doc|clarify|escalate|out_of_scope|off_topic",
  "directorate": "DBRCT|TECH_DIR|IB|MULTI|ALL|UNKNOWN",
  "selected_source_ids": ["S1", "S2"],
  "clarification_question": "",
  "clarification_options": [],
  "short_response": "",
  "missing_external_docs": [],
  "has_conflict": false,
  "conflict_note": "",
  "topic_key": "краткая тема из 2-4 слов на русском для аналитики",
  "reasoning": "1 предложение почему выбрано это действие"
}}"""

        user_prompt = (
            f"История диалога:\n{history_block}\n\n"
            f"Текущее сообщение сотрудника: {state['user_message']}\n"
            f"Полный поисковый контекст: {state['effective_query']}\n"
            f"Текущий раунд уточнений: {clar_round} из {max_rounds}\n\n"
            f"Найденные источники:\n{sources_text}"
        )

        resp = await self.client.chat.completions.create(
            model=self.settings.llm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=500,
        )
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

        # Enforce hard clarification cap (<= 3 rounds)
        if decision.action == "clarify" and clar_round >= max_rounds:
            decision.action = "escalate"
            decision.short_response = (
                "Мы уже провели максимальное количество уточнений, но однозначно определить применимое правило не удалось. "
                "Передаю ваш вопрос в Службу качества."
            )

        # Ensure selected_source_ids are valid keys from source_map
        source_map = state.get("source_map") or {}
        valid_ids = [sid for sid in decision.selected_source_ids if sid in source_map]
        if not valid_ids and decision.action in {"answer", "complex_answer", "partial_answer", "answer_all_variants"}:
            valid_ids = [f"S{i}" for i in range(1, min(6, len(reg_items) + 1)) if f"S{i}" in source_map]
        if not valid_ids and decision.action == "faq_answer":
            valid_ids = [f"F{i}" for i in range(1, min(3, len(faq_items) + 1)) if f"F{i}" in source_map]
        decision.selected_source_ids = valid_ids

        pt, ct, cost = _usage_stats(resp.usage)
        return {
            "decision": decision,
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
        """Run the full agent turn and yield SSE events: meta, delta, done."""
        t0 = time.perf_counter()
        session = self.db.get_or_create_session(
            session_id, user_email or self.settings.default_employee_email, title=user_message[:64]
        )
        sid = session["id"]
        history = self.db.get_messages(sid, limit=20)
        if not history:
            self.db.update_session_state(sid, title=user_message[:64])

        # Persist user message immediately
        self.db.add_message(sid, "user", user_message)
        yield {"event": "session", "data": {"session_id": sid, "title": session["title"]}}

        # Run LangGraph Steps 1 & 2 (Retrieve + Unified Analyze/Route)
        state: AgentState = await self.graph.ainvoke(
            {
                "session_id": sid,
                "user_email": session["user_email"],
                "user_message": user_message,
                "history": history,
                "clarification_round": int(session.get("clarification_round") or 0),
                "active_directorate": session.get("active_directorate"),
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "embedding_tokens": 0,
                "cost_rub": 0.0,
            }
        )

        decision: RouteDecision = state["decision"]
        source_map: dict[str, RetrievedItem] = state.get("source_map") or {}
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

        # --- Branch A: Single-call outcomes (NO Call 2 needed!) ---
        if decision.action == "off_topic":
            final_text = decision.short_response or (
                "Я специализированный ИИ-агент по корпоративным регламентам и процессам Softline. "
                "Задачи общего назначения (подготовка follow-up, курсы валют, сторонние тексты) не входят в мою зону ответственности. "
                "Задайте, пожалуйста, вопрос по проектным или пресейловым регламентам."
            )
            yield {"event": "delta", "data": {"text": final_text}}

        elif decision.action == "out_of_scope":
            final_text = decision.short_response or (
                "Этот вопрос выходит за рамки корпоративных регламентов проектной и пресейловой деятельности, загруженных в базу знаний. "
                "По вопросам личного вознаграждения, премирования или условий конкретной сделки рекомендую обратиться к вашему непосредственному руководителю или аккаунт-менеджеру."
            )
            yield {"event": "delta", "data": {"text": final_text}}

        elif decision.action == "find_doc":
            used_ids = decision.selected_source_ids or list(source_map.keys())[:4]
            citations = self._build_citations(used_ids, source_map)
            if decision.short_response:
                final_text = decision.short_response
            else:
                unique_docs = []
                seen_d = set()
                for c in citations:
                    if c["doc_id"] not in seen_d:
                        seen_d.add(c["doc_id"])
                        unique_docs.append(f"- **{c['doc_title']}** ({c['directorate_label']}, напр. Раздел {c['section_num']} / п. {c['clause_num']}) [{c['tag']}]")
                final_text = "По вашему запросу подходят следующие действующие регламенты:\n\n" + "\n".join(unique_docs)
            outcome = "answered"
            self.db.update_session_state(sid, clarification_round=0)
            yield {"event": "delta", "data": {"text": final_text}}

        elif decision.action == "clarify":
            new_round = int(session.get("clarification_round") or 0) + 1
            self.db.update_session_state(sid, clarification_round=new_round)
            final_text = decision.clarification_question or (
                "Правила и сроки по этому вопросу различаются в зависимости от подразделения. "
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
            smtp_note = (
                f"Письмо реально отправлено в Службу качества (`{self.settings.quality_email}`), вы добавлены в копию (`{ticket['user_email']}`)."
                if ticket["status"] == "sent"
                else f"Тикет создан, но при отправке SMTP возникла ошибка: `{ticket.get('smtp_error')}`."
            )
            final_text = (
                f"В действующих корпоративных регламентах и проверенной базе знаний нет подтверждённого правила для ответа на этот вопрос. "
                f"Чтобы не давать недостоверную информацию, я создал обращение в Службу качества.\n\n"
                f"- **Номер обращения:** `{ticket_code}`\n"
                f"- **Статус доставки:** {smtp_note}\n\n"
                f"Как только эксперт Службы качества ответит на письмо, агент автоматически сохранит разъяснение в проверенную базу знаний (FAQ) и примет его в работу для последующих запросов."
            )
            outcome = "escalated"
            yield {"event": "delta", "data": {"text": final_text}}

        # --- Branch B: CALL 2 — Streaming Grounded Answer Generator + Programmatic Citation Verifier ---
        else:
            self.db.update_session_state(
                sid,
                clarification_round=0,
                active_directorate=decision.directorate if decision.directorate in {"DBRCT", "TECH_DIR", "IB"} else None,
            )
            # Pick selected sources + ensure rich context
            selected_ids = decision.selected_source_ids or list(source_map.keys())[:6]
            selected_blocks = []
            for tag in selected_ids:
                it = source_map[tag]
                selected_blocks.append(
                    f"[{tag}] ({it.directorate_label} | {it.doc_title} | Раздел {it.section_num}. {it.section_title} | п. {it.clause_num})\n"
                    f"{it.parent_text}"
                )
            selected_context = "\n\n".join(selected_blocks)

            mode_instructions = {
                "complex_answer": "Это КОМПЛЕКСНЫЙ проект нескольких дирекций! Выведи требования КАЖДОЙ применимой дирекции ОТДЕЛЬНЫМ блоком/заголовком. НЕ усредняй сроки и НЕ смешивай условия разных подразделений!",
                "answer_all_variants": "Сотрудник не знает своё подразделение. Покажи применимые правила КАЖДОЙ дирекции ОТДЕЛЬНЫМ пунктом со ссылками [S#], чтобы сотрудник выбрал свой вариант.",
                "partial_answer": "В базе есть только ЧАСТИЧНЫЙ ответ. Выдай строго подтверждённую часть со ссылками [S#] и сделай ЯВНУЮ ОГОВОРКУ, какой информации нет в загруженных регламентах. Ничего не додумывай!",
                "faq_answer": "Ответ опирается на проверенное разъяснение Службы качества [F#]. Явно укажи, что это подтверждённое разъяснение Службы качества (с номером тикета) и процитируй [F#].",
                "answer": "Дай краткий, практичный, понятный ответ строго по приведённым фрагментам, не пропуская важные условия и исключения (например, RFI, ГП, нарушение времени реакции SS vs ПА).",
            }[decision.action]

            extra_notes = []
            if decision.missing_external_docs:
                extra_notes.append(
                    f"ОБЯЗАТЕЛЬНО упомяни, что регламент ссылается на внешние документы ({', '.join(decision.missing_external_docs)}), тексты которых отсутствуют в загруженной базе знаний — НЕ выдумывай их содержание и URL."
                )
            if decision.has_conflict:
                extra_notes.append(
                    f"ВНИМАНИЕ — КОНФЛИКТ ИСТОЧНИКОВ ({decision.conflict_note}): официальный регламент [S*] имеет ВЫСШИЙ приоритет. Обопрись на официальный регламент и отдельно предупреди о расхождении с разъяснением FAQ, порекомендовав уточнение в Службе качества."
                )

            gen_system = f"""Ты — точный корпоративный ИИ-агент Службы качества Softline.
Отвечай ПОНЯТНЫМ, практичным русским языком, опираясь ИСКЛЮЧИТЕЛЬНО на предоставленные источники ниже.

СТРОГИЕ ПРАВИЛА:
1. НИКАКИХ галлюцинаций и додумывания из общих знаний. Каждое существенное утверждение, срок, роль или исключение подтверждай ссылкой в формате [S1], [S2] или [F1] строго из списка источников ниже.
2. Сохраняй все оговорки, условия и исключения из пунктов (не упрощай до потери смысла).
3. {mode_instructions}
{' '.join(extra_notes)}
4. Формат: краткий структурированный Markdown (списки/жирный текст для сроков и ролей), без воды и длинных вступлений."""

            gen_user = (
                f"Вопрос сотрудника: {state['effective_query']}\n\n"
                f"Подтверждённые источники:\n{selected_context}"
            )

            stream = await self.client.chat.completions.create(
                model=self.settings.llm_model,
                messages=[
                    {"role": "system", "content": gen_system},
                    {"role": "user", "content": gen_user},
                ],
                temperature=0.1,
                max_tokens=900,
                stream=True,
                stream_options={"include_usage": True},
            )

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
            # Strip any hallucinated citation tag that does not exist in source_map
            invalid_tags = set(referenced_tags) - set(valid_referenced)
            for bad in invalid_tags:
                raw_answer = raw_answer.replace(f"[{bad}]", "")

            final_text = raw_answer
            cite_ids = valid_referenced if valid_referenced else selected_ids
            citations = self._build_citations(cite_ids, source_map)

            # Increment usage_count for any verified Quality FAQ used
            for c in citations:
                if c["source_type"] == "quality_faq":
                    it = source_map.get(c["tag"])
                    if it and it.faq_id:
                        self.db.increment_faq_usage(it.faq_id)

            outcome = {
                "complex_answer": "complex_answered",
                "faq_answer": "faq_answered",
                "partial_answer": "partial_answer",
            }.get(decision.action, "answered")

        latency_ms = int((time.perf_counter() - t0) * 1000)

        # Persist assistant message & analytics
        saved_msg = self.db.add_message(
            sid,
            "assistant",
            final_text,
            outcome=outcome,
            citations=citations,
            clarification_options=clarification_options,
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
