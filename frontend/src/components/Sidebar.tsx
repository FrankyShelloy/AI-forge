import React from "react";
import {
  Mail,
  MessageSquare,
  Plus,
  Trash2,
} from "lucide-react";
import type { HealthInfo, SessionInfo } from "../types";

interface Props {
  sessions: SessionInfo[];
  currentSessionId: string | null;
  onSelectSession: (id: string) => void;
  onNewSession: () => void;
  onDeleteSession: (id: string) => void;
  userEmail: string;
  onChangeUserEmail: (email: string) => void;
  health: HealthInfo | null;
}

export const Sidebar: React.FC<Props> = ({
  sessions,
  currentSessionId,
  onSelectSession,
  onNewSession,
  onDeleteSession,
  userEmail,
  onChangeUserEmail,
  health,
}) => {
  return (
    <aside className="w-[272px] shrink-0 bg-white border-r border-slate-200/80 flex flex-col h-full hidden md:flex">
      {/* Primary action */}
      <div className="p-3.5 border-b border-slate-100">
        <button
          onClick={onNewSession}
          className="w-full flex items-center justify-center gap-2 py-2.5 px-3.5 rounded-xl bg-gradient-to-r from-brand-600 to-rose-600 hover:from-brand-700 hover:to-rose-700 text-xs font-semibold text-white shadow-sm shadow-brand-600/20 transition"
        >
          <Plus className="w-4 h-4" />
          Новый приватный диалог
        </button>
      </div>

      {/* Sessions list */}
      <div className="p-3 flex-1 overflow-y-auto space-y-1">
        <div className="px-2 pb-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
          Приватные диалоги (1-на-1)
        </div>

        {sessions.length === 0 ? (
          <div className="px-2.5 py-6 text-center rounded-xl border border-dashed border-slate-200 bg-slate-50/60">
            <MessageSquare className="w-5 h-5 text-slate-300 mx-auto mb-1.5" />
            <div className="text-[11px] text-slate-400 leading-snug">
              История диалогов появится после первого вопроса
            </div>
          </div>
        ) : (
          sessions.map((s) => {
            const active = currentSessionId === s.id;
            return (
              <div
                key={s.id}
                onClick={() => onSelectSession(s.id)}
                className={`group relative flex items-center justify-between gap-2 px-3 py-2.5 rounded-xl text-xs cursor-pointer transition ${
                  active
                    ? "bg-rose-50/80 text-slate-900 font-medium ring-1 ring-rose-200/70"
                    : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"
                }`}
              >
                {active && (
                  <span className="absolute left-0 top-2 bottom-2 w-1 rounded-r-full bg-brand-600" />
                )}
                <div className="flex items-center gap-2 min-w-0">
                  <MessageSquare
                    className={`w-3.5 h-3.5 shrink-0 ${
                      active ? "text-brand-600" : "text-slate-400"
                    }`}
                  />
                  <span className="truncate">{s.title}</span>
                </div>
                <button
                  title="Удалить диалог"
                  onClick={(e) => {
                    e.stopPropagation();
                    onDeleteSession(s.id);
                  }}
                  className="opacity-0 group-hover:opacity-100 text-slate-400 hover:text-brand-600 p-1 rounded-md hover:bg-white transition shrink-0"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            );
          })
        )}
      </div>

      {/* Bottom employee profile + corpus pill */}
      <div className="p-3.5 border-t border-slate-100 bg-slate-50/60 space-y-3">
        <div>
          <label className="flex items-center gap-1.5 text-[11px] font-medium text-slate-500 mb-1.5">
            <Mail className="w-3 h-3 text-slate-400" />
            Email сотрудника (копия CC):
          </label>
          <input
            type="email"
            value={userEmail}
            onChange={(e) => onChangeUserEmail(e.target.value)}
            className="w-full px-2.5 py-1.5 rounded-lg bg-white border border-slate-200 text-xs text-slate-800 focus:outline-none focus:border-brand-500 focus:ring-2 focus:ring-rose-100 transition"
            placeholder="employee@corp.local"
          />
        </div>

        {health && (
          <div className="p-2.5 rounded-xl bg-white border border-slate-200/80 text-[11px] text-slate-500 flex items-center justify-between">
            <div>
              <div className="font-semibold text-slate-700">
                {health.docs_indexed} регламента · {health.chunks_indexed ?? "…"} пп.
              </div>
              <div className="text-[10px] text-slate-400 mt-0.5">
                Проверенных FAQ: <span className="text-emerald-600 font-semibold">{health.faq_active}</span>
              </div>
            </div>
            <span className="w-2 h-2 rounded-full bg-emerald-500 shrink-0" title="БЗ онлайн" />
          </div>
        )}
      </div>
    </aside>
  );
};
