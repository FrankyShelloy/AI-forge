"""Hierarchical XML-aware parser for corporate .docx regulations.

Preserves exact document order (paragraphs + Markdown tables + bullet nesting + 'Важно:' notes)
and constructs Parent-Child chunks with rich metadata and detected external document references.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
W_VAL = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val"

SECTION_RE = re.compile(r"^(\d+)\.\s+(.+)$")
CLAUSE_RE = re.compile(r"^(\d+\.\d+(?:\.\d+)*)\.?\s*(.*)$")
EXTERNAL_DOC_RE = re.compile(
    r"(?:регламент[а-яё]*|инструкци[а-яё]*|чек-лист[а-яё]*|шаблон[а-яё]*|матриц[а-яё]*|процедур[а-яё]*|политик[а-яё]*)\s+[а-яёА-ЯЁa-zA-Z0-9«»\"'\- ]{4,70}",
    re.I,
)


@dataclass
class ParsedChunk:
    chunk_id: str
    doc_id: str
    doc_title: str
    filename: str
    directorate: str       # DBRCT | TECH_DIR | IB | OTHER
    directorate_label: str
    stage: str             # presale | project | both
    section_num: str
    section_title: str
    clause_num: str
    child_text: str        # Contextualized text indexed for Dense + BM25 search
    parent_text: str       # Broader section/clause context fed to LLM
    raw_clause_text: str   # Clean verbatim clause text for Source Inspector UI
    external_doc_refs: list[str] = field(default_factory=list)


def detect_doc_meta(filename: str, first_line: str) -> tuple[str, str, str, str]:
    """Infer (doc_id, directorate, directorate_label, stage) from filename and title."""
    low = f"{filename} {first_line}".lower()
    if "дбрцт" in low or "бизнес-решен" in low:
        return "dbrct_project", "DBRCT", "ДБРЦТ", "both"
    if "тех" in low and "дирекц" in low:
        return "tech_dir_project", "TECH_DIR", "Техническая дирекция", "both"
    if "пресейл" in low and ("иб" in low or "безопасност" in low):
        return "ib_presale", "IB", "Дирекция ИБ", "presale"
    if "иб" in low or "информационной безопасности" in low:
        return "ib_project", "IB", "Дирекция ИБ", "project"
    slug = re.sub(r"[^a-z0-9]+", "_", filename.lower())[:32].strip("_") or "custom_doc"
    return f"doc_{slug}", "OTHER", Path(filename).stem, "both"


def _extract_table_md(tbl_el: ET.Element) -> str:
    rows: list[list[str]] = []
    for tr in tbl_el.findall("w:tr", NS):
        cells: list[str] = []
        for tc in tr.findall("w:tc", NS):
            parts = []
            for p in tc.findall("w:p", NS):
                txt = "".join(t.text for t in p.findall(".//w:t", NS) if t.text).strip()
                if txt:
                    parts.append(txt)
            cells.append(" ; ".join(parts))
        if any(cells):
            rows.append(cells)
    if not rows:
        return ""
    col_count = max(len(r) for r in rows)
    norm = [r + [""] * (col_count - len(r)) for r in rows]
    lines = ["| " + " | ".join(norm[0]) + " |", "| " + " | ".join(["---"] * col_count) + " |"]
    for row in norm[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _extract_external_refs(text: str) -> list[str]:
    refs: list[str] = []
    for m in EXTERNAL_DOC_RE.finditer(text):
        phrase = re.sub(r"\s+", " ", m.group(0)).strip(" .,;:()«»\"")
        if len(phrase) >= 12 and phrase not in refs:
            refs.append(phrase)
    return refs[:5]


def parse_docx(path: str | Path) -> list[ParsedChunk]:
    """Parse a .docx regulation into hierarchical Parent-Child chunks."""
    path = Path(path)
    with zipfile.ZipFile(path) as zf:
        root = ET.fromstring(zf.read("word/document.xml"))

    body = root.find("w:body", NS)
    if body is None:
        return []

    raw_items: list[tuple[str, str, str]] = []  # (kind: p|tbl, level, text)
    for child in body:
        tag = child.tag.split("}")[-1]
        if tag == "p":
            txt = "".join(t.text for t in child.findall(".//w:t", NS) if t.text).strip()
            if not txt:
                continue
            num_pr = child.find(".//w:numPr", NS)
            lvl = ""
            if num_pr is not None:
                ilvl = num_pr.find("w:ilvl", NS)
                if ilvl is not None:
                    lvl = f"L{ilvl.attrib.get(W_VAL, '0')}"
            raw_items.append(("p", lvl, txt))
        elif tag == "tbl":
            md = _extract_table_md(child)
            if md:
                raw_items.append(("tbl", "", md))

    if not raw_items:
        return []

    first_line = raw_items[0][2]
    doc_id, directorate, dir_label, stage = detect_doc_meta(path.name, first_line)
    doc_title = path.stem

    # Group items into sections -> clauses (grouping subclauses/bullets/tables/notes together)
    sections: list[dict] = []
    current_sec = {"num": "0", "title": doc_title, "clauses": []}
    current_clause: dict | None = None

    def flush_clause() -> None:
        nonlocal current_clause
        if current_clause and current_clause["lines"]:
            current_sec["clauses"].append(current_clause)
        current_clause = None

    for idx, (kind, lvl, text) in enumerate(raw_items):
        if idx == 0 and not SECTION_RE.match(text) and not CLAUSE_RE.match(text):
            # Document main title
            if len(text) < 160:
                doc_title = text
            continue

        if kind == "p" and not lvl:
            sec_m = SECTION_RE.match(text)
            clause_m = CLAUSE_RE.match(text)
            if sec_m and not clause_m:
                flush_clause()
                if current_sec["clauses"]:
                    sections.append(current_sec)
                current_sec = {
                    "num": sec_m.group(1),
                    "title": sec_m.group(2).strip(),
                    "clauses": [],
                }
                continue

            if clause_m:
                c_num = clause_m.group(1)
                # Keep deeply nested 3-level subclauses (e.g. 5.2.3) attached to parent 2-level clause (5.2)
                # ONLY if parent is short, OR emit as its own child linked to the 2-level parent!
                # Emitting each numbered clause as its own child while sharing parent_text of the 2-level group
                # gives exact clause citations (e.g. п. 5.2.2 vs п. 5.2.3) AND full note visibility!
                flush_clause()
                current_clause = {"num": c_num, "lines": [text]}
                continue

        # Continuation: bullet list, table, "Важно:", or unnumbered note
        prefix = "  - " if lvl == "L0" else ("    • " if lvl == "L1" else "")
        formatted = f"{prefix}{text}" if kind == "p" else f"\n{text}\n"
        if current_clause is None:
            current_clause = {"num": f"{current_sec['num']}.0", "lines": [formatted]}
        else:
            current_clause["lines"].append(formatted)

    flush_clause()
    if current_sec["clauses"]:
        sections.append(current_sec)

    # Build Parent-Child chunks:
    # Group clauses by 2-level parent (e.g. "5.2" groups 5.2, 5.2.1, 5.2.2, 5.2.3) so parent_text always includes
    # sibling notes/exceptions; if section itself is compact (< 2800 chars), attach surrounding context cleanly.
    chunks: list[ParsedChunk] = []
    stage_label = {"presale": "Пресейл", "project": "Проектная деятельность", "both": "Пресейл и Проекты"}[stage]

    for sec in sections:
        groups: dict[str, list[dict]] = {}
        group_order: list[str] = []
        for cl in sec["clauses"]:
            parts = cl["num"].split(".")
            gkey = ".".join(parts[:2]) if len(parts) >= 2 else cl["num"]
            if gkey not in groups:
                groups[gkey] = []
                group_order.append(gkey)
            groups[gkey].append(cl)

        for gkey in group_order:
            g_clauses = groups[gkey]
            parent_body = "\n".join("\n".join(c["lines"]) for c in g_clauses)
            # If parent_body is very small (< 300 chars) and section has neighbors, keep concise header
            header_parent = f"[{dir_label} | {doc_title} | Раздел {sec['num']}. {sec['title']}]"
            full_parent = f"{header_parent}\n{parent_body}"
            # Cap parent size to keep prompts crisp & economical
            if len(full_parent) > 2600:
                full_parent = full_parent[:2600] + "\n..."

            for cl in g_clauses:
                raw_text = "\n".join(cl["lines"]).strip()
                if not raw_text:
                    continue
                header_child = (
                    f"[{dir_label} | {stage_label} | {doc_title} | "
                    f"Раздел {sec['num']}. {sec['title']} | п. {cl['num']}]"
                )
                child_text = f"{header_child}\n{raw_text}"
                uid_src = f"{doc_id}:{sec['num']}:{cl['num']}:{raw_text[:80]}"
                chunk_id = hashlib.md5(uid_src.encode("utf-8")).hexdigest()
                # If a 2-level group is small enough, parent_text = full_parent; if huge, child + neighbors
                p_text = full_parent if len(parent_body) <= 2400 else child_text
                chunks.append(
                    ParsedChunk(
                        chunk_id=chunk_id,
                        doc_id=doc_id,
                        doc_title=doc_title,
                        filename=path.name,
                        directorate=directorate,
                        directorate_label=dir_label,
                        stage=stage,
                        section_num=sec["num"],
                        section_title=sec["title"],
                        clause_num=cl["num"],
                        child_text=child_text,
                        parent_text=p_text,
                        raw_clause_text=raw_text,
                        external_doc_refs=_extract_external_refs(raw_text),
                    )
                )

    return chunks


def parse_directory(docs_dir: str | Path) -> list[ParsedChunk]:
    """Parse all .docx regulations in a directory."""
    d = Path(docs_dir)
    if not d.exists():
        return []
    all_chunks: list[ParsedChunk] = []
    for p in sorted(d.glob("*.docx")):
        if p.name.startswith("~$"):
            continue
        all_chunks.extend(parse_docx(p))
    return all_chunks
