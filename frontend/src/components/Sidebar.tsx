import React from "react";
import {
  BarChart3,
  ExternalLink,
  Mail,
  MessageSquare,
  Plus,
  RefreshCw,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import type { HealthInfo, SessionInfo } from "../types";

interface Props {
  activeTab: "chat" | "quality";
  onSelectTab: (tab: "chat" | "quality") => void;
  sessions: SessionInfo[];
  currentSessionId: string | null;
  onSelectSession: (id: string) => void;
  onNewSession: () => void;
  onDeleteSession: (id: string) => void;
  userEmail: string;
  onChangeUserEmail: (email: string) => void;
  onSyncMail: () => void;
  syncingMail: boolean;
  syncNotice: string | null;
  health: HealthInfo | null;
}

export const Sidebar: React.FC<Props> = ({
  activeTab,
  onSelectTab,
  sessions,
  currentSessionId,
  onSelectSession,
  onNewSession,
  onDeleteSession,
  userEmail,
  onChangeUserEmail,
  onSyncMail,
  syncingMail,
  syncNotice,
  health,
}) => {
  return (
    <aside className="w-72 shrink-0 bg-slate-900 text-slate-100 flex flex-col border-r border-slate-800 h-full">
      {/* Header */}
      <div className="p-4 border-b border-slate-800">
        <div className="flex items-center gap-2.5">
          <div className="w-9 h-9 rounded-lg bg-brand-600 flex items-center justify-center font-bold text-white shadow">
            SL
          </div>
          <div>
            <div className="font-semibold text-sm leading-tight">
              Софтлайн · Регламенты AI
            </div>
            <div className="text-[11px] text-slate-400 flex items-center gap-1 mt-0.5">
              <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />
              Служба качества · 1-на-1
            </div>
          </div>
        </div>

        {/* Nav tabs */}
        <div className="mt-4 grid grid-cols-2 gap-1.5 bg-slate-800/80 p-1 rounded-lg">
          <button
            onClick={() => onSelectTab("chat")}
            className={`flex items-center justify-center gap-1.5 py-1.5 px-2 rounded-md text-xs font-medium transition ${
              activeTab === "chat"
                ? "bg-brand-600 text-white shadow"
                : "text-slate-300 hover:text-white"
            }`}
          >
            <MessageSquare className="w-3.5 h-3.5" />
            Чат
          </button>
          <button
            onClick={() => onSelectTab("quality")}
            className={`flex items-center justify-center gap-1.5 py-1.5 px-2 rounded-md text-xs font-medium transition ${
              activeTab === "quality"
                ? "bg-brand-600 text-white shadow"
                : "text-slate-300 hover:text-white"
            }`}
          >
            <BarChart3 className="w-3.5 h-3.5" />
            Качество
          </button>
        </div>
      </div>

      {/* New dialog + sessions */}
      <div className="p-3 flex-1 overflow-y-auto flex flex-col gap-2">
        <button
          onClick={() => {
            onSelectTab("chat");
            onNewSession();
          }}
          className="w-full flex items-center justify-center gap-2 py-2 px-3 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs font-medium text-white border border-slate-700 transition"
        >
          <Plus className="w-4 h-4" />
          Новый приватный диалог
        </button>

        <div className="text-[11px] uppercase tracking-wider text-slate-500 font-semibold mt-2 px-1">
          История диалогов
        </div>
        <div className="space-y-1 flex-1 overflow-y-auto">
          {sessions.length === 0 && (
            <div className="text-xs text-slate-500 px-2 py-3">
              Пока нет диалогов. Задайте первый вопрос по регламентам.
            </div>
          )}
          {sessions.map((s) => (
            <div
              key={s.id}
              className={`group flex items-center justify-between gap-1 px-2.5 py-2 rounded-lg text-xs cursor-pointer transition ${
                currentSessionId === s.id && activeTab === "chat"
                  ? "bg-slate-800 text-white border border-slate-700"
                  : "text-slate-300 hover:bg-slate-800/50"
              }`}
              onClick={() => {
                onSelectTab("chat");
                onSelectSession(s.id);
              }}
            >
              <span className="truncate">{s.title}</span>
              <button
                title="Удалить диалог"
                onClick={(e) => {
                  e.stopPropagation();
                  onDeleteSession(s.id);
                }}
                className="opacity-0 group-hover:opacity-100 text-slate-400 hover:text-rose-400 p-0.5"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          ))}
        </div>
      </div>

      {/* Footer: Employee email + Mail HITL controls */}
      <div className="p-3 border-t border-slate-800 space-y-2.5 bg-slate-950/40">
        <div>
          <label className="block text-[11px] text-slate-400 mb-1">
            Email сотрудника (для копии CC):
          </label>
          <input
            type="email"
            value={userEmail}
            onChange={(e) => onChangeUserEmail(e.target.value)}
            className="w-full px-2.5 py-1.5 rounded-md bg-slate-800 border border-slate-700 text-xs text-white focus:outline-none focus:border-brand-500"
            placeholder="employee@corp.local"
          />
        </div>

        <button
          onClick={onSyncMail}
          disabled={syncingMail}
          className="w-full flex items-center justify-center gap-1.5 py-1.5 px-2.5 rounded-md bg-emerald-600/90 hover:bg-emerald-600 disabled:opacity-60 text-xs font-medium text-white transition"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${syncingMail ? "animate-spin" : ""}`} />
          Синхронизировать почту сейчас
        </button>

        {syncNotice && (
          <div className="text-[11px] text-emerald-300 bg-emerald-950/60 border border-emerald-800/60 rounded px-2 py-1">
            {syncNotice}
          </div>
        )}

        <a
          href="/webmail/"
          target="_blank"
          rel="noopener noreferrer"
          className="w-full flex items-center justify-center gap-1.5 py-1.5 px-2.5 rounded-md bg-slate-800 hover:bg-slate-700 text-xs text-slate-200 border border-slate-700 transition"
        >
          <Mail className="w-3.5 h-3.5 text-brand-500" />
          Открыть веб-почту (Roundcube)
          <ExternalLink className="w-3 h-3 opacity-70" />
        </a>

        {health && (
          <div className="text-[10px] text-slate-500 pt-1 leading-tight">
            Модель: <span className="text-slate-400">{health.llm_model}</span>
            <br />
            Регламентов: {health.docs_indexed} · FAQ: {health.faq_active}
          </div>
        )}
      </div>
    </aside>
  );
};
