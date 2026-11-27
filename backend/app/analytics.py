"""Quality Service analytics, ROI projection, security audit summary, and executive report export."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from .db import Database


def build_quality_analytics(db: Database) -> dict[str, Any]:
    events = db.get_analytics_events(limit=1000)
    tickets = db.list_tickets(limit=200)
    faqs = db.list_faqs(active_only=False)
    docs = db.list_indexed_docs()
    sec_events = db.list_security_events(limit=50)

    docs_by_id = {d["doc_id"]: d for d in docs}

    total_queries = len(events)
    outcomes = Counter(e["outcome"] for e in events)
    directorates = Counter((e["directorate"] or "Не указана") for e in events)

    topic_counts: Counter[str] = Counter()
    topic_escalations: Counter[str] = Counter()
    topic_conflicts: Counter[str] = Counter()
    topic_samples: dict[str, str] = {}
    section_hits: Counter[str] = Counter()
    section_problems: Counter[str] = Counter()
    doc_hits: Counter[str] = Counter()

    total_prompt = 0
    total_comp = 0
    total_emb = 0
    total_cost = 0.0
    latencies: list[int] = []
    conflicts_total = 0

    for e in events:
        tkey = e["topic_key"]
        has_conf = bool(e.get("has_conflict"))
        if has_conf:
            conflicts_total += 1
        topic_counts[tkey] += 1
        topic_samples.setdefault(tkey, e["question"])
        if has_conf:
            topic_conflicts[tkey] += 1
        if e["outcome"] in {"escalated", "conflict", "partial_answer"} or has_conf:
            topic_escalations[tkey] += 1
        if e["doc_id"] and e["doc_id"] in docs_by_id:
            doc_hits[e["doc_id"]] += 1
        if e["section_label"]:
            section_hits[e["section_label"]] += 1
            if e["outcome"] in {"escalated", "clarify", "conflict", "partial_answer"} or has_conf:
                section_problems[e["section_label"]] += 1

        total_prompt += int(e["prompt_tokens"] or 0)
        total_comp += int(e["completion_tokens"] or 0)
        total_emb += int(e["embedding_tokens"] or 0)
        total_cost += float(e["cost_rub"] or 0.0)
        if e["latency_ms"]:
            latencies.append(int(e["latency_ms"]))

    recurring_topics = [
        {
            "topic": topic_samples[k],
            "count": cnt,
            "escalations": topic_escalations[k],
            "needs_revision": topic_escalations[k] >= 2 or cnt >= 3,
        }
        for k, cnt in topic_counts.most_common(8)
    ]

    revision_signals = [
        {
            "topic": topic_samples[k],
            "escalations": esc_cnt,
            "total_asks": topic_counts[k],
            "reason": (
                "Обнаружено расхождение между официальным регламентом и FAQ — требуется проверка и актуализация"
                if topic_conflicts[k] >= 1
                else (
                    "Повторяющийся пробел в регламентах — рекомендовано дополнение нормативной базы"
                    if esc_cnt >= 2
                    else "Зафиксировано обращение в Службу качества по неописанному или частичному кейсу"
                )
            ),
            "severity": "high" if (esc_cnt >= 2 or topic_conflicts[k] >= 1) else "medium",
        }
        for k, esc_cnt in topic_escalations.most_common(6)
        if esc_cnt >= 1
    ]

    problematic_sections = [
        {"section": sec, "problem_count": cnt, "total_hits": section_hits[sec]}
        for sec, cnt in section_problems.most_common(6)
    ]

    popular_sections = [
        {"section": sec, "hits": cnt} for sec, cnt in section_hits.most_common(6)
    ]

    doc_demand = [
        {
            "doc_id": d["doc_id"],
            "doc_title": d["doc_title"],
            "directorate_label": d["directorate_label"],
            "hits": doc_hits[d["doc_id"]],
        }
        for d in sorted(docs, key=lambda item: (-doc_hits[item["doc_id"]], item["doc_title"]))
    ]

    avg_latency_ms = int(sum(latencies) / len(latencies)) if latencies else 0
    avg_cost_rub = round(total_cost / total_queries, 5) if total_queries else 0.0

    answered_total = (
        outcomes["answered"]
        + outcomes["complex_answered"]
        + outcomes["faq_answered"]
        + outcomes["partial_answer"]
    )
    resolution_rate = round((answered_total / total_queries) * 100, 1) if total_queries else 0.0

    return {
        "summary": {
            "total_queries": total_queries,
            "answered": answered_total,
            "clarified": outcomes["clarify"],
            "escalated": outcomes["escalated"],
            "conflicts_detected": conflicts_total,
            "out_of_scope_or_refused": outcomes["out_of_scope"] + outcomes["off_topic"],
            "resolution_rate_pct": resolution_rate,
            "tickets_total": len(tickets),
            "tickets_indexed": sum(1 for t in tickets if t["status"] == "indexed"),
            "faq_active": sum(1 for f in faqs if f["status"] == "active"),
            "faq_total_reuses": sum(int(f.get("usage_count") or 0) for f in faqs),
            "security_blocked_count": len(sec_events),
            "avg_latency_ms": avg_latency_ms,
            "avg_cost_rub": avg_cost_rub,
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_comp,
            "total_embedding_tokens": total_emb,
            "total_cost_rub": round(total_cost, 4),
        },
        "outcomes": dict(outcomes),
        "directorates": dict(directorates),
        "recurring_topics": recurring_topics,
        "problematic_sections": problematic_sections,
        "popular_sections": popular_sections,
        "doc_demand": doc_demand,
        "revision_signals": revision_signals,
        "security_events": sec_events,
        "documents": docs,
    }


def export_quality_report_md(db: Database) -> str:
    """Generate an executive Markdown report for Quality Management & regulation revision."""
    data = build_quality_analytics(db)
    s = data["summary"]
    tickets = db.list_tickets(limit=50)
    faqs = db.list_faqs(active_only=False)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "# Отчёт Службы качества — ИИ-агент по корпоративным регламентам",
        f"_Сформирован: {now}_\n",
        "## 1. Ключевые показатели",
        f"- Всего обращений: **{s['total_queries']}**",
        f"- Отвечено с подтверждением по источникам: **{s['answered']}** ({s['resolution_rate_pct']}%)",
        f"- Диалогов с уточнением контекста (S2): **{s['clarified']}**",
        f"- Эскалаций в Службу качества (S3): **{s['tickets_total']}** (из них закрыто и добавлено в FAQ: **{s['tickets_indexed']}**)",
        f"- Обнаружено конфликтов «Регламент vs FAQ»: **{s['conflicts_detected']}**",
        f"- Активных проверенных разъяснений FAQ: **{s['faq_active']}** (повторных использований: **{s['faq_total_reuses']}**)",
        f"- Отклонено неавторизованных попыток / инцидентов безопасности: **{s['security_blocked_count']}**",
        f"- Средняя скорость ответа: **{s['avg_latency_ms']/1000:.2f} с** | Средняя стоимость запроса: **{s['avg_cost_rub']:.4f} ₽**\n",
        "## 2. Сигналы на пересмотр регламентов",
    ]
    if data["revision_signals"]:
        for sig in data["revision_signals"]:
            lines.append(
                f"- **[{sig['severity'].upper()}] {sig['topic']}** — проблемных обращений: {sig['escalations']}, всего вопросов: {sig['total_asks']}. _{sig['reason']}_"
            )
    else:
        lines.append("- На текущий момент критичных пробелов не зафиксировано.")

    lines.append("\n## 3. Востребованность регламентов")
    for dd in data["doc_demand"]:
        lines.append(f"- **{dd['doc_title']}** ({dd['directorate_label']}): {dd['hits']} обращений")

    lines.append("\n## 4. Повторяющиеся темы обращений")
    if data["recurring_topics"]:
        for rt in data["recurring_topics"]:
            lines.append(f"- {rt['topic']} — запросов: **{rt['count']}** (эскалаций/проблем: {rt['escalations']})")
    else:
        lines.append("- Данных пока нет.")

    lines.append("\n## 5. Проверенные разъяснения Службы качества (кандидаты на включение в регламенты)")
    if faqs:
        for f in faqs:
            lines.append(f"- **{f['ticket_code']}** (`{f['verified_by']}`, использований: {f['usage_count']}): *{f['question']}* → {f['answer']}")
    else:
        lines.append("- Проверенных разъяснений пока нет.")

    lines.append("\n## 6. Журнал обращений по Email (HITL)")
    if tickets:
        for t in tickets:
            lines.append(f"- `{t['ticket_code']}` | статус: **{t['status']}** | CC: `{t['user_email']}` | {t['question']}")
    else:
        lines.append("- Обращений пока нет.")

    return "\n".join(lines) + "\n"
