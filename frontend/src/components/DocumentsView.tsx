import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Award,
  BookOpen,
  FileText,
  FileWarning,
  Search,
  Upload,
} from "lucide-react";
import { fetchDocumentDetail, fetchDocuments, uploadDocx } from "../api";
import type { DocDetail, IndexedDoc } from "../types";

interface Props {
  initialDocId?: string | null;
  highlightClause?: string | null;
  onClearJump?: () => void;
}

export const DocumentsView: React.FC<Props> = ({
  initialDocId,
  highlightClause,
  onClearJump,
}) => {
  const [docs, setDocs] = useState<IndexedDoc[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(initialDocId || null);
  const [detail, setDetail] = useState<DocDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const loadList = async () => {
    const list = await fetchDocuments();
    setDocs(list);
    if (!selectedId && list.length > 0) {
      setSelectedId(initialDocId || list[0].doc_id);
    }
  };

  useEffect(() => {
    loadList();
  }, []);

  useEffect(() => {
    if (initialDocId) setSelectedId(initialDocId);
  }, [initialDocId]);

  useEffect(() => {
    if (!selectedId) return;
    setLoading(true);
    fetchDocumentDetail(selectedId)
      .then((d) => {
        setDetail(d);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [selectedId]);

  // Smoothly scroll to highlighted clause when jumping from chat citation!
  useEffect(() => {
    if (!highlightClause || !detail) return;
    const timer = setTimeout(() => {
      const el = document.getElementById(`clause-${highlightClause}`);
      if (el) {
        el.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    }, 120);
    return () => clearTimeout(timer);
  }, [highlightClause, detail]);

  const filteredSections = useMemo(() => {
    if (!detail) return [];
    const q = search.trim().toLowerCase();
    if (!q) return detail.sections;
    return detail.sections
      .map((sec) => ({
        ...sec,
        clauses: sec.clauses.filter(
          (cl) =>
            cl.clause_num.toLowerCase().includes(q) ||
            cl.raw_text.toLowerCase().includes(q) ||
            sec.section_title.toLowerCase().includes(q)
        ),
      }))
      .filter((sec) => sec.clauses.length > 0);
  }, [detail, search]);

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setUploadMsg(null);
    try {
      const res = await uploadDocx(file);
      setUploadMsg(
        `Успешно проиндексирован регламент «${res.doc_title}» (${res.chunk_count} пунктов)`
      );
      await loadList();
      setSelectedId(res.doc_id);
    } catch (err: unknown) {
      setUploadMsg(err instanceof Error ? err.message : "Ошибка загрузки");
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  return (
    <div className="flex-1 flex h-full overflow-hidden bg-[#F6F7FB]">
      {/* Left list of regulations */}
      <aside className="w-80 shrink-0 bg-white border-r border-slate-200/80 flex flex-col h-full">
        <div className="p-4 border-b border-slate-100 flex items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-bold text-slate-900">Реестр регламентов</h2>
            <p className="text-[11px] text-slate-500">Действующие документы (Read-Only)</p>
          </div>
          <input
            ref={fileRef}
            type="file"
            accept=".docx"
            className="hidden"
            onChange={handleUpload}
          />
          <button
            onClick={() => fileRef.current?.click()}
            disabled={uploading}
            title="Загрузить новый регламент .docx в базу знаний"
            className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-slate-900 hover:bg-slate-800 text-white text-[11px] font-semibold transition shrink-0"
          >
            <Upload className="w-3.5 h-3.5" />
            {uploading ? "..." : ".docx"}
          </button>
        </div>

        {uploadMsg && (
          <div className="mx-3 mt-3 p-2.5 rounded-xl bg-emerald-50 border border-emerald-200 text-[11px] text-emerald-800">
            {uploadMsg}
          </div>
        )}

        <div className="p-3 space-y-2 overflow-y-auto flex-1">
          {docs.map((doc) => {
            const active = selectedId === doc.doc_id;
            return (
              <button
                key={doc.doc_id}
                onClick={() => {
                  setSelectedId(doc.doc_id);
                  onClearJump?.();
                }}
                className={`w-full text-left p-3.5 rounded-xl border transition ${
                  active
                    ? "bg-rose-50/70 border-rose-200 shadow-sm"
                    : "bg-white border-slate-200/80 hover:border-slate-300 hover:bg-slate-50/70"
                }`}
              >
                <div className="flex items-center justify-between gap-2 mb-1">
                  <span
                    className={`px-2 py-0.5 rounded-md text-[10px] font-semibold ${
                      active
                        ? "bg-brand-600 text-white"
                        : "bg-slate-100 text-slate-600"
                    }`}
                  >
                    {doc.directorate_label}
                  </span>
                  <span className="text-[11px] font-mono text-slate-400">
                    {doc.chunk_count} пп.
                  </span>
                </div>
                <div className="text-xs font-bold text-slate-900 leading-snug">
                  {doc.doc_title}
                </div>
                <div className="text-[10px] text-slate-400 mt-1 truncate">
                  {doc.filename}
                </div>
              </button>
            );
          })}
        </div>
      </aside>

      {/* Right full-document reader */}
      <main className="flex-1 overflow-y-auto p-6">
        {loading || !detail ? (
          <div className="max-w-4xl mx-auto py-16 text-center text-sm text-slate-400">
            Загрузка структуры регламента...
          </div>
        ) : (
          <div className="max-w-4xl mx-auto space-y-5">
            {/* Document header + live search */}
            <div className="bg-white rounded-2xl border border-slate-200/80 shadow-sm p-5 space-y-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2 text-xs">
                    <span className="px-2.5 py-0.5 rounded-full bg-rose-50 text-brand-700 border border-rose-200 font-semibold">
                      {detail.directorate_label}
                    </span>
                    <span className="text-slate-400">·</span>
                    <span className="text-slate-500">{detail.sections.length} разделов</span>
                    <span className="text-slate-400">·</span>
                    <span className="text-slate-500">{detail.chunk_count} пунктов</span>
                  </div>
                  <h1 className="text-xl font-bold text-slate-900 mt-2">{detail.doc_title}</h1>
                </div>

                <div className="relative w-full sm:w-72">
                  <Search className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
                  <input
                    type="text"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="Поиск по тексту или № пункта..."
                    className="w-full pl-9 pr-3 py-2 rounded-xl bg-slate-50 border border-slate-200 text-xs focus:outline-none focus:bg-white focus:border-brand-500"
                  />
                </div>
              </div>

              {/* Section quick-jump pills */}
              <div className="flex flex-wrap gap-1.5 pt-2 border-t border-slate-100">
                {detail.sections.map((s) => (
                  <a
                    key={s.section_num}
                    href={`#sec-${s.section_num}`}
                    className="px-2 py-1 rounded-lg bg-slate-50 hover:bg-rose-50 text-[11px] text-slate-600 hover:text-brand-700 border border-slate-200/70 transition"
                  >
                    {s.section_num}. {s.section_title.slice(0, 32)}
                    {s.section_title.length > 32 ? "…" : ""}
                  </a>
                ))}
              </div>
            </div>

            {/* Linked verified Quality FAQ clarifications */}
            {detail.linked_faqs && detail.linked_faqs.length > 0 && (
              <div className="bg-emerald-50/70 rounded-2xl border border-emerald-200 p-4 space-y-2.5">
                <div className="text-xs font-bold text-emerald-900 flex items-center gap-1.5">
                  <Award className="w-4 h-4 text-emerald-600" />
                  Связанные проверенные разъяснения Службы качества ({detail.linked_faqs.length})
                </div>
                {detail.linked_faqs.map((f) => (
                  <div key={f.id} className="p-3 rounded-xl bg-white border border-emerald-200/80 text-xs space-y-1">
                    <div className="font-semibold text-slate-900">
                      <span className="font-mono text-emerald-700 mr-1.5">[{f.ticket_code}]</span>
                      {f.question}
                    </div>
                    <div className="text-slate-600">{f.answer}</div>
                  </div>
                ))}
              </div>
            )}

            {/* Sections & clauses */}
            <div className="space-y-4">
              {filteredSections.map((sec) => (
                <section
                  key={sec.section_num}
                  id={`sec-${sec.section_num}`}
                  className="bg-white rounded-2xl border border-slate-200/80 shadow-sm overflow-hidden"
                >
                  <div className="px-5 py-3.5 bg-slate-50/90 border-b border-slate-100 flex items-center gap-2">
                    <BookOpen className="w-4 h-4 text-brand-600 shrink-0" />
                    <h2 className="text-sm font-bold text-slate-900">
                      Раздел {sec.section_num}. {sec.section_title}
                    </h2>
                  </div>
                  <div className="divide-y divide-slate-100">
                    {sec.clauses.map((cl) => {
                      const isHi = highlightClause === cl.clause_num;
                      return (
                        <div
                          key={cl.chunk_id}
                          id={`clause-${cl.clause_num}`}
                          className={`p-4 transition-colors ${
                            isHi
                              ? "bg-rose-50/90 ring-2 ring-inset ring-brand-500/70"
                              : "hover:bg-slate-50/50"
                          }`}
                        >
                          <div className="flex items-center justify-between gap-2 mb-1.5">
                            <span
                              className={`px-2 py-0.5 rounded-md font-mono text-[11px] font-bold ${
                                isHi
                                  ? "bg-brand-600 text-white"
                                  : "bg-slate-100 text-slate-700"
                              }`}
                            >
                              п. {cl.clause_num}
                            </span>
                            {isHi && (
                              <span className="text-[11px] font-semibold text-brand-700">
                                Цитируемый пункт из ответа агента
                              </span>
                            )}
                          </div>
                          <div className="text-xs text-slate-800 whitespace-pre-wrap leading-relaxed">
                            {cl.raw_text}
                          </div>
                          {cl.external_doc_refs?.length > 0 && (
                            <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[10px] text-amber-800">
                              <FileWarning className="w-3 h-3 text-amber-600" />
                              <span>Внешняя ссылка:</span>
                              {cl.external_doc_refs.map((r, ri) => (
                                <span
                                  key={ri}
                                  className="px-1.5 py-0.5 rounded bg-amber-50 border border-amber-200"
                                >
                                  {r}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </section>
              ))}
              {filteredSections.length === 0 && (
                <div className="bg-white rounded-2xl border border-slate-200 p-10 text-center text-xs text-slate-400">
                  <FileText className="w-6 h-6 mx-auto mb-2 text-slate-300" />
                  Ничего не найдено по запросу «{search}»
                </div>
              )}
            </div>
          </div>
        )}
      </main>
    </div>
  );
};
