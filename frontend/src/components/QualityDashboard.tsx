import React, { useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  Award,
  Calculator,
  CheckCircle2,
  Clock,
  Download,
  FileText,
  Mail,
  RefreshCw,
  ShieldAlert,
  TrendingUp,
  Upload,
} from "lucide-react";
import {
  fetchAnalytics,
  fetchFaqs,
  fetchTickets,
  toggleFaqStatus,
  uploadDocx,
} from "../api";
import type { AnalyticsData, EscalationTicket, FaqEntry } from "../types";

interface Props {
  onSyncMail: () => Promise<void>;
  syncingMail: boolean;
}

export const QualityDashboard: React.FC<Props> = ({ onSyncMail, syncingMail }) => {
  const [analytics, setAnalytics] = useState<AnalyticsData | null>(null);
  const [tickets, setTickets] = useState<EscalationTicket[]>([]);
  const [faqs, setFaqs] = useState<FaqEntry[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState<string | null>(null);
  const [dailyVolume, setDailyVolume] = useState(200);
  const fileRef = useRef<HTMLInputElement>(null);

  const loadAll = async () => {
    const [a, t, f] = await Promise.all([
      fetchAnalytics(),
      fetchTickets(),
      fetchFaqs(),
    ]);
    setAnalytics(a);
    setTickets(t);
    setFaqs(f);
  };

  useEffect(() => {
    loadAll();
  }, []);

  const handleSync = async () => {
    await onSyncMail();
    await loadAll();
  };

  const handleToggleFaq = async (faq: FaqEntry) => {
    const next = faq.status === "active" ? "disabled" : "active";
    await toggleFaqStatus(faq.id, next);
    await loadAll();
  };

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setUploadMsg(null);
    try {
      const res = await uploadDocx(file);
      setUploadMsg(
        `Проиндексирован регламент «${res.doc_title}» (${res.directorate_label}, ${res.chunk_count} пунктов)`
      );
      await loadAll();
    } catch (err: unknown) {
      setUploadMsg(err instanceof Error ? err.message : "Ошибка загрузки");
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const s = analytics?.summary;
  const costPerReq = s?.avg_cost_rub && s.avg_cost_rub > 0 ? s.avg_cost_rub : 0.0458;
  const monthlyReqs = dailyVolume * 22; // 22 working days/month
  const monthlyApiRub = Math.round(monthlyReqs * costPerReq);
  // Softline brief baseline: 30–120 min (avg 45 min conservative) saved per query!
  const savedHoursMonth = Math.round((monthlyReqs * 45) / 60);

  const outcomeRows = [
    {
      label: "Точные ответы (регламенты + FAQ)",
      value: s?.answered || 0,
      color: "bg-emerald-500",
    },
    {
      label: "Уточнение контекста (Кейс 2)",
      value: s?.clarified || 0,
      color: "bg-amber-500",
    },
    {
      label: "Эскалация в Качество (Кейс 3)",
      value: s?.escalated || 0,
      color: "bg-brand-600",
    },
    {
      label: "Вне области / Отказ общего ИИ",
      value: s?.out_of_scope_or_refused || 0,
      color: "bg-slate-400",
    },
  ];
  const maxOutcome = Math.max(1, ...outcomeRows.map((r) => r.value));

  return (
    <div className="flex-1 overflow-y-auto bg-[#F6F7FB] softline-aura p-6">
      <div className="max-w-6xl mx-auto space-y-6">
        {/* Top bar */}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-bold text-slate-900 tracking-tight">
              Аналитика и Центр управления Службы качества
            </h1>
            <p className="text-xs text-slate-500 mt-0.5">
              Замкнутый цикл знаний, сигналы на актуализацию регламентов, аудит безопасности и экономика эксплуатации
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <input
              ref={fileRef}
              type="file"
              accept=".docx"
              className="hidden"
              onChange={handleUpload}
            />
            <a
              href="/api/analytics/export"
              download="softline_quality_report.md"
              className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl bg-white border border-slate-200 hover:border-slate-300 text-xs font-semibold text-slate-700 shadow-sm transition"
            >
              <Download className="w-3.5 h-3.5 text-slate-500" />
              Скачать отчёт (.md)
            </a>
            <button
              onClick={() => fileRef.current?.click()}
              disabled={uploading}
              className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl bg-white border border-slate-200 hover:border-brand-400 text-xs font-semibold text-slate-700 shadow-sm transition"
            >
              <Upload className="w-3.5 h-3.5 text-brand-600" />
              {uploading ? "Индексация..." : "Загрузить .docx"}
            </button>
            <button
              onClick={handleSync}
              disabled={syncingMail}
              className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-700 text-xs font-semibold text-white shadow-sm transition"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${syncingMail ? "animate-spin" : ""}`} />
              Синхронизировать почту
            </button>
          </div>
        </div>

        {uploadMsg && (
          <div className="p-3.5 rounded-xl bg-emerald-50 border border-emerald-200 text-xs text-emerald-900 font-medium">
            {uploadMsg}
          </div>
        )}

        {/* KPI Grid */}
        {s && (
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3.5">
            <div className="bg-white p-4 rounded-2xl border border-slate-200/80 shadow-sm">
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-slate-500">Всего обращений</span>
                <span className="w-8 h-8 rounded-xl bg-blue-50 text-blue-600 flex items-center justify-center">
                  <TrendingUp className="w-4 h-4" />
                </span>
              </div>
              <div className="text-2xl font-bold text-slate-900 mt-2">{s.total_queries}</div>
              <div className="text-[11px] text-slate-500 mt-1">
                Разрешение агентом:{" "}
                <strong className="text-emerald-600">{s.resolution_rate_pct}%</strong>
              </div>
            </div>

            <div className="bg-white p-4 rounded-2xl border border-slate-200/80 shadow-sm">
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-slate-500">Эскалации (Email HITL)</span>
                <span className="w-8 h-8 rounded-xl bg-rose-50 text-brand-600 flex items-center justify-center">
                  <Mail className="w-4 h-4" />
                </span>
              </div>
              <div className="text-2xl font-bold text-brand-600 mt-2">{s.tickets_total}</div>
              <div className="text-[11px] text-emerald-700 mt-1">
                Закрыто и в БЗ FAQ: <strong>{s.tickets_indexed}</strong>
              </div>
            </div>

            <div className="bg-white p-4 rounded-2xl border border-slate-200/80 shadow-sm">
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-slate-500">Проверенные FAQ</span>
                <span className="w-8 h-8 rounded-xl bg-emerald-50 text-emerald-600 flex items-center justify-center">
                  <Award className="w-4 h-4" />
                </span>
              </div>
              <div className="text-2xl font-bold text-emerald-600 mt-2">{s.faq_active}</div>
              <div className="text-[11px] text-slate-500 mt-1">
                Повторных ответов: <strong>{s.faq_total_reuses}</strong>
              </div>
            </div>

            <div className="bg-white p-4 rounded-2xl border border-slate-200/80 shadow-sm">
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-slate-500">Скорость / Экономика</span>
                <span className="w-8 h-8 rounded-xl bg-slate-100 text-slate-700 flex items-center justify-center">
                  <Clock className="w-4 h-4" />
                </span>
              </div>
              <div className="text-2xl font-bold text-slate-900 mt-2">
                {s.avg_latency_ms ? `${(s.avg_latency_ms / 1000).toFixed(1)} с` : "—"}
              </div>
              <div className="text-[11px] text-slate-500 mt-1">
                ~<strong className="font-mono text-slate-800">{costPerReq.toFixed(4)} ₽</strong> / запрос
              </div>
            </div>
          </div>
        )}

        {/* Row 2: Visual Outcomes Chart + Interactive ROI & Cost Calculator */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="bg-white p-5 rounded-2xl border border-slate-200/80 shadow-sm space-y-4">
            <h2 className="text-sm font-bold text-slate-900">
              Распределение сценариев работы агента
            </h2>
            <div className="space-y-3">
              {outcomeRows.map((row, idx) => (
                <div key={idx} className="space-y-1">
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-slate-600 font-medium">{row.label}</span>
                    <span className="font-mono font-bold text-slate-800">{row.value}</span>
                  </div>
                  <div className="w-full h-2 bg-slate-100 rounded-full overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all duration-500 ${row.color}`}
                      style={{ width: `${row.value === 0 ? 0 : Math.max(6, (row.value / maxOutcome) * 100)}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>

            {analytics && Object.keys(analytics.directorates).length > 0 && (
              <div className="pt-3 border-t border-slate-100">
                <div className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-2">
                  Запросы по дирекциям
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {Object.entries(analytics.directorates).map(([dir, cnt]) => (
                    <span
                      key={dir}
                      className="px-2.5 py-1 rounded-lg bg-slate-50 border border-slate-200 text-xs font-medium text-slate-700"
                    >
                      {dir}: <strong className="text-slate-900">{cnt}</strong>
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* Interactive Business ROI & Economics Simulator */}
          <div className="bg-white p-5 rounded-2xl border border-slate-200/80 shadow-sm flex flex-col justify-between space-y-4">
            <div>
              <div className="flex items-center justify-between mb-1">
                <h2 className="text-sm font-bold text-slate-900 flex items-center gap-2">
                  <Calculator className="w-4 h-4 text-brand-600" />
                  Калькулятор экономики и бизнес-эффекта (ROI)
                </h2>
                <span className="text-[11px] font-mono font-semibold px-2 py-0.5 rounded-md bg-rose-50 text-brand-700">
                  {dailyVolume} запр./день
                </span>
              </div>
              <p className="text-[11px] text-slate-500">
                По данным ТЗ Софтлайн: ручной поиск занимает 30–120 мин (в расчёте консервативно 45 мин)
              </p>

              <div className="mt-4">
                <input
                  type="range"
                  min={50}
                  max={500}
                  step={25}
                  value={dailyVolume}
                  onChange={(e) => setDailyVolume(Number(e.target.value))}
                  className="w-full accent-brand-600 cursor-pointer"
                />
                <div className="flex justify-between text-[10px] text-slate-400 font-mono">
                  <span>50/день</span>
                  <span>200/день (цель ТЗ)</span>
                  <span>500/день</span>
                </div>
              </div>
            </div>

            <div className="grid grid-cols-3 gap-2.5">
              <div className="p-3 rounded-xl bg-slate-50 border border-slate-200/80">
                <div className="text-[10px] text-slate-500">Запросов / мес.</div>
                <div className="text-base font-bold text-slate-900 mt-0.5">
                  {monthlyReqs.toLocaleString("ru-RU")}
                </div>
              </div>
              <div className="p-3 rounded-xl bg-emerald-50/80 border border-emerald-200">
                <div className="text-[10px] text-emerald-700">Экономия времени</div>
                <div className="text-base font-bold text-emerald-800 mt-0.5">
                  ~{savedHoursMonth.toLocaleString("ru-RU")} ч/мес
                </div>
              </div>
              <div className="p-3 rounded-xl bg-rose-50/70 border border-rose-200">
                <div className="text-[10px] text-brand-700">Токены (эквив.)</div>
                <div className="text-base font-bold text-brand-700 mt-0.5">
                  ~{monthlyApiRub.toLocaleString("ru-RU")} ₽/мес
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Row 3: Revision Signals + Security Audit & Popular Sections */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="bg-white p-5 rounded-2xl border border-slate-200/80 shadow-sm">
            <h2 className="text-sm font-bold text-slate-900 flex items-center gap-2 mb-3">
              <AlertCircle className="w-4 h-4 text-amber-500" />
              Сигналы на пересмотр регламентов
            </h2>
            {!analytics?.revision_signals.length ? (
              <div className="text-xs text-slate-400 py-5 text-center bg-slate-50 rounded-xl border border-dashed border-slate-200">
                Пробелов пока не выявлено. При эскалации вопроса в Службу качества здесь автоматически сформируется рекомендация на пересмотр.
              </div>
            ) : (
              <div className="space-y-2.5">
                {analytics.revision_signals.map((sig, i) => (
                  <div
                    key={i}
                    className={`p-3.5 rounded-xl border text-xs ${
                      sig.severity === "high"
                        ? "bg-rose-50 border-rose-200 text-rose-950"
                        : "bg-amber-50/80 border-amber-200 text-amber-950"
                    }`}
                  >
                    <div className="font-bold">{sig.topic}</div>
                    <div className="text-[11px] mt-0.5 opacity-90">{sig.reason}</div>
                    <div className="text-[11px] mt-1.5 font-semibold">
                      Эскалаций: {sig.escalations} · Обращений: {sig.total_asks}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="space-y-4">
            <div className="bg-white p-5 rounded-2xl border border-slate-200/80 shadow-sm">
              <h2 className="text-sm font-bold text-slate-900 flex items-center gap-2 mb-3">
                <FileText className="w-4 h-4 text-blue-600" />
                Востребованные разделы регламентов
              </h2>
              {!analytics?.popular_sections.length ? (
                <div className="text-xs text-slate-400 py-3">
                  Статистика по разделам появится после первых вопросов в чате.
                </div>
              ) : (
                <div className="space-y-2">
                  {analytics.popular_sections.map((sec, i) => (
                    <div
                      key={i}
                      className="flex items-center justify-between gap-2 text-xs py-1.5 border-b border-slate-100 last:border-0"
                    >
                      <span className="text-slate-700 truncate">{sec.section}</span>
                      <span className="px-2 py-0.5 rounded-full bg-slate-100 text-slate-700 font-semibold shrink-0">
                        {sec.hits}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="bg-white p-5 rounded-2xl border border-slate-200/80 shadow-sm">
              <h2 className="text-sm font-bold text-slate-900 flex items-center gap-2 mb-2.5">
                <ShieldAlert className="w-4 h-4 text-emerald-600" />
                Аудит прав и безопасности (Whitelist / RBAC)
              </h2>
              {!analytics?.security_events.length ? (
                <div className="text-xs text-slate-500">
                  Нарушений не зафиксировано. Неавторизованные письма (вне whitelist Службы качества) и попытки изменения БЗ через чат автоматически блокируются.
                </div>
              ) : (
                <div className="space-y-2">
                  {analytics.security_events.slice(0, 4).map((ev) => (
                    <div
                      key={ev.id}
                      className="p-2.5 rounded-xl bg-rose-50/60 border border-rose-200 text-[11px] text-rose-950"
                    >
                      <div className="font-semibold">
                        [{ev.event_type}] {ev.actor}
                      </div>
                      <div className="opacity-85 mt-0.5">{ev.detail}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Escalation Tickets Table */}
        <div className="bg-white p-5 rounded-2xl border border-slate-200/80 shadow-sm">
          <div className="flex items-center justify-between mb-3.5">
            <h2 className="text-sm font-bold text-slate-900 flex items-center gap-2">
              <Mail className="w-4 h-4 text-brand-600" />
              Журнал обращений в Службу качества (Email HITL)
            </h2>
            <a
              href="/webmail/"
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-brand-600 hover:underline font-semibold"
            >
              Открыть Roundcube →
            </a>
          </div>
          {tickets.length === 0 ? (
            <div className="text-xs text-slate-400 py-5 text-center bg-slate-50 rounded-xl border border-dashed border-slate-200">
              Обращений пока нет. Задайте в чате вопрос, отсутствующий в 4 регламентах, чтобы протестировать реальную отправку письма.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs text-left border-collapse">
                <thead>
                  <tr className="border-b border-slate-200 text-slate-400 uppercase text-[10px] tracking-wider">
                    <th className="py-2.5 pr-3">Тикет</th>
                    <th className="py-2.5 pr-3">Вопрос сотрудника</th>
                    <th className="py-2.5 pr-3">Статус</th>
                    <th className="py-2.5 pr-3">Ответ эксперта (очищенный)</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {tickets.map((t) => (
                    <tr key={t.id} className="align-top">
                      <td className="py-3 pr-3 font-mono font-bold text-slate-800 whitespace-nowrap">
                        {t.ticket_code}
                        <div className="text-[10px] font-sans font-normal text-slate-400 mt-0.5">
                          CC: {t.user_email}
                        </div>
                      </td>
                      <td className="py-3 pr-3 text-slate-700 max-w-xs">{t.question}</td>
                      <td className="py-3 pr-3 whitespace-nowrap">
                        {t.status === "indexed" ? (
                          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full bg-emerald-50 text-emerald-800 border border-emerald-200 font-semibold">
                            <CheckCircle2 className="w-3 h-3" /> В базе FAQ
                          </span>
                        ) : t.status === "sent" ? (
                          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full bg-amber-50 text-amber-800 border border-amber-200 font-semibold">
                            Отправлено (SMTP)
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full bg-rose-50 text-rose-800 border border-rose-200 font-semibold">
                            {t.status}
                          </span>
                        )}
                      </td>
                      <td className="py-3 text-slate-600 max-w-sm">
                        {t.expert_reply_clean ? (
                          <div>
                            <div className="text-[10px] text-emerald-700 font-semibold mb-0.5">
                              От: {t.expert_email}
                            </div>
                            {t.expert_reply_clean}
                          </div>
                        ) : (
                          <span className="text-slate-400">Ожидает ответа эксперта в Roundcube</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Verified FAQ KB Governance */}
        <div className="bg-white p-5 rounded-2xl border border-slate-200/80 shadow-sm">
          <h2 className="text-sm font-bold text-slate-900 flex items-center gap-2 mb-3.5">
            <Award className="w-4 h-4 text-emerald-600" />
            Проверенная база знаний (FAQ от Службы качества)
          </h2>
          {faqs.length === 0 ? (
            <div className="text-xs text-slate-400 py-5 text-center bg-slate-50 rounded-xl border border-dashed border-slate-200">
              Разъяснения появятся здесь автоматически после ответа Службы качества по электронной почте.
            </div>
          ) : (
            <div className="space-y-2.5">
              {faqs.map((f) => (
                <div
                  key={f.id}
                  className="p-4 rounded-xl border border-slate-200/90 bg-slate-50/50 flex items-start justify-between gap-4"
                >
                  <div className="text-xs space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono font-bold text-emerald-700">
                        {f.ticket_code}
                      </span>
                      <span className="text-slate-300">·</span>
                      <span className="text-slate-500">Эксперт: {f.verified_by}</span>
                      <span className="text-slate-300">·</span>
                      <span className="text-slate-700 font-semibold">
                        Повторных использований: {f.usage_count}
                      </span>
                    </div>
                    <div className="font-bold text-slate-900">{f.question}</div>
                    <div className="text-slate-700 whitespace-pre-wrap leading-relaxed">
                      {f.answer}
                    </div>
                  </div>
                  <button
                    onClick={() => handleToggleFaq(f)}
                    className={`px-3 py-1.5 rounded-lg text-[11px] font-semibold shrink-0 border transition ${
                      f.status === "active"
                        ? "bg-emerald-50 border-emerald-200 text-emerald-800 hover:bg-rose-50 hover:border-rose-200 hover:text-rose-700"
                        : "bg-slate-200 border-slate-300 text-slate-600 hover:bg-emerald-50"
                    }`}
                  >
                    {f.status === "active" ? "Активно (откл.)" : "Отключено (вкл.)"}
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
