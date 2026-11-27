"""Automated quality, anti-hallucination, and token-economics benchmark on all regulation traps."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import RegulationAgent
from app.config import get_settings
from app.db import Database
from app.email_service import EmailHITLService
from app.retrieval import HybridRetriever


class StubEmailService(EmailHITLService):
    """Captures escalation emails locally during offline trap benchmarks (real SMTP tested live on server)."""

    async def send_escalation(self, *, session_id, user_email, question, context_summary, directorate=None):
        ticket = self.db.create_ticket(
            session_id=session_id,
            user_email=user_email,
            question=question,
            context_summary=context_summary,
            directorate=directorate,
            smtp_message_id="<test@corp.local>",
            status="sent",
        )
        return ticket


async def run_turn(agent: RegulationAgent, session_id: str | None, msg: str) -> dict:
    final_msg = None
    sid = session_id
    async for ev in agent.stream_turn(session_id=session_id, user_email="employee@corp.local", user_message=msg):
        if ev["event"] == "session":
            sid = ev["data"]["session_id"]
        elif ev["event"] == "done":
            final_msg = ev["data"]["message"]
    return {"session_id": sid, "message": final_msg}


async def main() -> None:
    settings = get_settings()
    tmpdir = Path(tempfile.mkdtemp(prefix="aiforge_test_"))
    settings.sqlite_path = str(tmpdir / "test.db")
    docker_docs = Path("/app/docs")
    if docker_docs.exists() and any(docker_docs.glob("*.docx")):
        settings.docs_dir = str(docker_docs)
        # Reuse the fast Rust qdrant:6333 service already running in Docker Compose
        settings.qdrant_local_path = ""
    else:
        settings.qdrant_local_path = str(tmpdir / "qdrant")
        settings.docs_dir = str(Path(__file__).resolve().parents[2] / "Поиск ответов по корпоративным документам")

    db = Database(settings.sqlite_path)
    retriever = HybridRetriever(settings, db)
    stats = retriever.bootstrap_index(force_reindex=False)
    print(f"Indexed KB: {stats}")

    email_svc = StubEmailService(settings, db, retriever)
    agent = RegulationAgent(settings, db, retriever, email_svc)

    tests = [
        # 1. S2 Ambiguity -> Complex project (Trap: budget estimation RFI + lead time 3d vs 2w)
        (
            "S2_AMBIGUOUS_BUDGET_LEADTIME",
            [
                "Можно ли использовать бюджетную оценку для подачи в конкурс и за сколько дней нужно инициировать расчёт?",
                "Комплексный проект: ДБРЦТ и Техническая дирекция",
            ],
            lambda r: r[0]["message"]["outcome"] == "clarify"
            and "rfi" in r[1]["message"]["content"].lower()
            and ("2 недел" in r[1]["message"]["content"].lower() or "две недел" in r[1]["message"]["content"].lower())
            and "3 рабоч" in r[1]["message"]["content"].lower(),
        ),
        # 2. S1 Internal nuance in IB Presale (Trap: SLA violation priority 5.2.2 vs 5.2.3)
        (
            "S1_IB_SLA_PRIORITY",
            ["Меняется ли приоритет пресейловой заявки в Дирекции ИБ при нарушении SLA?"],
            lambda r: r[0]["message"]["outcome"] in {"answered", "partial_answer"}
            and "ss" in r[0]["message"]["content"].lower()
            and "па" in r[0]["message"]["content"].lower(),
        ),
        # 3. S1 Start without contract in DBRCT (Trap: Warranty Letter + Budget Owner + Director)
        (
            "S1_START_WITHOUT_CONTRACT_DBRCT",
            ["Можно ли в ДБРЦТ начать работы по проекту без подписанного договора и согласованной сметы?"],
            lambda r: r[0]["message"]["outcome"] in {"answered", "partial_answer"}
            and "гарантий" in r[0]["message"]["content"].lower(),
        ),
        # 4. S1 Start without estimate in IB (Trap: strict prohibition 11.6)
        (
            "S1_START_WITHOUT_ESTIMATE_IB",
            ["Можно ли в Дирекции ИБ начать работы без согласованной сметы?"],
            lambda r: r[0]["message"]["outcome"] in {"answered", "partial_answer"}
            and "запрещ" in r[0]["message"]["content"].lower(),
        ),
        # 5. S1 Subcontractor contracts in Tech Dir (Trap: Logist GRSO 16.1)
        (
            "S1_SUBCONTRACTOR_TECHDIR",
            ["Кто в Технической дирекции отвечает за подготовку, согласование и подписание договора с субподрядчиком?"],
            lambda r: r[0]["message"]["outcome"] in {"answered", "partial_answer"}
            and "логист" in r[0]["message"]["content"].lower(),
        ),
        # 6. Edge-case: External missing document reference
        (
            "EDGE_MISSING_EXTERNAL_DOC",
            ["Что написано в регламенте подбора субподрядчика для ДБРЦТ и по какой ссылке его открыть?"],
            lambda r: r[0]["message"]["outcome"] in {"answered", "partial_answer", "escalated"}
            and any(w in r[0]["message"]["content"].lower() for w in ("нет", "отсутств", "не загружен", "не найден"))
            and "sharepoint" not in r[0]["message"]["content"].lower(),
        ),
        # 7. Edge-case: Out of scope -> redirect to manager (NOT Quality escalation!)
        (
            "EDGE_OUT_OF_SCOPE_BONUS",
            ["Какую премию я получу за закрытие этой сделки?"],
            lambda r: r[0]["message"]["outcome"] == "out_of_scope"
            and "руководител" in r[0]["message"]["content"].lower(),
        ),
        # 8. Edge-case: Off-topic general AI -> polite refusal
        (
            "EDGE_OFF_TOPIC_GENERAL_AI",
            ["Сделай follow-up по встрече и подскажи актуальный курс доллара"],
            lambda r: r[0]["message"]["outcome"] == "off_topic",
        ),
        # 9. S3 Escalation -> FAQ enrichment -> Reuse on paraphrased question
        (
            "S3_ESCALATE_NO_HALLUCINATION",
            ["Как согласовать и компенсировать расходы на такси при выезде проектной команды к заказчику в выходной день в ДБРЦТ?"],
            lambda r: r[0]["message"]["outcome"] == "escalated" and bool(r[0]["message"]["ticket_code"]),
        ),
    ]

    results = []
    passed = 0
    for name, prompts, check_fn in tests:
        sid = None
        turn_results = []
        for p in prompts:
            res = await run_turn(agent, sid, p)
            sid = res["session_id"]
            turn_results.append(res)
            await asyncio.sleep(1.5)
        ok = check_fn(turn_results)
        passed += int(ok)
        last = turn_results[-1]["message"]
        print(
            f"[{'PASS' if ok else 'FAIL'}] {name} | outcome={last['outcome']} | "
            f"lat={last['latency_ms']}ms | tok={last['prompt_tokens']}+{last['completion_tokens']}+{last['embedding_tokens']} | "
            f"cost={last['cost_rub']:.5f} RUB"
        )
        if not ok:
            print("  TEXT:", last["content"][:300])
        for tr in turn_results:
            m = tr["message"]
            results.append(
                {
                    "test": name,
                    "outcome": m["outcome"],
                    "prompt_tokens": m["prompt_tokens"],
                    "completion_tokens": m["completion_tokens"],
                    "embedding_tokens": m["embedding_tokens"],
                    "cost_rub": m["cost_rub"],
                    "latency_ms": m["latency_ms"],
                }
            )

    # Test S3 FAQ reuse right after simulating verified Quality reply!
    faq = db.create_faq(
        ticket_code="QA-2026-0001",
        question="Как согласовать и компенсировать расходы на такси при выезде проектной команды к заказчику в выходной день в ДБРЦТ?",
        answer="По разъяснению Службы качества: выезд проектной команды ДБРЦТ на такси в выходной день согласовывается предварительно с РП и Руководителем направления по почте, лимит компенсации — до 3 000 руб. за поездку при приложении чека к авансовому отчёту.",
        verified_by="quality@corp.local",
        directorate="DBRCT",
        linked_doc_title="Ответ Службы качества по обращению QA-2026-0001",
    )
    retriever.index_faq_entry(faq)
    reuse = await run_turn(agent, None, "Оплатят ли такси инженеру ДБРЦТ за выезд к клиенту в субботу и какой лимит?")
    rm = reuse["message"]
    ok_reuse = rm["outcome"] in {"faq_answered", "answered"} and "3 000" in rm["content"].replace("3000", "3 000")
    passed += int(ok_reuse)
    print(
        f"[{'PASS' if ok_reuse else 'FAIL'}] S3_FAQ_REUSE_PARAPHRASED | outcome={rm['outcome']} | "
        f"lat={rm['latency_ms']}ms | tok={rm['prompt_tokens']}+{rm['completion_tokens']}+{rm['embedding_tokens']} | "
        f"cost={rm['cost_rub']:.5f} RUB"
    )
    if not ok_reuse:
        print("  TEXT:", rm["content"][:300])
    results.append(
        {
            "test": "S3_FAQ_REUSE_PARAPHRASED",
            "outcome": rm["outcome"],
            "prompt_tokens": rm["prompt_tokens"],
            "completion_tokens": rm["completion_tokens"],
            "embedding_tokens": rm["embedding_tokens"],
            "cost_rub": rm["cost_rub"],
            "latency_ms": rm["latency_ms"],
        }
    )

    total_tests = len(tests) + 1
    # Clean up the synthetic test FAQ point from Qdrant so production stays spotless
    retriever.remove_faq_entry(faq["id"])
    print(f"\nSUMMARY: {passed}/{total_tests} passed")
    out_path = Path(__file__).resolve().parent / "benchmark_results.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved metrics to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
