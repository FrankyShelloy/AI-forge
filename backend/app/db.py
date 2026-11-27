"""SQLite (WAL mode) persistence for sessions, messages, tickets, verified FAQ, security audit, and full clauses."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    client_id TEXT NOT NULL DEFAULT 'default',
                    title TEXT NOT NULL,
                    user_email TEXT NOT NULL,
                    clarification_round INTEGER NOT NULL DEFAULT 0,
                    active_directorate TEXT DEFAULT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    outcome TEXT DEFAULT NULL,
                    citations_json TEXT DEFAULT '[]',
                    clarification_options_json TEXT DEFAULT '[]',
                    trace_json TEXT DEFAULT '[]',
                    has_conflict INTEGER DEFAULT 0,
                    conflict_note TEXT DEFAULT '',
                    ticket_code TEXT DEFAULT NULL,
                    prompt_tokens INTEGER DEFAULT 0,
                    completion_tokens INTEGER DEFAULT 0,
                    embedding_tokens INTEGER DEFAULT 0,
                    cost_rub REAL DEFAULT 0.0,
                    latency_ms INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS escalation_tickets (
                    id TEXT PRIMARY KEY,
                    ticket_code TEXT UNIQUE NOT NULL,
                    session_id TEXT NOT NULL,
                    user_email TEXT NOT NULL,
                    question TEXT NOT NULL,
                    context_summary TEXT DEFAULT '',
                    directorate TEXT DEFAULT NULL,
                    smtp_message_id TEXT DEFAULT NULL,
                    status TEXT NOT NULL DEFAULT 'sent',
                    expert_email TEXT DEFAULT NULL,
                    expert_reply_raw TEXT DEFAULT NULL,
                    expert_reply_clean TEXT DEFAULT NULL,
                    linked_doc_title TEXT DEFAULT NULL,
                    faq_id TEXT DEFAULT NULL,
                    created_at TEXT NOT NULL,
                    answered_at TEXT DEFAULT NULL
                );

                CREATE TABLE IF NOT EXISTS faq_entries (
                    id TEXT PRIMARY KEY,
                    ticket_code TEXT NOT NULL,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    directorate TEXT DEFAULT 'ALL',
                    linked_doc_title TEXT DEFAULT '',
                    verified_by TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    usage_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS analytics_events (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    topic_key TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    directorate TEXT DEFAULT NULL,
                    doc_id TEXT DEFAULT NULL,
                    section_label TEXT DEFAULT NULL,
                    prompt_tokens INTEGER DEFAULT 0,
                    completion_tokens INTEGER DEFAULT 0,
                    embedding_tokens INTEGER DEFAULT 0,
                    cost_rub REAL DEFAULT 0.0,
                    latency_ms INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS indexed_documents (
                    doc_id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    doc_title TEXT NOT NULL,
                    directorate TEXT NOT NULL,
                    directorate_label TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    chunk_count INTEGER NOT NULL,
                    indexed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS document_clauses (
                    chunk_id TEXT PRIMARY KEY,
                    doc_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    section_num TEXT NOT NULL,
                    section_title TEXT NOT NULL,
                    clause_num TEXT NOT NULL,
                    raw_text TEXT NOT NULL,
                    external_doc_refs_json TEXT DEFAULT '[]'
                );

                CREATE TABLE IF NOT EXISTS security_events (
                    id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    ticket_code TEXT DEFAULT NULL,
                    detail TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            # Safe additive migrations for existing DB files
            session_cols = {
                r["name"] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()
            }
            if "client_id" not in session_cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN client_id TEXT NOT NULL DEFAULT 'default'")

            existing_cols = {
                r["name"] for r in conn.execute("PRAGMA table_info(messages)").fetchall()
            }
            if "trace_json" not in existing_cols:
                conn.execute("ALTER TABLE messages ADD COLUMN trace_json TEXT DEFAULT '[]'")
            if "has_conflict" not in existing_cols:
                conn.execute("ALTER TABLE messages ADD COLUMN has_conflict INTEGER DEFAULT 0")
            if "conflict_note" not in existing_cols:
                conn.execute("ALTER TABLE messages ADD COLUMN conflict_note TEXT DEFAULT ''")

    # --- Sessions (Isolated 1-on-1 per client_id) ---
    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            return dict(row) if row else None

    def get_or_create_session(
        self,
        session_id: str | None,
        user_email: str,
        title: str = "Новый диалог",
        client_id: str = "default",
    ) -> dict[str, Any]:
        now = utcnow()
        cid = (client_id or "default").strip()[:64]
        with self._lock, self._connect() as conn:
            if session_id:
                row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
                if row:
                    owner = row["client_id"] or "default"
                    if cid == "internal" or owner in {"default", cid}:
                        new_owner = cid if (owner == "default" and cid != "internal") else owner
                        if (user_email and row["user_email"] != user_email) or new_owner != owner:
                            conn.execute(
                                "UPDATE sessions SET user_email = ?, client_id = ?, updated_at = ? WHERE id = ?",
                                (user_email or row["user_email"], new_owner, now, session_id),
                            )
                            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
                        return dict(row)
                    # Foreign client_id trying to reuse another user's session -> create isolated session
                    session_id = None
            sid = session_id or str(uuid.uuid4())
            conn.execute(
                "INSERT INTO sessions (id, client_id, title, user_email, clarification_round, created_at, updated_at) VALUES (?, ?, ?, ?, 0, ?, ?)",
                (sid, "default" if cid == "internal" else cid, title[:80], user_email, now, now),
            )
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
            return dict(row)

    def list_sessions(self, client_id: str = "default", limit: int = 30) -> list[dict[str, Any]]:
        cid = (client_id or "default").strip()[:64]
        with self._lock, self._connect() as conn:
            if cid == "internal":
                rows = conn.execute(
                    "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM sessions WHERE client_id = ? ORDER BY updated_at DESC LIMIT ?",
                    (cid, limit),
                ).fetchall()
            return [dict(r) for r in rows]

    def update_session_state(
        self,
        session_id: str,
        *,
        title: str | None = None,
        clarification_round: int | None = None,
        active_directorate: str | None = None,
        reset_directorate: bool = False,
    ) -> None:
        now = utcnow()
        with self._lock, self._connect() as conn:
            parts = ["updated_at = ?"]
            vals: list[Any] = [now]
            if title is not None:
                parts.append("title = ?")
                vals.append(title[:80])
            if clarification_round is not None:
                parts.append("clarification_round = ?")
                vals.append(clarification_round)
            if reset_directorate:
                parts.append("active_directorate = NULL")
            elif active_directorate is not None:
                parts.append("active_directorate = ?")
                vals.append(active_directorate)
            vals.append(session_id)
            conn.execute(f"UPDATE sessions SET {', '.join(parts)} WHERE id = ?", vals)

    def delete_session(self, session_id: str, client_id: str = "default") -> bool:
        cid = (client_id or "default").strip()[:64]
        with self._lock, self._connect() as conn:
            if cid == "internal":
                cur = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            else:
                cur = conn.execute(
                    "DELETE FROM sessions WHERE id = ? AND client_id = ?", (session_id, cid)
                )
            return cur.rowcount > 0

    # --- Messages ---
    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        outcome: str | None = None,
        citations: list[dict[str, Any]] | None = None,
        clarification_options: list[str] | None = None,
        trace: list[dict[str, Any]] | None = None,
        has_conflict: bool = False,
        conflict_note: str = "",
        ticket_code: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        embedding_tokens: int = 0,
        cost_rub: float = 0.0,
        latency_ms: int = 0,
    ) -> dict[str, Any]:
        mid = str(uuid.uuid4())
        now = utcnow()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO messages (
                    id, session_id, role, content, outcome, citations_json,
                    clarification_options_json, trace_json, has_conflict, conflict_note,
                    ticket_code, prompt_tokens, completion_tokens, embedding_tokens,
                    cost_rub, latency_ms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mid,
                    session_id,
                    role,
                    content,
                    outcome,
                    json.dumps(citations or [], ensure_ascii=False),
                    json.dumps(clarification_options or [], ensure_ascii=False),
                    json.dumps(trace or [], ensure_ascii=False),
                    int(bool(has_conflict)),
                    conflict_note or "",
                    ticket_code,
                    prompt_tokens,
                    completion_tokens,
                    embedding_tokens,
                    cost_rub,
                    latency_ms,
                    now,
                ),
            )
            conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
        return {
            "id": mid,
            "session_id": session_id,
            "role": role,
            "content": content,
            "outcome": outcome,
            "citations": citations or [],
            "clarification_options": clarification_options or [],
            "trace": trace or [],
            "has_conflict": bool(has_conflict),
            "conflict_note": conflict_note or "",
            "ticket_code": ticket_code,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "embedding_tokens": embedding_tokens,
            "cost_rub": cost_rub,
            "latency_ms": latency_ms,
            "created_at": now,
        }

    def get_messages(self, session_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["citations"] = json.loads(d.pop("citations_json") or "[]")
            d["clarification_options"] = json.loads(d.pop("clarification_options_json") or "[]")
            d["trace"] = json.loads(d.pop("trace_json") or "[]")
            d["has_conflict"] = bool(d.get("has_conflict"))
            out.append(d)
        return out

    # --- Escalation Tickets ---
    def create_ticket(
        self,
        session_id: str,
        user_email: str,
        question: str,
        context_summary: str,
        directorate: str | None,
        smtp_message_id: str | None,
        status: str = "sent",
    ) -> dict[str, Any]:
        tid = str(uuid.uuid4())
        now = utcnow()
        with self._lock, self._connect() as conn:
            cnt = conn.execute("SELECT COUNT(*) FROM escalation_tickets").fetchone()[0] + 1
            ticket_code = f"QA-2026-{cnt:04d}"
            conn.execute(
                """
                INSERT INTO escalation_tickets (
                    id, ticket_code, session_id, user_email, question, context_summary,
                    directorate, smtp_message_id, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tid,
                    ticket_code,
                    session_id,
                    user_email,
                    question,
                    context_summary,
                    directorate,
                    smtp_message_id,
                    status,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM escalation_tickets WHERE id = ?", (tid,)).fetchone()
            return dict(row)

    def find_ticket_by_message_ids_or_code(
        self, message_ids: list[str], ticket_code: str | None
    ) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            if ticket_code:
                row = conn.execute(
                    "SELECT * FROM escalation_tickets WHERE ticket_code = ?", (ticket_code,)
                ).fetchone()
                if row:
                    return dict(row)
            for mid in message_ids:
                cleaned = mid.strip()
                if not cleaned:
                    continue
                row = conn.execute(
                    "SELECT * FROM escalation_tickets WHERE smtp_message_id = ?", (cleaned,)
                ).fetchone()
                if row:
                    return dict(row)
        return None

    def mark_ticket_answered(
        self,
        ticket_code: str,
        expert_email: str,
        raw_reply: str,
        clean_reply: str,
        faq_id: str,
        linked_doc_title: str = "",
    ) -> None:
        now = utcnow()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE escalation_tickets
                SET status = 'indexed', expert_email = ?, expert_reply_raw = ?,
                    expert_reply_clean = ?, faq_id = ?, linked_doc_title = ?, answered_at = ?
                WHERE ticket_code = ?
                """,
                (expert_email, raw_reply, clean_reply, faq_id, linked_doc_title, now, ticket_code),
            )

    def list_tickets(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM escalation_tickets ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    # --- FAQ Entries ---
    def upsert_faq_for_ticket(
        self,
        ticket_code: str,
        question: str,
        answer: str,
        verified_by: str,
        directorate: str = "ALL",
        linked_doc_title: str = "",
    ) -> dict[str, Any]:
        """Create or cleanly update an existing FAQ entry for a ticket (prevents duplicates on follow-up replies)."""
        now = utcnow()
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM faq_entries WHERE ticket_code = ?", (ticket_code,)
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE faq_entries
                    SET answer = ?, verified_by = ?, status = 'active', created_at = ?
                    WHERE id = ?
                    """,
                    (answer, verified_by, now, existing["id"]),
                )
                row = conn.execute("SELECT * FROM faq_entries WHERE id = ?", (existing["id"],)).fetchone()
                return dict(row)
            fid = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO faq_entries (
                    id, ticket_code, question, answer, directorate,
                    linked_doc_title, verified_by, status, usage_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', 0, ?)
                """,
                (fid, ticket_code, question, answer, directorate or "ALL", linked_doc_title, verified_by, now),
            )
            row = conn.execute("SELECT * FROM faq_entries WHERE id = ?", (fid,)).fetchone()
            return dict(row)

    # Alias for backward compatibility in tests
    create_faq = upsert_faq_for_ticket

    def list_faqs(self, active_only: bool = False) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            sql = "SELECT * FROM faq_entries"
            if active_only:
                sql += " WHERE status = 'active'"
            sql += " ORDER BY created_at DESC"
            return [dict(r) for r in conn.execute(sql).fetchall()]

    def increment_faq_usage(self, faq_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE faq_entries SET usage_count = usage_count + 1 WHERE id = ?", (faq_id,))

    def toggle_faq_status(self, faq_id: str, status: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE faq_entries SET status = ? WHERE id = ?", (status, faq_id))

    def delete_faq(self, faq_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM faq_entries WHERE id = ?", (faq_id,))

    # --- Security Audit Events ---
    def log_security_event(
        self, event_type: str, actor: str, detail: str, ticket_code: str | None = None
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO security_events (id, event_type, actor, ticket_code, detail, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), event_type, actor, ticket_code, detail, utcnow()),
            )

    def list_security_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM security_events ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
            ]

    # --- Analytics ---
    def log_analytics(
        self,
        session_id: str,
        question: str,
        topic_key: str,
        outcome: str,
        directorate: str | None,
        doc_id: str | None,
        section_label: str | None,
        prompt_tokens: int,
        completion_tokens: int,
        embedding_tokens: int,
        cost_rub: float,
        latency_ms: int,
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO analytics_events (
                    id, session_id, question, topic_key, outcome, directorate,
                    doc_id, section_label, prompt_tokens, completion_tokens,
                    embedding_tokens, cost_rub, latency_ms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    session_id,
                    question,
                    topic_key,
                    outcome,
                    directorate,
                    doc_id,
                    section_label,
                    prompt_tokens,
                    completion_tokens,
                    embedding_tokens,
                    cost_rub,
                    latency_ms,
                    utcnow(),
                ),
            )

    def get_analytics_events(self, limit: int = 500) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM analytics_events ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
            ]

    # --- Indexed Docs & Clauses (for Interactive Regulation Explorer) ---
    def upsert_indexed_doc(
        self,
        doc_id: str,
        filename: str,
        doc_title: str,
        directorate: str,
        directorate_label: str,
        stage: str,
        chunk_count: int,
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO indexed_documents (
                    doc_id, filename, doc_title, directorate, directorate_label, stage, chunk_count, indexed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(doc_id) DO UPDATE SET
                    filename=excluded.filename,
                    doc_title=excluded.doc_title,
                    directorate=excluded.directorate,
                    directorate_label=excluded.directorate_label,
                    stage=excluded.stage,
                    chunk_count=excluded.chunk_count,
                    indexed_at=excluded.indexed_at
                """,
                (doc_id, filename, doc_title, directorate, directorate_label, stage, chunk_count, utcnow()),
            )

    def replace_doc_clauses(self, doc_id: str, clauses: list[dict[str, Any]]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM document_clauses WHERE doc_id = ?", (doc_id,))
            conn.executemany(
                """
                INSERT INTO document_clauses (
                    chunk_id, doc_id, seq, section_num, section_title, clause_num, raw_text, external_doc_refs_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        c["chunk_id"],
                        doc_id,
                        idx,
                        c["section_num"],
                        c["section_title"],
                        c["clause_num"],
                        c["raw_text"],
                        json.dumps(c.get("external_doc_refs") or [], ensure_ascii=False),
                    )
                    for idx, c in enumerate(clauses)
                ],
            )

    def get_doc_with_clauses(self, doc_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            doc = conn.execute(
                "SELECT * FROM indexed_documents WHERE doc_id = ?", (doc_id,)
            ).fetchone()
            if not doc:
                return None
            rows = conn.execute(
                "SELECT * FROM document_clauses WHERE doc_id = ? ORDER BY seq ASC", (doc_id,)
            ).fetchall()
        sections_map: dict[str, dict[str, Any]] = {}
        section_order: list[str] = []
        for r in rows:
            skey = r["section_num"]
            if skey not in sections_map:
                sections_map[skey] = {
                    "section_num": r["section_num"],
                    "section_title": r["section_title"],
                    "clauses": [],
                }
                section_order.append(skey)
            sections_map[skey]["clauses"].append(
                {
                    "chunk_id": r["chunk_id"],
                    "clause_num": r["clause_num"],
                    "raw_text": r["raw_text"],
                    "external_doc_refs": json.loads(r["external_doc_refs_json"] or "[]"),
                }
            )
        result = dict(doc)
        result["sections"] = [sections_map[k] for k in section_order]
        return result

    def list_indexed_docs(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            return [
                dict(r)
                for r in conn.execute("SELECT * FROM indexed_documents ORDER BY directorate, doc_title").fetchall()
            ]
