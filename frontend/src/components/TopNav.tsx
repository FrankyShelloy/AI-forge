import React from "react";
import {
  BarChart3,
  BookOpen,
  ExternalLink,
  KeyRound,
  Mail,
  MessageSquareText,
  Plus,
  RefreshCw,
  ShieldCheck,
  UserCheck,
} from "lucide-react";
import type { HealthInfo } from "../types";

export type ActiveTab = "chat" | "docs" | "quality";

interface Props {
  activeTab: ActiveTab;
  onSelectTab: (tab: ActiveTab) => void;
  onSyncMail: () => void;
  syncingMail: boolean;
  onOpenSecurity: () => void;
  isQualityRole: boolean;
  onOpenRoleModal: () => void;
  onNewSession: () => void;
  health: HealthInfo | null;
}

export const TopNav: React.FC<Props> = ({
  activeTab,
  onSelectTab,
  onSyncMail,
  syncingMail,
  onOpenSecurity,
  isQualityRole,
  onOpenRoleModal,
  onNewSession,
  health,
}) => {
  const tabs: Array<{ id: ActiveTab; label: string; icon: React.ReactNode }> = [
    {
      id: "chat",
      label: "Чат-ассистент",
      icon: <MessageSquareText className="w-4 h-4" />,
    },
    {
      id: "docs",
      label: "База регламентов",
      icon: <BookOpen className="w-4 h-4" />,
    },
    {
      id: "quality",
      label: "Служба качества",
      icon: <BarChart3 className="w-4 h-4" />,
    },
  ];

  return (
    <header className="h-[60px] shrink-0 bg-white/95 backdrop-blur-md border-b border-slate-200/80 px-3 lg:px-6 flex items-center justify-between gap-2.5 z-20">
      {/* Brand lockup + mobile New Chat */}
      <div className="flex items-center gap-2.5 min-w-0">
        <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-brand-600 to-rose-700 text-white font-bold text-sm flex items-center justify-center shadow-sm shadow-brand-600/20 shrink-0">
          SL
        </div>
        <div className="leading-tight hidden sm:block">
          <div className="flex items-center gap-2">
            <span className="font-bold text-[15px] tracking-tight text-slate-900">
              Софтлайн · Регламенты AI
            </span>
            <span className="px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide rounded-md bg-rose-50 text-brand-700 border border-rose-200/80">
              ИИКузница
            </span>
          </div>
          <div className="text-[11px] text-slate-500">
            Интеллектуальная база знаний Службы качества
          </div>
        </div>

        {activeTab === "chat" && (
          <button
            onClick={onNewSession}
            title="Новый приватный диалог"
            className="md:hidden inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-brand-600 text-white text-xs font-semibold shadow-sm"
          >
            <Plus className="w-3.5 h-3.5" />
            <span>Диалог</span>
          </button>
        )}
      </div>

      {/* Center segmented nav */}
      <nav className="flex items-center gap-1 bg-slate-100/90 p-1 rounded-xl border border-slate-200/70">
        {tabs.map((t) => {
          const active = activeTab === t.id;
          return (
            <button
              key={t.id}
              onClick={() => onSelectTab(t.id)}
              className={`flex items-center gap-1.5 px-2.5 sm:px-3.5 py-1.5 rounded-lg text-xs font-semibold transition-all ${
                active
                  ? "bg-white text-slate-900 shadow-sm ring-1 ring-slate-200/80"
                  : "text-slate-600 hover:text-slate-900"
              }`}
            >
              <span className={active ? "text-brand-600" : "text-slate-400"}>
                {t.icon}
              </span>
              <span className="hidden xs:inline sm:inline">{t.label}</span>
              {t.id === "docs" && health?.docs_indexed ? (
                <span className="ml-0.5 text-[10px] px-1.5 py-[1px] rounded-full bg-slate-100 text-slate-600 hidden sm:inline">
                  {health.docs_indexed}
                </span>
              ) : null}
            </button>
          );
        })}
      </nav>

      {/* Right utility actions */}
      <div className="flex items-center gap-1.5 sm:gap-2">
        {/* RBAC Role Switcher */}
        <button
          onClick={onOpenRoleModal}
          title="Модель прав доступа: Сотрудник (чтение) / Служба качества (управление БЗ и FAQ)"
          className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border text-xs font-semibold transition ${
            isQualityRole
              ? "bg-indigo-50 text-indigo-800 border-indigo-200 hover:bg-indigo-100"
              : "bg-slate-50 text-slate-700 border-slate-200 hover:bg-slate-100"
          }`}
        >
          {isQualityRole ? (
            <>
              <UserCheck className="w-3.5 h-3.5 text-indigo-600" />
              <span className="hidden lg:inline">Роль: Качество</span>
            </>
          ) : (
            <>
              <KeyRound className="w-3.5 h-3.5 text-slate-500" />
              <span className="hidden lg:inline">Роль: Сотрудник</span>
            </>
          )}
        </button>

        <button
          onClick={onOpenSecurity}
          title="Контур безопасности, Open-Weight модели Apache 2.0 и DION API"
          className="hidden xl:inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-emerald-50/90 hover:bg-emerald-100/80 text-emerald-800 border border-emerald-200/80 text-xs font-medium transition"
        >
          <ShieldCheck className="w-3.5 h-3.5 text-emerald-600" />
          <span>Apache 2.0 · Контур</span>
        </button>

        <button
          onClick={onSyncMail}
          disabled={syncingMail}
          title="Проверить входящие ответы Службы качества по IMAP прямо сейчас"
          className="inline-flex items-center gap-1.5 px-2.5 sm:px-3 py-1.5 rounded-lg bg-slate-900 hover:bg-slate-800 disabled:opacity-60 text-white text-xs font-medium shadow-sm transition"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${syncingMail ? "animate-spin" : ""}`} />
          <span className="hidden 2xl:inline">Синхр. почты</span>
        </button>

        <a
          href="/webmail/"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1.5 px-2.5 sm:px-3 py-1.5 rounded-lg bg-white hover:bg-slate-50 text-slate-700 border border-slate-200 text-xs font-medium shadow-sm transition"
        >
          <Mail className="w-3.5 h-3.5 text-brand-600" />
          <span className="hidden lg:inline">Веб-почта</span>
          <ExternalLink className="w-3 h-3 text-slate-400" />
        </a>
      </div>
    </header>
  );
};
