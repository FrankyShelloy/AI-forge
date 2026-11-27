"""Hybrid retrieval engine: Qdrant Dense (qwen3-embedding-4b) + BM25 + RRF.

Maintains two strictly separated knowledge layers:
1. `regulations_official` (priority_rank = 1) — official corporate .docx regulations.
2. `quality_faq` (priority_rank = 2) — verified clarifications received from Quality Service via Email.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.http import models as qm
from rank_bm25 import BM25Okapi

from .config import Settings
from .db import Database
from .glossary import expand_query_with_glossary
from .parser import ParsedChunk, parse_directory, parse_docx

logger = logging.getLogger(__name__)

REG_COLLECTION = "regulations_official"
FAQ_COLLECTION = "quality_faq"

TOKEN_RE = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9]+(?:-[a-zA-Zа-яА-ЯёЁ0-9]+)?")


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in TOKEN_RE.findall(text) if len(t) >= 2]


def _str_to_uuid(s: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, s))


@dataclass
class RetrievedItem:
    id: str
    source_type: str          # official_regulation | quality_faq
    priority_rank: int        # 1 for regulation, 2 for quality_faq
    score: float
    doc_id: str
    doc_title: str
    directorate: str
    directorate_label: str
    stage: str
    section_num: str
    section_title: str
    clause_num: str
    parent_text: str
    raw_text: str
    external_doc_refs: list[str]
    faq_id: str | None = None
    ticket_code: str | None = None
    verified_by: str | None = None
    answered_at: str | None = None


class HybridRetriever:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self._lock = threading.Lock()

        self.openai = OpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            timeout=30.0,
        )
        if settings.qdrant_local_path:
            self.qdrant = QdrantClient(path=settings.qdrant_local_path)
        else:
            self.qdrant = QdrantClient(url=settings.qdrant_url, timeout=15.0)

        # In-memory BM25 corpora synced with Qdrant + tiny query embedding LRU cache
        self._reg_docs: list[dict[str, Any]] = []
        self._reg_bm25: BM25Okapi | None = None
        self._faq_docs: list[dict[str, Any]] = []
        self._faq_bm25: BM25Okapi | None = None
        self._emb_cache: dict[str, tuple[list[float], int, float]] = {}

    def _ensure_collection(self, name: str, dim: int) -> None:
        existing = {c.name for c in self.qdrant.get_collections().collections}
        if name not in existing:
            self.qdrant.create_collection(
                collection_name=name,
                vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE),
            )

    def embed_texts(self, texts: list[str]) -> tuple[list[list[float]], int, float]:
        """Compute embeddings via OpenAI-compatible API with 429 retry & single-query cache."""
        if not texts:
            return [], 0, 0.0
        if len(texts) == 1 and texts[0] in self._emb_cache:
            vec, toks, cost = self._emb_cache[texts[0]]
            return [vec], toks, 0.0

        vectors: list[list[float]] = []
        total_tokens = 0
        total_cost = 0.0
        batch_size = 48
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            resp = None
            for attempt in range(4):
                try:
                    resp = self.openai.embeddings.create(
                        model=self.settings.embedding_model,
                        input=batch,
                    )
                    break
                except Exception as exc:
                    if "429" in str(exc) and attempt < 3:
                        time.sleep(2.0 * (attempt + 1))
                        continue
                    raise
            assert resp is not None
            vectors.extend([d.embedding for d in resp.data])
            if resp.usage:
                total_tokens += int(getattr(resp.usage, "prompt_tokens", 0) or getattr(resp.usage, "total_tokens", 0) or 0)
                extra = getattr(resp.usage, "model_extra", None) or {}
                total_cost += float(extra.get("cost_rub", 0.0) or 0.0)
        if len(texts) == 1 and vectors:
            if len(self._emb_cache) > 256:
                self._emb_cache.clear()
            self._emb_cache[texts[0]] = (vectors[0], total_tokens, total_cost)
        return vectors, total_tokens, total_cost

    def _rebuild_bm25_regs(self) -> None:
        if not self._reg_docs:
            self._reg_bm25 = None
            return
        corpus = [_tokenize(d["child_text"]) for d in self._reg_docs]
        self._reg_bm25 = BM25Okapi(corpus)

    def _rebuild_bm25_faqs(self) -> None:
        if not self._faq_docs:
            self._faq_bm25 = None
            return
        corpus = [_tokenize(f"{d['question']} {d['answer']}") for d in self._faq_docs]
        self._faq_bm25 = BM25Okapi(corpus)

    def bootstrap_index(self, force_reindex: bool = False) -> dict[str, int]:
        """Ensure official regulations and active FAQ entries are indexed in Qdrant + BM25."""
        chunks = parse_directory(self.settings.docs_dir)
        dim = self.settings.embedding_dim

        # Ensure collections exist
        self._ensure_collection(REG_COLLECTION, dim)
        self._ensure_collection(FAQ_COLLECTION, dim)

        reg_count = self.qdrant.count(REG_COLLECTION).count
        if force_reindex or reg_count < len(chunks):
            logger.info("Indexing %d regulation chunks into Qdrant...", len(chunks))
            self._index_chunks(chunks, replace_all=force_reindex)
        else:
            logger.info("Qdrant already has %d regulation chunks; loading BM25 index.", reg_count)
            self._load_bm25_from_chunks(chunks)

        # Record indexed documents & structured clauses metadata in SQLite (for Document Explorer)
        by_doc: dict[str, list[ParsedChunk]] = {}
        for c in chunks:
            by_doc.setdefault(c.doc_id, []).append(c)
        for doc_id, dchunks in by_doc.items():
            c0 = dchunks[0]
            self.db.upsert_indexed_doc(
                doc_id=doc_id,
                filename=c0.filename,
                doc_title=c0.doc_title,
                directorate=c0.directorate,
                directorate_label=c0.directorate_label,
                stage=c0.stage,
                chunk_count=len(dchunks),
            )
            self.db.replace_doc_clauses(
                doc_id,
                [
                    {
                        "chunk_id": ch.chunk_id,
                        "section_num": ch.section_num,
                        "section_title": ch.section_title,
                        "clause_num": ch.clause_num,
                        "raw_text": ch.raw_clause_text,
                        "external_doc_refs": ch.external_doc_refs,
                    }
                    for ch in dchunks
                ],
            )

        # Sync active FAQ entries from SQLite into Qdrant & BM25
        faqs = self.db.list_faqs(active_only=True)
        faq_indexed = self._sync_faqs(faqs)
        return {"regulation_chunks": len(self._reg_docs), "faq_entries": faq_indexed}

    def _chunk_to_payload(self, c: ParsedChunk) -> dict[str, Any]:
        return {
            "chunk_id": c.chunk_id,
            "doc_id": c.doc_id,
            "doc_title": c.doc_title,
            "filename": c.filename,
            "directorate": c.directorate,
            "directorate_label": c.directorate_label,
            "stage": c.stage,
            "section_num": c.section_num,
            "section_title": c.section_title,
            "clause_num": c.clause_num,
            "child_text": c.child_text,
            "parent_text": c.parent_text,
            "raw_clause_text": c.raw_clause_text,
            "external_doc_refs": c.external_doc_refs,
            "source_type": "official_regulation",
            "priority_rank": 1,
        }

    def _load_bm25_from_chunks(self, chunks: list[ParsedChunk]) -> None:
        with self._lock:
            self._reg_docs = [self._chunk_to_payload(c) for c in chunks]
            self._rebuild_bm25_regs()

    def _index_chunks(self, chunks: list[ParsedChunk], replace_all: bool = False) -> None:
        if not chunks:
            return
        texts = [c.child_text for c in chunks]
        vectors, tokens, cost = self.embed_texts(texts)
        dim = len(vectors[0])
        if replace_all:
            self.qdrant.recreate_collection(
                collection_name=REG_COLLECTION,
                vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE),
            )
        else:
            self._ensure_collection(REG_COLLECTION, dim)

        points = []
        payloads = []
        for c, vec in zip(chunks, vectors):
            p = self._chunk_to_payload(c)
            payloads.append(p)
            points.append(qm.PointStruct(id=_str_to_uuid(c.chunk_id), vector=vec, payload=p))

        for i in range(0, len(points), 100):
            self.qdrant.upsert(collection_name=REG_COLLECTION, points=points[i : i + 100])

        with self._lock:
            if replace_all or not self._reg_docs:
                self._reg_docs = payloads
            else:
                existing_ids = {d["chunk_id"] for d in payloads}
                self._reg_docs = [d for d in self._reg_docs if d["chunk_id"] not in existing_ids] + payloads
            self._rebuild_bm25_regs()
        logger.info("Indexed %d chunks (%d emb tokens, %.4f RUB)", len(chunks), tokens, cost)

    def index_uploaded_docx(self, file_path: str | Path) -> dict[str, Any]:
        """Parse and index a newly uploaded .docx regulation without restarting."""
        chunks = parse_docx(file_path)
        if not chunks:
            raise ValueError("Не удалось извлечь текст из документа .docx")
        self._index_chunks(chunks, replace_all=False)
        c0 = chunks[0]
        self.db.upsert_indexed_doc(
            doc_id=c0.doc_id,
            filename=c0.filename,
            doc_title=c0.doc_title,
            directorate=c0.directorate,
            directorate_label=c0.directorate_label,
            stage=c0.stage,
            chunk_count=len(chunks),
        )
        self.db.replace_doc_clauses(
            c0.doc_id,
            [
                {
                    "chunk_id": ch.chunk_id,
                    "section_num": ch.section_num,
                    "section_title": ch.section_title,
                    "clause_num": ch.clause_num,
                    "raw_text": ch.raw_clause_text,
                    "external_doc_refs": ch.external_doc_refs,
                }
                for ch in chunks
            ],
        )
        return {
            "doc_id": c0.doc_id,
            "doc_title": c0.doc_title,
            "directorate_label": c0.directorate_label,
            "chunk_count": len(chunks),
        }

    def index_faq_entry(self, faq: dict[str, Any]) -> None:
        """Index a single verified Quality Service FAQ entry into Qdrant + BM25."""
        text = f"Вопрос: {faq['question']}\nОтвет Службы качества: {faq['answer']}"
        vectors, _, _ = self.embed_texts([text])
        dim = len(vectors[0])
        self._ensure_collection(FAQ_COLLECTION, dim)
        payload = {
            "faq_id": faq["id"],
            "ticket_code": faq["ticket_code"],
            "question": faq["question"],
            "answer": faq["answer"],
            "directorate": faq.get("directorate") or "ALL",
            "linked_doc_title": faq.get("linked_doc_title") or "Разъяснение Службы качества",
            "verified_by": faq["verified_by"],
            "created_at": faq["created_at"],
            "source_type": "quality_faq",
            "priority_rank": 2,
        }
        self.qdrant.upsert(
            collection_name=FAQ_COLLECTION,
            points=[qm.PointStruct(id=_str_to_uuid(faq["id"]), vector=vectors[0], payload=payload)],
        )
        with self._lock:
            self._faq_docs = [d for d in self._faq_docs if d["faq_id"] != faq["id"]] + [payload]
            self._rebuild_bm25_faqs()

    def remove_faq_entry(self, faq_id: str) -> None:
        try:
            self.qdrant.delete(
                collection_name=FAQ_COLLECTION,
                points_selector=qm.PointIdsList(points=[_str_to_uuid(faq_id)]),
            )
        except Exception:
            pass
        with self._lock:
            self._faq_docs = [d for d in self._faq_docs if d["faq_id"] != faq_id]
            self._rebuild_bm25_faqs()

    def _sync_faqs(self, faqs: list[dict[str, Any]]) -> int:
        if not faqs:
            with self._lock:
                self._faq_docs = []
                self._faq_bm25 = None
            return 0
        # Ensure all active FAQs exist in Qdrant + BM25
        texts = [f"Вопрос: {f['question']}\nОтвет Службы качества: {f['answer']}" for f in faqs]
        vectors, _, _ = self.embed_texts(texts)
        points = []
        payloads = []
        for faq, vec in zip(faqs, vectors):
            payload = {
                "faq_id": faq["id"],
                "ticket_code": faq["ticket_code"],
                "question": faq["question"],
                "answer": faq["answer"],
                "directorate": faq.get("directorate") or "ALL",
                "linked_doc_title": faq.get("linked_doc_title") or "Разъяснение Службы качества",
                "verified_by": faq["verified_by"],
                "created_at": faq["created_at"],
                "source_type": "quality_faq",
                "priority_rank": 2,
            }
            payloads.append(payload)
            points.append(qm.PointStruct(id=_str_to_uuid(faq["id"]), vector=vec, payload=payload))
        self.qdrant.upsert(collection_name=FAQ_COLLECTION, points=points)
        with self._lock:
            self._faq_docs = payloads
            self._rebuild_bm25_faqs()
        return len(payloads)

    def search(
        self,
        query: str,
        *,
        directorate_filter: str | None = None,
        directorates_list: list[str] | None = None,
        top_k_regs: int = 8,
        top_k_faq: int = 3,
    ) -> tuple[list[RetrievedItem], list[RetrievedItem], int, float, list[str]]:
        """Execute hybrid Dense + BM25 + RRF search across Regulations and Verified FAQ.

        Supports single-directorate filter, balanced cross-directorate search, OR explicit
        multi-directorate search (e.g. ['DBRCT', 'TECH_DIR'] for complex projects) using
        a SINGLE embedding API call.
        """
        expanded_query, matched_glossary = expand_query_with_glossary(query)
        vectors, emb_tokens, emb_cost = self.embed_texts([expanded_query])
        q_vec = vectors[0]

        q_tokens = _tokenize(expanded_query)
        with self._lock:
            bm25 = self._reg_bm25
            reg_docs = list(self._reg_docs)

        def _search_regs_for_filter(dir_val: str | None, limit_k: int) -> list[RetrievedItem]:
            q_filter = None
            if dir_val and dir_val in {"DBRCT", "TECH_DIR", "IB"}:
                q_filter = qm.Filter(
                    must=[qm.FieldCondition(key="directorate", match=qm.MatchValue(value=dir_val))]
                )
            dense_hits = self.qdrant.query_points(
                collection_name=REG_COLLECTION,
                query=q_vec,
                query_filter=q_filter,
                limit=24,
                with_payload=True,
            ).points

            rrf_scores: dict[str, float] = {}
            payload_by_id: dict[str, dict[str, Any]] = {}
            dense_sim_by_id: dict[str, float] = {}
            k_rrf = 60.0

            for rank, pt in enumerate(dense_hits):
                p = pt.payload or {}
                cid = p.get("chunk_id")
                if not cid:
                    continue
                payload_by_id[cid] = p
                dense_sim_by_id[cid] = float(pt.score)
                rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 1.3 / (k_rrf + rank + 1)

            if bm25 and q_tokens:
                scores = bm25.get_scores(q_tokens)
                indexed_scores = [
                    (idx, float(sc))
                    for idx, sc in enumerate(scores)
                    if sc > 0.0
                    and (
                        not dir_val
                        or dir_val not in {"DBRCT", "TECH_DIR", "IB"}
                        or reg_docs[idx]["directorate"] == dir_val
                    )
                ]
                indexed_scores.sort(key=lambda x: x[1], reverse=True)
                for rank, (idx, _) in enumerate(indexed_scores[:24]):
                    p = reg_docs[idx]
                    cid = p["chunk_id"]
                    payload_by_id[cid] = p
                    rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 1.0 / (k_rrf + rank + 1)

            ranked_ids = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)
            selected_ids: list[str] = []
            if not dir_val:
                seen_dirs: dict[str, int] = {}
                for cid in ranked_ids:
                    if len(selected_ids) >= limit_k:
                        break
                    d = payload_by_id[cid]["directorate"]
                    if seen_dirs.get(d, 0) < 3:
                        selected_ids.append(cid)
                        seen_dirs[d] = seen_dirs.get(d, 0) + 1
                for cid in ranked_ids:
                    if len(selected_ids) >= limit_k:
                        break
                    if cid not in selected_ids:
                        selected_ids.append(cid)
            else:
                selected_ids = ranked_ids[:limit_k]

            out: list[RetrievedItem] = []
            for cid in selected_ids:
                p = payload_by_id[cid]
                sim = dense_sim_by_id.get(cid, 0.0)
                score = round(sim if sim > 0 else min(0.85, rrf_scores[cid] * 25.0), 4)
                out.append(
                    RetrievedItem(
                        id=cid,
                        source_type="official_regulation",
                        priority_rank=1,
                        score=score,
                        doc_id=p["doc_id"],
                        doc_title=p["doc_title"],
                        directorate=p["directorate"],
                        directorate_label=p["directorate_label"],
                        stage=p["stage"],
                        section_num=p["section_num"],
                        section_title=p["section_title"],
                        clause_num=p["clause_num"],
                        parent_text=p["parent_text"],
                        raw_text=p["raw_clause_text"],
                        external_doc_refs=p.get("external_doc_refs") or [],
                    )
                )
            return out

        # 1. Search official regulations
        if directorates_list and len(directorates_list) >= 2:
            per_dir = max(4, top_k_regs // len(directorates_list))
            reg_items: list[RetrievedItem] = []
            seen_cids: set[str] = set()
            for dcode in directorates_list:
                for it in _search_regs_for_filter(dcode, per_dir):
                    if it.id not in seen_cids:
                        seen_cids.add(it.id)
                        reg_items.append(it)
        else:
            reg_items = _search_regs_for_filter(directorate_filter, top_k_regs)

        k_rrf = 60.0

        # 2. Search verified Quality Service FAQ
        faq_items: list[RetrievedItem] = []
        with self._lock:
            has_faqs = len(self._faq_docs) > 0
            faq_bm25 = self._faq_bm25
            faq_docs = list(self._faq_docs)

        if has_faqs:
            faq_dense = self.qdrant.query_points(
                collection_name=FAQ_COLLECTION,
                query=q_vec,
                limit=max(5, top_k_faq),
                with_payload=True,
            ).points
            faq_rrf: dict[str, float] = {}
            faq_payloads: dict[str, dict[str, Any]] = {}
            faq_sim: dict[str, float] = {}
            for rank, pt in enumerate(faq_dense):
                p = pt.payload or {}
                fid = p.get("faq_id")
                if not fid:
                    continue
                faq_payloads[fid] = p
                faq_sim[fid] = float(pt.score)
                if pt.score >= 0.42:
                    faq_rrf[fid] = faq_rrf.get(fid, 0.0) + 1.4 / (k_rrf + rank + 1)

            if faq_bm25 and q_tokens:
                f_scores = faq_bm25.get_scores(q_tokens)
                f_idx = sorted(
                    [(i, float(s)) for i, s in enumerate(f_scores) if s > 0.5],
                    key=lambda x: x[1],
                    reverse=True,
                )
                for rank, (i, _) in enumerate(f_idx[:5]):
                    p = faq_docs[i]
                    fid = p["faq_id"]
                    faq_payloads[fid] = p
                    faq_rrf[fid] = faq_rrf.get(fid, 0.0) + 1.0 / (k_rrf + rank + 1)

            for fid in sorted(faq_rrf.keys(), key=lambda x: faq_rrf[x], reverse=True)[:top_k_faq]:
                p = faq_payloads[fid]
                sim = faq_sim.get(fid, 0.5)
                if sim < 0.42 and faq_rrf[fid] < 0.02:
                    continue
                faq_items.append(
                    RetrievedItem(
                        id=fid,
                        source_type="quality_faq",
                        priority_rank=2,
                        score=round(sim, 4),
                        doc_id=f"faq_{p['ticket_code']}",
                        doc_title=f"Разъяснение Службы качества ({p['ticket_code']})",
                        directorate=p.get("directorate") or "ALL",
                        directorate_label=p.get("directorate") or "Служба качества",
                        stage="faq",
                        section_num="FAQ",
                        section_title=p.get("linked_doc_title") or "Проверенная база знаний",
                        clause_num=p["ticket_code"],
                        parent_text=f"[Разъяснение Службы качества | Тикет {p['ticket_code']} | Автор: {p['verified_by']}]\nВопрос: {p['question']}\nОтвет эксперта: {p['answer']}",
                        raw_text=f"Вопрос: {p['question']}\n\nОтвет эксперта Службы качества ({p['verified_by']}): {p['answer']}",
                        external_doc_refs=[],
                        faq_id=fid,
                        ticket_code=p["ticket_code"],
                        verified_by=p["verified_by"],
                        answered_at=p.get("created_at"),
                    )
                )

        return reg_items, faq_items, emb_tokens, emb_cost, matched_glossary
