import React, { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  AlertTriangle,
  Award,
  BookOpen,
  Clock,
  ExternalLink,
  Mail,
  RefreshCw,
  Send,
  Sparkles,
} from "lucide-react";
import type { ChatMessage, Citation } from "../types";

const QUICK_PROMPTS = [
  {
    label: "S2: Ловушка (бюджетная оценка + срок)",
    text: "Можно ли использовать бюджетную оценку для подачи в конкурс и за сколько дней нужно инициировать расчёт?",
  },
  {
    label: "S1: Нюанс SLA и приоритет (ИБ)",
    text: "Меняется ли приоритет пресейловой заявки в Дирекции ИБ при нарушении SLA?",
  },
  {
    label: "Edge: Вне регламентов (премия)",
    text: "Какую премию я получу за закрытие этой сделки?",
  },
  {
    label: "S3: Эскалация в Качество (такси в выходной)",
    text: "Как согласовать и компенсировать расходы на такси при выезде проектной команды к заказчику в выходной день в ДБРЦТ?",
  },
];

interface Props {
  messages: ChatMessage[];
  streamingText: string;
  streamingStatus: string | null;
  isSending: boolean;
  onSend: (text: string) => void;
  onSelectCitation: (c: Citation) => void;
  onSyncMail: () => void;
  syncingMail: boolean;
}

export const ChatView: React.FC<Props> = ({
  messages,
  streamingText,
  streamingStatus,
  isSending,
  onSend,
  onSelectCitation,
  onSyncMail,
  syncingMail,
}) => {
  const [input, setInput] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamingText]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isSending) return;
    const text = input.trim();
    setInput("");
    onSend(text);
  };

  return (
    <div className="flex-1 flex flex-col h-full min-w-0">
      {/* Top bar */}
      <header className="px-6 py-3.5 bg-white border-b border-slate-200 flex items-center justify-between">
        <div>
          <h1 className="text-base font-semibold text-slate-900">
            Приватный чат с ИИ-агентом по регламентам
          </h1>
          <p className="text-xs text-slate-500">
            Поиск строго по действующим регламентам (ДБРЦТ, Техдирекция, Дирекция ИБ) и проверенным разъяснениям Службы качества
          </p>
        </div>
        <span className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200 font-medium">
          <span className="w-2 h-2 rounded-full bg-emerald-500" />
          Zero-Hallucination Mode
        </span>
      </header>

      {/* Messages area */}
      <div className="flex-1 overflow-y-auto px-6 py-5 space-y-5">
        {messages.length === 0 && !isSending && (
          <div className="max-w-3xl mx-auto mt-8 text-center">
            <div className="w-12 h-12 rounded-2xl bg-brand-50 border border-brand-100 text-brand-600 flex items-center justify-center mx-auto mb-3">
              <Sparkles className="w-6 h-6" />
            </div>
            <h2 className="text-lg font-semibold text-slate-900">
              Задайте вопрос по корпоративным регламентам
            </h2>
            <p className="text-sm text-slate-500 mt-1 max-w-xl mx-auto">
              Агент различает правила ДБРЦТ, Технической дирекции и Дирекции ИБ, задаёт уточняющие вопросы при неоднозначности и подключает Службу качества по почте, если правила нет в базе.
            </p>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 mt-6 text-left">
              {QUICK_PROMPTS.map((q, idx) => (
                <button
                  key={idx}
                  onClick={() => onSend(q.text)}
                  className="p-3.5 rounded-xl bg-white border border-slate-200 hover:border-brand-500 hover:shadow-sm transition group"
                >
                  <div className="text-[11px] font-semibold text-brand-600 mb-1">
                    {q.label}
                  </div>
                  <div className="text-xs text-slate-700 group-hover:text-slate-900 leading-relaxed">
                    {q.text}
                  </div>
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, idx) => {
          const isUser = m.role === "user";
          const isLastAssistant = !isUser && idx === messages.length - 1;
          return (
            <div
              key={m.id || idx}
              className={`max-w-3xl mx-auto flex ${isUser ? "justify-end" : "justify-start"}`}
            >
              <div
                className={`rounded-2xl px-4 py-3 text-sm leading-relaxed ${
                  isUser
                    ? "bg-slate-900 text-white max-w-[80%]"
                    : "bg-white border border-slate-200 shadow-sm w-full"
                }`}
              >
                {/* Outcome badge */}
                {!isUser && m.outcome && (
                  <div className="flex flex-wrap items-center gap-2 mb-2">
                    {m.outcome === "faq_answered" && (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-100 text-emerald-800">
                        <Award className="w-3 h-3" /> Ответ из проверенной БЗ Службы качества (FAQ)
                      </span>
                    )}
                    {m.outcome === "faq_updated" && (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-600 text-white">
                        <Mail className="w-3 h-3" /> Новое разъяснение получено по почте
                      </span>
                    )}
                    {m.outcome === "complex_answered" && (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold bg-indigo-100 text-indigo-800">
                        Комплексный проект (раздельно по дирекциям)
                      </span>
                    )}
                    {m.outcome === "clarify" && (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold bg-amber-100 text-amber-800">
                        Требуется уточнение контекста
                      </span>
                    )}
                    {m.outcome === "escalated" && (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold bg-rose-100 text-rose-800">
                        <AlertTriangle className="w-3 h-3" /> Передано в Службу качества ({m.ticket_code})
                      </span>
                    )}
                  </div>
                )}

                {/* Markdown text */}
                <div className={isUser ? "" : "markdown-body text-slate-800"}>
                  {isUser ? m.content : <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>}
                </div>

                {/* Clickable clarification buttons */}
                {!isUser && isLastAssistant && m.clarification_options?.length > 0 && !isSending && (
                  <div className="mt-3 pt-3 border-t border-slate-100 flex flex-wrap gap-2">
                    {m.clarification_options.map((opt, oi) => (
                      <button
                        key={oi}
                        onClick={() => onSend(opt)}
                        className="px-3 py-1.5 rounded-lg text-xs font-medium bg-brand-50 text-brand-700 border border-brand-200 hover:bg-brand-600 hover:text-white transition"
                      >
                        {opt}
                      </button>
                    ))}
                  </div>
                )}

                {/* Escalation action strip */}
                {!isUser && m.outcome === "escalated" && (
                  <div className="mt-3 pt-3 border-t border-slate-100 flex flex-wrap items-center gap-2">
                    <a
                      href="/webmail/"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-900 text-white hover:bg-slate-800 transition"
                    >
                      <Mail className="w-3.5 h-3.5" />
                      Открыть веб-почту и ответить от Качества
                      <ExternalLink className="w-3 h-3" />
                    </a>
                    <button
                      onClick={onSyncMail}
                      disabled={syncingMail}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-60 transition"
                    >
                      <RefreshCw className={`w-3.5 h-3.5 ${syncingMail ? "animate-spin" : ""}`} />
                      Синхронизировать почту сейчас
                    </button>
                  </div>
                )}

                {/* Clickable citations strip */}
                {!isUser && m.citations?.length > 0 && (
                  <div className="mt-3 pt-3 border-t border-slate-100">
                    <div className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-1.5">
                      Источники (нажмите, чтобы открыть текст пункта)
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {m.citations.map((c, ci) => {
                        const isFaq = c.source_type === "quality_faq";
                        return (
                          <button
                            key={ci}
                            onClick={() => onSelectCitation(c)}
                            className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs border transition ${
                              isFaq
                                ? "bg-emerald-50 border-emerald-200 text-emerald-800 hover:bg-emerald-100"
                                : "bg-slate-50 border-slate-200 text-slate-700 hover:border-brand-500 hover:text-brand-700"
                            }`}
                          >
                            {isFaq ? (
                              <Award className="w-3.5 h-3.5 shrink-0" />
                            ) : (
                              <BookOpen className="w-3.5 h-3.5 shrink-0" />
                            )}
                            <span className="font-mono font-semibold">[{c.tag}]</span>
                            <span>
                              {isFaq
                                ? `FAQ Качества (${c.ticket_code})`
                                : `${c.directorate_label} · п. ${c.clause_num}`}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}

                {/* Telemetry micro-footer */}
                {!isUser && (m.latency_ms || 0) > 0 && (
                  <div className="mt-2.5 flex items-center gap-3 text-[11px] text-slate-400">
                    <span className="inline-flex items-center gap-1">
                      <Clock className="w-3 h-3" />
                      {((m.latency_ms || 0) / 1000).toFixed(2)} с
                    </span>
                    <span>
                      {(m.prompt_tokens || 0) + (m.completion_tokens || 0)} LLM tok
                      {m.embedding_tokens ? ` + ${m.embedding_tokens} emb` : ""}
                    </span>
                    {typeof m.cost_rub === "number" && (
                      <span>~{m.cost_rub.toFixed(4)} ₽</span>
                    )}
                  </div>
                )}
              </div>
            </div>
          );
        })}

        {/* Active streaming bubble */}
        {isSending && (
          <div className="max-w-3xl mx-auto flex justify-start">
            <div className="rounded-2xl px-4 py-3 text-sm bg-white border border-slate-200 shadow-sm w-full">
              {streamingStatus && !streamingText && (
                <div className="text-xs text-slate-500 flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full bg-brand-500 animate-ping" />
                  {streamingStatus}
                </div>
              )}
              {streamingText && (
                <div className="markdown-body text-slate-800">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{streamingText}</ReactMarkdown>
                </div>
              )}
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input box */}
      <div className="p-4 bg-white border-t border-slate-200">
        <form onSubmit={handleSubmit} className="max-w-3xl mx-auto flex gap-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={isSending}
            placeholder="Задайте вопрос по регламентам (например: можно ли начать работы без договора?)..."
            className="flex-1 px-4 py-2.5 rounded-xl border border-slate-300 text-sm focus:outline-none focus:border-brand-600 focus:ring-2 focus:ring-brand-100 disabled:bg-slate-100"
          />
          <button
            type="submit"
            disabled={isSending || !input.trim()}
            className="px-4 py-2.5 rounded-xl bg-brand-600 hover:bg-brand-700 disabled:opacity-50 text-white text-sm font-medium inline-flex items-center gap-1.5 transition shadow-sm"
          >
            <Send className="w-4 h-4" />
            Отправить
          </button>
        </form>
      </div>
    </div>
  );
};
