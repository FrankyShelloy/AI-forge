"""Quality Service analytics: unanswered questions, recurring topics, problematic sections, revision signals."""

from __future__ import annotations

from collections import Counter
from typing import Any

from .db import Database


def build_quality_analytics(db: Database) -> dict[str, Any]:
    events = db.get_analytics_events(limit=1000)
    tickets = db.list_tickets(limit=200)
    faqs = db.list_faqs(active_only=False)
    docs = db.list_indexed_docs()

    total_queries = len(events)
    outcomes = Counter(e["outcome"] for e in events)
    directorates = Counter((e["directorate"] or "Не указана") for e in events)

    # Recurring question topics & revision signals
    topic_counts: Counter[str] = Counter()
    topic_escalations: Counter[str] = Counter()
    topic_samples: dict[str, str] = {}
    section_hits: Counter[str] = Counter()
    section_problems: Counter[str] = Counter()

    total_prompt = 0
    total_comp = 0
    total_emb = 0
    total_cost = 0.0
    latencies: list[int] = []

    for e in events:
        tkey = e["topic_key"]
        topic_counts[tkey] += 1
        topic_samples.setdefault(tkey, e["question"])
        if e["outcome"] in {"escalated", "conflict", "partial_answer"}:
            topic_escalations[tkey] += 1
        if e["section_label"]:
            section_hits[e["section_label"]] += 1
            if e["outcome"] in {"escalated", "clarify", "conflict", "partial_answer"}:
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
                "Повторяющийся пробел в регламентах — рекомендовано дополнение нормативной базы"
                if esc_cnt >= 2
                else "Зафиксировано обращение в Службу качества по неописанному кейсу"
            ),
            "severity": "high" if esc_cnt >= 2 else "medium",
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

    avg_latency_ms = int(sum(latencies) / len(latencies)) if latencies else 0
    avg_cost_rub = round(total_cost / total_queries, 5) if total_queries else 0.0

    return {
        "summary": {
            "total_queries": total_queries,
            "answered": outcomes["answered"] + outcomes["complex_answered"] + outcomes["faq_answered"],
            "clarified": outcomes["clarify"],
            "escalated": outcomes["escalated"],
            "out_of_scope_or_refused": outcomes["out_of_scope"] + outcomes["off_topic"],
            "tickets_total": len(tickets),
            "tickets_indexed": sum(1 for t in tickets if t["status"] == "indexed"),
            "faq_active": sum(1 for f in faqs if f["status"] == "active"),
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
        "revision_signals": revision_signals,
        "documents": docs,
    }
