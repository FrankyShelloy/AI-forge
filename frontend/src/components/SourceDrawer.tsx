import React from "react";
import { ArrowUpRight, Award, BookOpen, FileWarning, X } from "lucide-react";
import type { Citation } from "../types";

interface Props {
  citation: Citation | null;
  onClose: () => void;
  onOpenFullDocument: (docId: string, clauseNum: string) => void;
}

export const SourceDrawer: React.FC<Props> = ({
  citation,
  onClose,
  onOpenFullDocument,
}) => {
  if (!citation) return null;
  const isFaq = citation.source_type === "quality_faq";
  const pct = Math.max(10, Math.min(100, Math.round(citation.score * 100)));

  return (
    <aside className="fixed inset-y-0 right-0 lg:static w-full sm:w-[360px] xl:w-[400px] shrink-0 bg-white border-l border-slate-200/90 h-full flex flex-col shadow-2xl lg:shadow-xl z-30 lg:z-10 animate-fadeSlideUp">
      {/* Header */}
      <div className="p-5 border-b border-slate-100 bg-gradient-to-b from-slate-50/90 to-white">
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2">
            <span
              className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold ${
                isFaq
                  ? "bg-emerald-50 text-emerald-800 border border-emerald-200"
                  : "bg-rose-50 text-brand-700 border border-rose-200"
              }`}
            >
              {isFaq ? (
                <>
                  <Award className="w-3.5 h-3.5" /> Проверено Службой качества
                </>
              ) : (
                <>
                  <BookOpen className="w-3.5 h-3.5" /> Официальный регламент
                </>
              )}
            </span>
            <span className="px-2 py-0.5 rounded-md bg-slate-100 text-slate-700 font-mono text-xs font-bold">
              {citation.tag}
            </span>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 text-slate-400 hover:text-slate-700 rounded-lg hover:bg-slate-100 transition"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <h3 className="font-bold text-[15px] text-slate-900 mt-3 leading-snug">
          {citation.doc_title}
        </h3>
        <div className="text-xs text-slate-500 mt-1 flex flex-wrap items-center gap-1.5">
          <span className="font-medium text-slate-700">{citation.directorate_label}</span>
          <span>·</span>
          <span>
            Раздел {citation.section_num}. {citation.section_title}
          </span>
          <span>·</span>
          <span className="font-semibold text-brand-700">п. {citation.clause_num}</span>
        </div>

        {/* Relevance bar */}
        <div className="mt-3 flex items-center gap-2.5">
          <span className="text-[11px] text-slate-400">Релевантность:</span>
          <div className="flex-1 h-1.5 bg-slate-100 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full ${
                isFaq ? "bg-emerald-500" : "bg-brand-600"
              }`}
              style={{ width: `${pct}%` }}
            />
          </div>
          <span className="text-[11px] font-mono font-semibold text-slate-600">{pct}%</span>
        </div>
      </div>

      {/* Body */}
      <div className="p-5 overflow-y-auto flex-1 space-y-4">
        {isFaq && citation.verified_by && (
          <div className="p-3.5 rounded-xl bg-emerald-50/70 border border-emerald-200 text-xs text-emerald-900 space-y-1">
            <div>
              <strong>Тикет обращения:</strong>{" "}
              <span className="font-mono">{citation.ticket_code}</span>
            </div>
            <div>
              <strong>Уполномоченный эксперт:</strong> {citation.verified_by}
            </div>
            <div className="text-[11px] text-emerald-700 pt-1">
              Статус: проверенное разъяснение (дополняет регламент; официальный регламент имеет высший приоритет).
            </div>
          </div>
        )}

        <div>
          <div className="text-[11px] uppercase tracking-wider font-semibold text-slate-400 mb-2">
            Точный текст пункта ({citation.clause_num})
          </div>
          <div
            className={`p-4 rounded-xl border text-xs text-slate-800 whitespace-pre-wrap leading-relaxed ${
              isFaq
                ? "bg-emerald-50/30 border-emerald-200/80"
                : "bg-rose-50/25 border-rose-200/70"
            }`}
          >
            {citation.excerpt}
          </div>
        </div>

        {citation.parent_context && citation.parent_context !== citation.excerpt && (
          <div>
            <div className="text-[11px] uppercase tracking-wider font-semibold text-slate-400 mb-2">
              Контекст родительского блока (Parent-Child RAG)
            </div>
            <div className="p-4 rounded-xl bg-slate-50 border border-slate-200/80 text-xs text-slate-600 whitespace-pre-wrap leading-relaxed">
              {citation.parent_context}
            </div>
          </div>
        )}

        {citation.external_doc_refs && citation.external_doc_refs.length > 0 && (
          <div className="p-3.5 rounded-xl bg-amber-50 border border-amber-200 text-xs text-amber-900">
            <div className="font-semibold flex items-center gap-1.5 mb-1.5">
              <FileWarning className="w-4 h-4 text-amber-600" />
              Упомянутые внешние документы
            </div>
            <ul className="list-disc ml-4 space-y-0.5 text-[11px]">
              {citation.external_doc_refs.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {/* Footer: Deep-link to full regulation viewer */}
      {!isFaq && (
        <div className="p-4 border-t border-slate-100 bg-slate-50/60">
          <button
            onClick={() => onOpenFullDocument(citation.doc_id, citation.clause_num)}
            className="w-full py-2.5 px-4 rounded-xl bg-slate-900 hover:bg-slate-800 text-white text-xs font-semibold inline-flex items-center justify-center gap-1.5 shadow-sm transition"
          >
            Открыть полный регламент на п. {citation.clause_num}
            <ArrowUpRight className="w-4 h-4" />
          </button>
        </div>
      )}
    </aside>
  );
};
