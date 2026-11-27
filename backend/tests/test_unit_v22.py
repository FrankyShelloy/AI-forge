"""Deterministic 0-LLM unit & HTTP security tests covering all v2.2 fixes."""

from __future__ import annotations

import os
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.agent import _detect_mentioned_directorates
from app.config import Settings
from app.db import Database
from app.email_service import EmailHITLService
from app.glossary import expand_query_with_glossary
from app.parser import detect_doc_meta, parse_docx
from app.retrieval import HybridRetriever, REG_COLLECTION


def _make_minimal_docx(path: Path, title: str, clause_text: str) -> None:
    xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>{title}</w:t></w:r></w:p>
    <w:p><w:r><w:t>1. Общие положения</w:t></w:r></w:p>
    <w:p><w:r><w:t>1.1 {clause_text}</w:t></w:r></w:p>
  </w:body>
</w:document>"""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", xml)


class _StubRetriever(HybridRetriever):
    """Uses tiny deterministic vectors (dim=8) so we test real Qdrant + BM25 without any external API call."""

    def embed_texts(self, texts: list[str]) -> tuple[list[list[float]], int, float]:
        vecs = []
        for t in texts:
            v = [float((sum(t.encode("utf-8")) + i * 17) % 97) / 100.0 for i in range(8)]
            vecs.append(v)
        return vecs, len(texts) * 5, 0.0


def run_all() -> None:
    passed = 0
    total = 0

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal passed, total
        total += 1
        passed += int(bool(cond))
        print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" | {detail}" if detail else ""))

    tmpdir = Path(tempfile.mkdtemp(prefix="aiforge_unit_v22_"))
    db = Database(tmpdir / "unit.db")

    # 1. Cyrillic .docx unique doc_id & directorate (no doc_docx collision) + clean update without stale chunks
    doc_a = tmpdir / "Регламент_Облаков.docx"
    doc_b = tmpdir / "Регламент_Кадров.docx"
    _make_minimal_docx(doc_a, "Регламент Дирекции Облаков", "Срок согласования облачного стенда — 2 рабочих дня.")
    _make_minimal_docx(doc_b, "Регламент Кадровой службы", "Заявка на отпуск подается за 14 календарных дней.")

    id_a, dir_a, lbl_a, _ = detect_doc_meta(doc_a.name, "Регламент Дирекции Облаков")
    id_b, dir_b, lbl_b, _ = detect_doc_meta(doc_b.name, "Регламент Кадровой службы")
    check(
        "UNIT_CYRILLIC_DOCX_UNIQUE_IDS",
        id_a != id_b and id_a != "doc_docx" and dir_a != dir_b and dir_a.startswith("CUSTOM_"),
        f"a=({id_a},{dir_a}) b=({id_b},{dir_b})",
    )

    s = Settings(
        sqlite_path=str(tmpdir / "unit.db"),
        qdrant_local_path=str(tmpdir / "qdrant_local"),
        embedding_dim=8,
        llm_api_key="dummy",
    )
    retr = _StubRetriever(s, db)
    retr._ensure_collection(REG_COLLECTION, 8)
    retr.index_uploaded_docx(doc_a)
    # Now overwrite doc_a with updated deadline text and re-upload!
    _make_minimal_docx(doc_a, "Регламент Дирекции Облаков", "Срок согласования облачного стенда — 5 рабочих дней (новая редакция).")
    retr.index_uploaded_docx(doc_a)
    qdrant_count = retr.qdrant.count(REG_COLLECTION).count
    bm25_texts = [d["raw_clause_text"] for d in retr._reg_docs if d["doc_id"] == id_a]
    check(
        "UNIT_DOCX_UPDATE_DELETES_STALE_CHUNKS",
        qdrant_count == 1
        and len(bm25_texts) == 1
        and "5 рабочих дней" in bm25_texts[0]
        and "2 рабочих дня" not in bm25_texts[0],
        f"qdrant_count={qdrant_count} bm25_len={len(bm25_texts)}",
    )

    # 2. db.get_messages(..., limit=20) returns LAST 20 messages in chronological order
    sess_hist = db.get_or_create_session(None, "emp@corp.local", title="Long Chat", client_id="c-hist")
    for i in range(25):
        db.add_message(sess_hist["id"], "user" if i % 2 == 0 else "assistant", f"msg_{i}")
    recent_20 = db.get_messages(sess_hist["id"], limit=20)
    check(
        "UNIT_GET_MESSAGES_RETURNS_MOST_RECENT_20",
        len(recent_20) == 20 and recent_20[0]["content"] == "msg_5" and recent_20[-1]["content"] == "msg_24",
        f"first={recent_20[0]['content']} last={recent_20[-1]['content']}",
    )

    # 3. Glossary lowercase 2-letter acronyms (рп, ам, кп) expanded, preposition "по" NOT expanded
    expanded, notes = expand_query_with_glossary("что делает рп и ам по кп?")
    notes_str = " | ".join(notes)
    check(
        "UNIT_GLOSSARY_LOWERCASE_2LETTER_NO_PREPOSITION",
        "РП =" in notes_str
        and "АМ =" in notes_str
        and "КП =" in notes_str
        and "ПО =" not in notes_str
        and "Программное обеспечение" not in expanded,
        f"notes={notes}",
    )

    # 4. Session privacy: X-Client-Id: internal CANNOT read/delete another user's sessions; no 'default' takeover
    s_alpha = db.get_or_create_session(None, "a@corp.local", title="Secret Alpha", client_id="client-alpha")
    s_def = db.get_or_create_session(None, "d@corp.local", title="Default Sess", client_id="default")
    internal_list = db.list_sessions(client_id="internal")
    internal_del = db.delete_session(s_alpha["id"], client_id="internal")
    hijack_attempt = db.get_or_create_session(s_def["id"], "hacker@corp.local", title="Hijack", client_id="client-beta")
    check(
        "UNIT_PRIVACY_NO_INTERNAL_BYPASS_NO_DEFAULT_TAKEOVER",
        len(internal_list) == 0
        and internal_del is False
        and hijack_attempt["id"] != s_def["id"]
        and hijack_attempt["client_id"] == "client-beta",
        f"internal_list={len(internal_list)} del={internal_del} hijack_same={hijack_attempt['id']==s_def['id']}",
    )

    # 5. Ticket code monotonic MAX+1 even after deletion (no IntegrityError)
    t1 = db.create_ticket(s_alpha["id"], "a@corp.local", "Q1", "", None, "<1>")
    t2 = db.create_ticket(s_alpha["id"], "a@corp.local", "Q2", "", None, "<2>")
    with db._lock, db._connect() as conn:
        conn.execute("DELETE FROM escalation_tickets WHERE ticket_code = ?", (t1["ticket_code"],))
    t3 = db.create_ticket(s_alpha["id"], "a@corp.local", "Q3", "", None, "<3>")
    check(
        "UNIT_TICKET_CODE_NO_COLLISION_AFTER_DELETE",
        t2["ticket_code"] == "QA-2026-0002" and t3["ticket_code"] == "QA-2026-0003",
        f"t2={t2['ticket_code']} t3={t3['ticket_code']}",
    )

    # 6. IMAP polling lock prevents concurrent duplicate ingestion
    email_svc = EmailHITLService(s, db, retr)
    email_svc._imap_lock.acquire()
    try:
        busy_res = email_svc._poll_imap_sync()
    finally:
        email_svc._imap_lock.release()
    check(
        "UNIT_IMAP_LOCK_PREVENTS_CONCURRENT_RACE",
        bool(busy_res.get("skipped_busy")) and busy_res.get("processed") == 0,
        f"res={busy_res}",
    )

    # 7. FastAPI HTTP endpoint checks: robots.txt GET+HEAD=200, LFI guard, RBAC 403/200 on analytics/tickets/faqs/delete
    from app import main as app_main

    client = TestClient(app_main.app)
    r_robots = client.get("/robots.txt")
    r_robots_head = client.head("/robots.txt")
    r_lfi = client.get("//etc/passwd")
    r_ana_unauth = client.get("/api/analytics")
    r_tik_unauth = client.get("/api/tickets")
    r_exp_unauth = client.get("/api/analytics/export")
    r_ana_ok = client.get("/api/analytics", headers={"X-Quality-Key": app_main.settings.quality_admin_pin})
    r_tik_ok = client.get("/api/tickets", headers={"X-Quality-Key": app_main.settings.quality_admin_pin})
    r_del_core = client.delete(
        "/api/documents/dbrct_project",
        headers={"X-Quality-Key": app_main.settings.quality_admin_pin},
    )

    # Test GET /api/faqs hides disabled entries for non-Quality role and shows them for Quality role
    tmp_faq = app_main.db.upsert_faq_for_ticket("QA-2026-8888", "Temp Q", "Temp A", "quality@corp.local")
    app_main.db.toggle_faq_status(tmp_faq["id"], "disabled")
    faqs_emp = client.get("/api/faqs").json()
    faqs_qual = client.get("/api/faqs", headers={"X-Quality-Key": app_main.settings.quality_admin_pin}).json()
    app_main.db.delete_faq(tmp_faq["id"])

    # Clean up RBAC test security events from live DB if running in app container
    with app_main.db._lock, app_main.db._connect() as conn:
        conn.execute("DELETE FROM security_events WHERE event_type = 'rbac_access_blocked'")

    check(
        "UNIT_HTTP_ROBOTS_LFI_AND_QUALITY_RBAC",
        r_robots.status_code == 200
        and r_robots_head.status_code == 200
        and "Disallow: /" in r_robots.text
        and "root:x:0:0" not in r_lfi.text
        and r_ana_unauth.status_code == 403
        and r_tik_unauth.status_code == 403
        and r_exp_unauth.status_code == 403
        and r_ana_ok.status_code == 200
        and "doc_demand" in r_ana_ok.json()
        and r_tik_ok.status_code == 200
        and r_del_core.status_code == 400
        and all(f["id"] != tmp_faq["id"] for f in faqs_emp)
        and any(f["id"] == tmp_faq["id"] for f in faqs_qual),
        f"robots=({r_robots.status_code},{r_robots_head.status_code}) lfi_safe={'root:x:0:0' not in r_lfi.text} del_core={r_del_core.status_code}",
    )

    # 8. Unnumbered .docx fallback chunking splits long unnumbered prose into multiple chunks (no truncation)
    unnum_path = tmpdir / "Памятка_без_нумерации.docx"
    long_paras = "".join(
        f"<w:p><w:r><w:t>Абзац {i}: Подробное описание корпоративного процесса согласования и контроля качества проектных артефактов для подразделения облачной инфраструктуры. "
        + ("Детали выполнения задачи и порядок взаимодействия с заказчиком. " * 3)
        + "</w:t></w:r></w:p>"
        for i in range(12)
    )
    unnum_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Памятка облачной дирекции</w:t></w:r></w:p>
    {long_paras}
  </w:body>
</w:document>"""
    with zipfile.ZipFile(unnum_path, "w") as zf:
        zf.writestr("word/document.xml", unnum_xml)
    unnum_chunks = parse_docx(unnum_path)
    check(
        "UNIT_UNNUMBERED_DOCX_WINDOW_CHUNKING",
        len(unnum_chunks) >= 3 and any("Абзац 11" in c.raw_clause_text for c in unnum_chunks),
        f"chunks={len(unnum_chunks)}",
    )

    # 9. Dynamic custom uploaded directorate recognition (prevents clarification loop on CUSTOM_*)
    extra_docs_meta = [
        {"directorate": dir_a, "directorate_label": lbl_a, "doc_title": "Регламент Дирекции Облаков"},
        {"directorate": dir_b, "directorate_label": lbl_b, "doc_title": "Регламент Кадровой службы"},
    ]
    matched_dirs, is_multi_c = _detect_mentioned_directorates(lbl_a, extra_docs=extra_docs_meta)
    matched_stem, _ = _detect_mentioned_directorates("вопрос про облаков", extra_docs=extra_docs_meta)
    check(
        "UNIT_CUSTOM_DIRECTORATE_RECOGNITION",
        matched_dirs == [dir_a] and is_multi_c is False and matched_stem == [dir_a],
        f"exact={matched_dirs} stem={matched_stem}",
    )

    print(f"\nUNIT v2.3 SUMMARY: {passed}/{total} passed")
    if passed != total:
        sys.exit(1)


if __name__ == "__main__":
    run_all()
