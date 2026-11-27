import React, { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  AlertTriangle,
  ArrowRight,
  Award,
  BookOpen,
  Check,
  ChevronDown,
  ChevronUp,
  Clock,
  Copy,
  Cpu,
  ExternalLink,
  Mail,
  RefreshCw,
  Send,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import type { ChatMessage, Citation, HealthInfo } from "../types";

const QUICK_SCENARIOS = [
  {
    badge: "Кейс 2 · Ловушка дирекций",
    badgeClass: "bg-rose-50 text-brand-700 border-rose-200",
    title: "Бюджетная оценка и срок расчёта",
    text: "Можно ли использовать бюджетную оценку для подачи в конкурс и за сколько дней нужно инициировать расчёт?",
  },
  {
    badge: "Кейс 1 · Тонкость внутри документа",
    badgeClass: "bg-blue-50 text-blue-700 border-blue-200",
    title: "Приоритет пресейла ИБ при нарушении SLA",
    text: "Меняется ли приоритет пресейловой заявки в Дирекции ИБ при нарушении SLA?",
  },
  {
    badge: "Edge-case · Вне регламентов",
    badgeClass: "bg-amber-50 text-amber-800 border-amber-200",
    title: "Вопрос про личную премию",
    text: "Какую премию я получу за закрытие этой сделки?",
  },
  {
    badge: "Кейс 3 · Email HITL → FAQ",
    badgeClass: "bg-emerald-50 text-emerald-700 border-emerald-200",
    title: "Компенсация такси в выходной (нет в базе)",
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
  onManualEscalate: (question: string) => Promise<void> | void;
  health: HealthInfo | null;
}

/**
 * Turns inline [S1] / [F1] markers into markdown links `[[S1]](#cite-S1)`
 * so ReactMarkdown renders them as interactive clickable pills right inside sentences!
 */
function linkifyCitations(text: string, citations: Citation[]): string {
  if (!citations.length) return text;
  const known = new Set(citations.map((c) => c.tag));
  return text.replace(/\[([SF]\d+)\]/g, (full, tag) => {
    if (!known.has(tag)) return full;
    return `[${tag}](#cite-${tag})`;
  });
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
  onManualEscalate,
  health,
}) => {
  const [input, setInput] = useState("");
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [escalatingId, setEscalatingId] = useState<string | null>(null);
  const [expandedTraces, setExpandedTraces] = useState<Record<string, boolean>>({});
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

  const handleCopy = (id: string, content: string) => {
    navigator.clipboard.writeText(content).catch(() => {});
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 1600);
  };

  const toggleTrace = (id: string) => {
    setExpandedTraces((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const lastUserQuestionBefore = (idx: number) => {
    for (let i = idx - 1; i >= 0; i--) {
      if (messages[i].role === "user") return messages[i].content;
    }
    return "";
  };

  return (
    <div className="flex-1 flex flex-col h-full min-w-0 softline-aura">
      {/* Scrollable chat feed */}
      <div className="flex-1 overflow-y-auto px-4 sm:px-8 py-6">
        {messages.length === 0 && !isSending ? (
          /* Flagship Hero Empty State */
          <div className="max-w-3xl mx-auto pt-4 sm:pt-8 animate-fadeSlideUp">
            <div className="bg-white/90 backdrop-blur rounded-3xl border border-slate-200/80 shadow-[0_8px_32px_rgba(15,23,42,0.05)] p-7 sm:p-9 text-center relative overflow-hidden">
              <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-rose-50 border border-rose-200/80 text-brand-700 text-xs font-semibold mb-4">
                <Sparkles className="w-3.5 h-3.5" />
                Zero-Hallucination Agentic RAG · LangGraph + Qdrant + Email HITL
              </div>
              <h1 className="text-2xl sm:text-3xl font-bold tracking-tight text-slate-900">
                Ответы строго по регламентам{" "}
                <span className="text-brand-600">Софтлайн</span>
              </h1>
              <p className="text-sm text-slate-500 mt-2.5 max-w-xl mx-auto leading-relaxed">
                Различает правила ДБРЦТ, Технической дирекции и Дирекции ИБ, уточняет контекст при неоднозначности и автоматически подключает Службу качества по почте, если нормы нет в базе.
              </p>

              {/* Trust indicators */}
              <div className="flex flex-wrap items-center justify-center gap-2 mt-5 text-[11px] font-medium text-slate-600">
                <span className="px-2.5 py-1 rounded-full bg-slate-100/90 border border-slate-200/70">
                  {health ? `${health.docs_indexed} действующих регламента · ${health.chunks_indexed ?? "…"} пунктов` : "Загрузка индекса регламентов…"}
                </span>
                <span className="px-2.5 py-1 rounded-full bg-slate-100/90 border border-slate-200/70">
                  48 терминов корпоративного глоссария
                </span>
                <span className="px-2.5 py-1 rounded-full bg-emerald-50 text-emerald-800 border border-emerald-200/80">
                  Open-Weight Apache 2.0 (≤ 23 ГБ VRAM)
                </span>
              </div>
            </div>

            {/* Scenario Cards */}
            <div className="mt-6">
              <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-400 px-1 mb-2.5">
                Быстрые сценарии для проверки всех кейсов заказчика
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {QUICK_SCENARIOS.map((q, idx) => (
                  <button
                    key={idx}
                    onClick={() => onSend(q.text)}
                    className="group text-left p-4 rounded-2xl bg-white/95 hover:bg-white border border-slate-200/80 hover:border-brand-500/70 shadow-sm hover:shadow-md transition-all duration-150 flex flex-col justify-between"
                  >
                    <div>
                      <span
                        className={`inline-block px-2 py-0.5 rounded-md text-[10px] font-semibold border mb-2 ${q.badgeClass}`}
                      >
                        {q.badge}
                      </span>
                      <div className="text-xs font-bold text-slate-900 group-hover:text-brand-700 transition">
                        {q.title}
                      </div>
                      <div className="text-xs text-slate-500 mt-1 leading-relaxed line-clamp-2">
                        «{q.text}»
                      </div>
                    </div>
                    <div className="mt-2.5 flex items-center gap-1 text-[11px] font-semibold text-brand-600 opacity-80 group-hover:opacity-100">
                      Задать вопрос <ArrowRight className="w-3 h-3 transition-transform group-hover:translate-x-0.5" />
                    </div>
                  </button>
                ))}
              </div>
            </div>
          </div>
        ) : (
          /* Message thread */
          <div className="max-w-3xl mx-auto space-y-5">
            {messages.map((m, idx) => {
              const isUser = m.role === "user";
              const isLastAssistant = !isUser && idx === messages.length - 1;
              const citeMap = new Map<string, Citation>();
              (m.citations || []).forEach((c) => citeMap.set(c.tag, c));
              const processedMd = isUser ? m.content : linkifyCitations(m.content, m.citations || []);
              const traceOpen = Boolean(expandedTraces[m.id]);

              return (
                <div
                  key={m.id || idx}
                  className={`flex animate-fadeSlideUp ${isUser ? "justify-end" : "justify-start"}`}
                >
                  {isUser ? (
                    <div className="max-w-[82%] rounded-2xl rounded-br-md px-4 py-3 bg-slate-900 text-white text-sm leading-relaxed shadow-sm">
                      {m.content}
                    </div>
                  ) : (
                    <div className="w-full rounded-2xl rounded-tl-md bg-white border border-slate-200/80 shadow-[0_2px_12px_rgba(15,23,42,0.04)] p-5">
                      {/* Header badges + Agent Trace toggle */}
                      <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
                        <div className="flex flex-wrap items-center gap-1.5">
                          {m.outcome === "faq_answered" && (
                            <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-50 text-emerald-800 border border-emerald-200">
                              <Award className="w-3 h-3" /> Проверенная БЗ Службы качества (FAQ)
                            </span>
                          )}
                          {m.outcome === "faq_updated" && (
                            <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-600 text-white shadow-sm">
                              <Mail className="w-3 h-3" /> Ответ эксперта получен по почте
                            </span>
                          )}
                          {m.outcome === "complex_answered" && (
                            <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-indigo-50 text-indigo-700 border border-indigo-200">
                              Комплексный проект · Раздельно по дирекциям
                            </span>
                          )}
                          {m.outcome === "partial_answer" && (
                            <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-amber-50 text-amber-800 border border-amber-200">
                              Частичный ответ с оговоркой
                            </span>
                          )}
                          {m.outcome === "clarify" && (
                            <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-amber-50 text-amber-800 border border-amber-200">
                              Уточнение контекста подразделения
                            </span>
                          )}
                          {m.outcome === "escalated" && (
                            <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-rose-50 text-brand-700 border border-rose-200">
                              <AlertTriangle className="w-3 h-3" /> Передано в Службу качества ({m.ticket_code})
                            </span>
                          )}
                          {m.outcome === "out_of_scope" && (
                            <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-slate-100 text-slate-700 border border-slate-200">
                              Вне области регламентов → Руководитель
                            </span>
                          )}
                          {m.outcome === "off_topic" && (
                            <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-slate-100 text-slate-600 border border-slate-200">
                              Ограничение области / Политика прав
                            </span>
                          )}
                          {m.outcome === "answered" && (
                            <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-blue-50 text-blue-700 border border-blue-200">
                              <BookOpen className="w-3 h-3" /> Подтверждено регламентом
                            </span>
                          )}
                        </div>

                        {/* Collapsible LangGraph Trace toggle */}
                        {m.trace && m.trace.length > 0 && (
                          <button
                            onClick={() => toggleTrace(m.id)}
                            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-medium text-slate-500 hover:text-slate-800 hover:bg-slate-100 transition"
                          >
                            <Cpu className="w-3 h-3 text-brand-600" />
                            <span>Шаги агента ({m.trace.length})</span>
                            {traceOpen ? (
                              <ChevronUp className="w-3 h-3" />
                            ) : (
                              <ChevronDown className="w-3 h-3" />
                            )}
                          </button>
                        )}
                      </div>

                      {/* Expanded LangGraph Stepper */}
                      {traceOpen && m.trace && m.trace.length > 0 && (
                        <div className="mb-3.5 p-3 rounded-xl bg-slate-50/90 border border-slate-200/80 space-y-2 animate-fadeSlideUp">
                          {m.trace.map((st, si) => (
                            <div key={si} className="flex items-start justify-between gap-3 text-[11px]">
                              <div className="flex items-start gap-2">
                                <span className="w-4 h-4 rounded-full bg-white border border-slate-300 text-[10px] font-bold text-slate-600 flex items-center justify-center shrink-0 mt-0.5">
                                  {si + 1}
                                </span>
                                <div>
                                  <div className="font-semibold text-slate-800">{st.title}</div>
                                  <div className="text-slate-500">{st.detail}</div>
                                </div>
                              </div>
                              <span className="font-mono text-[10px] text-slate-400 shrink-0">
                                {st.duration_ms} мс
                              </span>
                            </div>
                          ))}
                        </div>
                      )}

                      {/* Conflict alert banner (Q&A 27.11 rule: Regulation > FAQ) */}
                      {m.has_conflict && (
                        <div className="mb-3 p-3 rounded-xl bg-amber-50 border border-amber-200 text-xs text-amber-900 flex items-start gap-2">
                          <ShieldAlert className="w-4 h-4 text-amber-600 shrink-0 mt-0.5" />
                          <div>
                            <strong>Обнаружено расхождение между регламентом и FAQ:</strong>{" "}
                            {m.conflict_note ||
                              "Официальный регламент имеет высший приоритет; рекомендуем уточнение в Службе качества."}
                          </div>
                        </div>
                      )}

                      {/* Answer Markdown with real interactive inline [S1]/[F1] pills! */}
                      <div className="markdown-body">
                        <ReactMarkdown
                          remarkPlugins={[remarkGfm]}
                          components={{
                            a: ({ href, children }) => {
                              if (href && href.startsWith("#cite-")) {
                                const tag = href.replace("#cite-", "");
                                const cit = citeMap.get(tag);
                                if (cit) {
                                  const isFaq = cit.source_type === "quality_faq";
                                  return (
                                    <button
                                      type="button"
                                      onClick={() => onSelectCitation(cit)}
                                      title={`${cit.doc_title} · п. ${cit.clause_num}`}
                                      className={`inline-flex items-center px-1.5 py-[1px] mx-0.5 rounded-md font-mono text-[11px] font-bold align-baseline transition cursor-pointer border ${
                                        isFaq
                                          ? "bg-emerald-50 text-emerald-700 border-emerald-200 hover:bg-emerald-600 hover:text-white"
                                          : "bg-rose-50 text-brand-700 border-rose-200 hover:bg-brand-600 hover:text-white"
                                      }`}
                                    >
                                      {children}
                                    </button>
                                  );
                                }
                              }
                              return (
                                <a
                                  href={href}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="text-brand-600 hover:underline"
                                >
                                  {children}
                                </a>
                              );
                            },
                          }}
                        >
                          {processedMd}
                        </ReactMarkdown>
                      </div>

                      {/* Clickable clarification pills (S2) */}
                      {isLastAssistant && m.clarification_options?.length > 0 && !isSending && (
                        <div className="mt-4 pt-3.5 border-t border-slate-100">
                          <div className="text-[11px] font-medium text-slate-400 mb-2">
                            Выберите подходящий вариант:
                          </div>
                          <div className="flex flex-wrap gap-2">
                            {m.clarification_options.map((opt, oi) => (
                              <button
                                key={oi}
                                disabled={isSending}
                                onClick={() => onSend(opt)}
                                className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-xs font-semibold bg-rose-50/70 text-brand-700 border border-rose-200 hover:bg-brand-600 hover:text-white hover:border-brand-600 disabled:opacity-50 disabled:pointer-events-none shadow-sm transition"
                              >
                                <span>{opt}</span>
                                <ArrowRight className="w-3 h-3" />
                              </button>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Escalation action strip (S3) */}
                      {m.outcome === "escalated" && (
                        <div className="mt-4 pt-3.5 border-t border-slate-100 space-y-2.5">
                          <div className="flex flex-wrap items-center gap-2">
                            <a
                              href="/webmail/"
                              target="_blank"
                              rel="noopener noreferrer"
                              className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-xs font-semibold bg-slate-900 text-white hover:bg-slate-800 shadow-sm transition"
                            >
                              <Mail className="w-3.5 h-3.5" />
                              Открыть Roundcube (ответить от Качества)
                              <ExternalLink className="w-3 h-3 opacity-75" />
                            </a>
                            <button
                              onClick={onSyncMail}
                              disabled={syncingMail}
                              className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-xs font-semibold bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-60 shadow-sm transition"
                            >
                              <RefreshCw className={`w-3.5 h-3.5 ${syncingMail ? "animate-spin" : ""}`} />
                              Проверить ответ сейчас
                            </button>
                          </div>
                          <div className="px-3 py-2 rounded-xl bg-slate-50 border border-slate-200/80 flex flex-wrap items-center gap-2 text-[11px] text-slate-600">
                            <span className="font-semibold text-slate-700">Демо-ящик Службы качества в Roundcube:</span>
                            <button
                              type="button"
                              onClick={() => handleCopy(`rc-u-${m.id}`, "quality@corp.local")}
                              className="px-2 py-0.5 rounded-md bg-white border border-slate-200 font-mono text-slate-800 hover:border-brand-400 transition"
                              title="Нажмите, чтобы скопировать логин"
                            >
                              {copiedId === `rc-u-${m.id}` ? "✓ Скопировано" : "quality@corp.local"}
                            </button>
                            <span className="text-slate-400">/</span>
                            <button
                              type="button"
                              onClick={() => handleCopy(`rc-p-${m.id}`, "QualityPass2026!")}
                              className="px-2 py-0.5 rounded-md bg-white border border-slate-200 font-mono text-slate-800 hover:border-brand-400 transition"
                              title="Нажмите, чтобы скопировать пароль"
                            >
                              {copiedId === `rc-p-${m.id}` ? "✓ Скопировано" : "QualityPass2026!"}
                            </button>
                          </div>
                        </div>
                      )}

                      {/* Clickable source cards strip */}
                      {m.citations?.length > 0 && (
                        <div className="mt-4 pt-3.5 border-t border-slate-100">
                          <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-2">
                            Проверенные источники ({m.citations.length}) — нажмите для просмотра пункта
                          </div>
                          <div className="flex flex-wrap gap-1.5">
                            {m.citations.map((c, ci) => {
                              const isFaq = c.source_type === "quality_faq";
                              return (
                                <button
                                  key={ci}
                                  onClick={() => onSelectCitation(c)}
                                  className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl text-xs border transition ${
                                    isFaq
                                      ? "bg-emerald-50/70 border-emerald-200 text-emerald-800 hover:bg-emerald-100"
                                      : "bg-slate-50 border-slate-200/90 text-slate-700 hover:border-brand-400 hover:bg-rose-50/40 hover:text-brand-800"
                                  }`}
                                >
                                  {isFaq ? (
                                    <Award className="w-3.5 h-3.5 shrink-0 text-emerald-600" />
                                  ) : (
                                    <BookOpen className="w-3.5 h-3.5 shrink-0 text-brand-600" />
                                  )}
                                  <span className="font-mono font-bold text-[11px]">{c.tag}</span>
                                  <span className="font-medium">
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

                      {/* Telemetry + quick utility bar */}
                      <div className="mt-3.5 pt-2.5 border-t border-slate-100/80 flex flex-wrap items-center justify-between gap-2 text-[11px] text-slate-400">
                        <div className="flex items-center gap-3">
                          {(m.latency_ms || 0) > 0 && (
                            <span
                              className="inline-flex items-center gap-1"
                              title={
                                (m.ttft_ms || 0) > 0 && (m.ttft_ms || 0) < (m.latency_ms || 0)
                                  ? `Первый токен (TTFT): ${((m.ttft_ms || 0) / 1000).toFixed(2)} с · Полный ответ: ${((m.latency_ms || 0) / 1000).toFixed(2)} с`
                                  : undefined
                              }
                            >
                              <Clock className="w-3 h-3" />
                              {(m.ttft_ms || 0) > 0 && (m.ttft_ms || 0) < (m.latency_ms || 0)
                                ? `TTFT ${((m.ttft_ms || 0) / 1000).toFixed(1)} с · всего ${((m.latency_ms || 0) / 1000).toFixed(1)} с`
                                : `${((m.latency_ms || 0) / 1000).toFixed(2)} с`}
                            </span>
                          )}
                          {(m.prompt_tokens || 0) > 0 && (
                            <span>
                              {(m.prompt_tokens || 0) + (m.completion_tokens || 0)} tok
                            </span>
                          )}
                          {typeof m.cost_rub === "number" && m.cost_rub > 0 && (
                            <span className="font-mono">~{m.cost_rub.toFixed(4)} ₽</span>
                          )}
                        </div>

                        <div className="flex items-center gap-1.5">
                          <button
                            onClick={() => handleCopy(m.id, m.content)}
                            className="inline-flex items-center gap-1 px-2 py-1 rounded-md hover:bg-slate-100 text-slate-500 hover:text-slate-800 transition"
                          >
                            {copiedId === m.id ? (
                              <>
                                <Check className="w-3 h-3 text-emerald-600" /> Скопировано
                              </>
                            ) : (
                              <>
                                <Copy className="w-3 h-3" /> Копировать
                              </>
                            )}
                          </button>

                          {m.outcome !== "escalated" &&
                            m.outcome !== "off_topic" &&
                            m.outcome !== "out_of_scope" &&
                            m.outcome !== "faq_updated" && (
                              <button
                                disabled={isSending || escalatingId === m.id}
                                onClick={async () => {
                                  setEscalatingId(m.id);
                                  try {
                                    await onManualEscalate(
                                      lastUserQuestionBefore(idx) || m.content.slice(0, 120)
                                    );
                                  } finally {
                                    setEscalatingId(null);
                                  }
                                }}
                                className="inline-flex items-center gap-1 px-2 py-1 rounded-md hover:bg-rose-50 text-slate-400 hover:text-brand-700 disabled:opacity-40 transition"
                                title="Направить дополнительный вопрос в Службу качества по почте"
                              >
                                <Mail className="w-3 h-3" />{" "}
                                {escalatingId === m.id ? "Отправка..." : "Уточнить в Качестве"}
                              </button>
                            )}
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}

            {/* Live streaming bubble */}
            {isSending && (
              <div className="flex justify-start animate-fadeSlideUp">
                <div className="w-full rounded-2xl rounded-tl-md bg-white border border-slate-200/80 shadow-sm p-5">
                  {streamingStatus && !streamingText && (
                    <div className="flex items-center gap-2.5 text-xs font-medium text-slate-600">
                      <span className="relative flex h-2.5 w-2.5">
                        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-brand-500 opacity-75" />
                        <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-brand-600" />
                      </span>
                      <span>{streamingStatus}</span>
                    </div>
                  )}
                  {streamingText && (
                    <div className="markdown-body">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{streamingText}</ReactMarkdown>
                    </div>
                  )}
                </div>
              </div>
            )}

            <div ref={bottomRef} />
          </div>
        )}
      </div>

      {/* Elevated Floating Composer */}
      <div className="px-4 sm:px-8 pb-5 pt-2 bg-gradient-to-t from-[#F6F7FB] via-[#F6F7FB]/95 to-transparent">
        <form
          onSubmit={handleSubmit}
          className="max-w-3xl mx-auto bg-white rounded-2xl border border-slate-200/90 shadow-[0_4px_20px_rgba(15,23,42,0.06)] focus-within:border-brand-500/80 focus-within:ring-4 focus-within:ring-rose-500/10 transition flex items-center gap-2 p-2 pl-4"
        >
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={isSending}
            placeholder="Задайте вопрос по регламентам (например: можно ли начать работы без договора?)..."
            className="flex-1 bg-transparent text-sm text-slate-900 placeholder:text-slate-400 focus:outline-none disabled:opacity-60 py-1.5"
          />
          <button
            type="submit"
            disabled={isSending || !input.trim()}
            className="px-4 py-2.5 rounded-xl bg-gradient-to-r from-brand-600 to-rose-600 hover:from-brand-700 hover:to-rose-700 disabled:opacity-40 text-white text-xs font-semibold inline-flex items-center gap-1.5 shadow-sm transition shrink-0"
          >
            <Send className="w-3.5 h-3.5" />
            Отправить
          </button>
        </form>
        <div className="max-w-3xl mx-auto mt-1.5 px-2 flex items-center justify-between text-[10px] text-slate-400">
          <span>Все ответы проверяются по тексту регламентов · Официальный регламент главнее FAQ</span>
          <span>Приватный диалог 1-на-1</span>
        </div>
      </div>
    </div>
  );
};
