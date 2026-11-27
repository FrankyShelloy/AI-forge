import React from "react";
import { Award, BookOpen, FileWarning, X } from "lucide-react";
import type { Citation } from "../types";

interface Props {
  citation: Citation | null;
  onClose: () => void;
}

export const SourceDrawer: React.FC<Props> = ({ citation, onClose }) => {
  if (!citation) return null;
  const isFaq = citation.source_type === "quality_faq";

  return (
    <div className="w-96 shrink-0 bg-white border-l border-slate-200 h-full flex flex-col shadow-lg">
      <div className="p-4 border-b border-slate-200 flex items-start justify-between gap-2 bg-slate-50">
        <div>
          <div className="flex items-center gap-2">
            <span
              className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold ${
                isFaq
                  ? "bg-emerald-100 text-emerald-800"
                  : "bg-blue-100 text-blue-800"
              }`}
            >
              {isFaq ? (
                <>
                  <Award className="w-3 h-3" /> Проверено Службой качества
                </>
              ) : (
                <>
                  <BookOpen className="w-3 h-3" /> Официальный регламент
                </>
              )}
            </span>
            <span className="text-xs font-mono font-semibold text-slate-600">
              [{citation.tag}]
            </span>
          </div>
          <h3 className="font-semibold text-sm text-slate-900 mt-2 leading-snug">
            {citation.doc_title}
          </h3>
          <div className="text-xs text-slate-500 mt-0.5">
            {citation.directorate_label} · Раздел {citation.section_num}.{" "}
            {citation.section_title} · п. {citation.clause_num}
          </div>
        </div>
        <button
          onClick={onClose}
          className="p-1 text-slate-400 hover:text-slate-700 rounded-md hover:bg-slate-200/60"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      <div className="p-4 overflow-y-auto flex-1 space-y-4">
        {isFaq && citation.verified_by && (
          <div className="p-3 rounded-lg bg-emerald-50 border border-emerald-200 text-xs text-emerald-900 space-y-1">
            <div>
              <strong>Тикет:</strong> {citation.ticket_code}
            </div>
            <div>
              <strong>Уполномоченный эксперт:</strong> {citation.verified_by}
            </div>
            <div className="text-[11px] text-emerald-700">
              Статус: подтверждённое разъяснение (приоритет ниже официального регламента при расхождениях).
            </div>
          </div>
        )}

        <div>
          <div className="text-[11px] uppercase tracking-wider font-semibold text-slate-400 mb-1.5">
            Точный текст пункта ({citation.clause_num})
          </div>
          <div className="p-3.5 rounded-lg bg-slate-50 border border-slate-200 text-xs text-slate-800 whitespace-pre-wrap leading-relaxed">
            {citation.excerpt}
          </div>
        </div>

        {citation.parent_context && citation.parent_context !== citation.excerpt && (
          <div>
            <div className="text-[11px] uppercase tracking-wider font-semibold text-slate-400 mb-1.5">
              Полный контекст родительского блока
            </div>
            <div className="p-3.5 rounded-lg bg-white border border-slate-200 text-xs text-slate-600 whitespace-pre-wrap leading-relaxed">
              {citation.parent_context}
            </div>
          </div>
        )}

        {citation.external_doc_refs && citation.external_doc_refs.length > 0 && (
          <div className="p-3 rounded-lg bg-amber-50 border border-amber-200 text-xs text-amber-900">
            <div className="font-semibold flex items-center gap-1.5 mb-1">
              <FileWarning className="w-3.5 h-3.5" />
              Упомянутые внешние документы
            </div>
            <ul className="list-disc ml-4 space-y-0.5 text-[11px]">
              {citation.external_doc_refs.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          </div>
        )}

        <div className="text-[11px] text-slate-400 pt-2 border-t border-slate-100">
          Релевантность гибридного поиска: {(citation.score * 100).toFixed(1)}%
        </div>
      </div>
    </div>
  );
};
