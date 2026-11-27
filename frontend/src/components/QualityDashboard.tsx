import React, { useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  Award,
  CheckCircle2,
  FileText,
  Mail,
  RefreshCw,
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

  return (
    <div className="flex-1 overflow-y-auto bg-slate-50 p-6">
      <div className="max-w-6xl mx-auto space-y-6">
        {/* Header */}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-bold text-slate-900">
              Панель Службы качества и Аналитика
            </h1>
            <p className="text-xs text-slate-500 mt-0.5">
              Мониторинг неотвеченных запросов, проверенная БЗ (FAQ), сигналы на пересмотр регламентов и экономика запросов
            </p>
          </div>
          <div className="flex items-center gap-2">
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
              className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-white border border-slate-300 hover:border-brand-500 text-xs font-medium text-slate-700 transition"
            >
              <Upload className="w-3.5 h-3.5" />
              {uploading ? "Индексация..." : "Загрузить .docx регламент"}
            </button>
            <button
              onClick={handleSync}
              disabled={syncingMail}
              className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-xs font-medium text-white transition"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${syncingMail ? "animate-spin" : ""}`} />
              Синхронизировать почту
            </button>
          </div>
        </div>

        {uploadMsg && (
          <div className="p-3 rounded-lg bg-blue-50 border border-blue-200 text-xs text-blue-900">
            {uploadMsg}
          </div>
        )}

        {/* KPI Grid */}
        {s && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
              <div className="text-xs text-slate-500">Всего обращений</div>
              <div className="text-2xl font-bold text-slate-900 mt-1">{s.total_queries}</div>
              <div className="text-[11px] text-slate-400 mt-1">
                Точных ответов: {s.answered} · Уточнений: {s.clarified}
              </div>
            </div>
            <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
              <div className="text-xs text-slate-500">Эскалаций в Качество</div>
              <div className="text-2xl font-bold text-brand-600 mt-1">{s.tickets_total}</div>
              <div className="text-[11px] text-emerald-600 mt-1">
                Закрыто и добавлено в FAQ: {s.tickets_indexed}
              </div>
            </div>
            <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
              <div className="text-xs text-slate-500">Активных FAQ-разъяснений</div>
              <div className="text-2xl font-bold text-emerald-600 mt-1">{s.faq_active}</div>
              <div className="text-[11px] text-slate-400 mt-1">
                Только от whitelist Службы качества
              </div>
            </div>
            <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
              <div className="text-xs text-slate-500">Скорость / Экономика</div>
              <div className="text-2xl font-bold text-slate-900 mt-1">
                {(s.avg_latency_ms / 1000).toFixed(2)} с
              </div>
              <div className="text-[11px] text-slate-400 mt-1">
                Ср. стоимость: {s.avg_cost_rub.toFixed(4)} ₽/запрос
              </div>
            </div>
          </div>
        )}

        {/* Revision signals + Top sections */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
            <h2 className="text-sm font-semibold text-slate-900 flex items-center gap-2 mb-3">
              <AlertCircle className="w-4 h-4 text-amber-500" />
              Сигналы на пересмотр регламентов
            </h2>
            {!analytics?.revision_signals.length ? (
              <div className="text-xs text-slate-400 py-4">
                Пока нет неотвеченных тем. При эскалациях в Службу качества здесь появятся рекомендации по актуализации регламентов.
              </div>
            ) : (
              <div className="space-y-2">
                {analytics.revision_signals.map((sig, i) => (
                  <div
                    key={i}
                    className={`p-3 rounded-lg border text-xs ${
                      sig.severity === "high"
                        ? "bg-rose-50 border-rose-200 text-rose-900"
                        : "bg-amber-50 border-amber-200 text-amber-900"
                    }`}
                  >
                    <div className="font-semibold">{sig.topic}</div>
                    <div className="text-[11px] mt-0.5 opacity-90">{sig.reason}</div>
                    <div className="text-[11px] mt-1 font-medium">
                      Эскалаций: {sig.escalations} · Всего вопросов: {sig.total_asks}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
            <h2 className="text-sm font-semibold text-slate-900 flex items-center gap-2 mb-3">
              <FileText className="w-4 h-4 text-blue-600" />
              Востребованные разделы и проблемные зоны
            </h2>
            {!analytics?.popular_sections.length ? (
              <div className="text-xs text-slate-400 py-4">
                Статистика по разделам появится после первых обращений в чате.
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
                      {sec.hits} запр.
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Escalation Tickets Table */}
        <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-slate-900 flex items-center gap-2">
              <Mail className="w-4 h-4 text-brand-600" />
              Журнал обращений в Службу качества (Email HITL)
            </h2>
            <a
              href="/webmail/"
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-brand-600 hover:underline font-medium"
            >
              Открыть почту (Roundcube) →
            </a>
          </div>
          {tickets.length === 0 ? (
            <div className="text-xs text-slate-400 py-4">
              Обращений пока нет. Задайте вопрос, ответа на который нет в 4 регламентах, чтобы запустить сценарий 3.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs text-left border-collapse">
                <thead>
                  <tr className="border-b border-slate-200 text-slate-500">
                    <th className="py-2 pr-3">Тикет</th>
                    <th className="py-2 pr-3">Вопрос сотрудника</th>
                    <th className="py-2 pr-3">Статус</th>
                    <th className="py-2 pr-3">Ответ эксперта</th>
                  </tr>
                </thead>
                <tbody>
                  {tickets.map((t) => (
                    <tr key={t.id} className="border-b border-slate-100 align-top">
                      <td className="py-2.5 pr-3 font-mono font-semibold text-slate-800 whitespace-nowrap">
                        {t.ticket_code}
                        <div className="text-[10px] font-sans font-normal text-slate-400">
                          CC: {t.user_email}
                        </div>
                      </td>
                      <td className="py-2.5 pr-3 text-slate-700 max-w-xs">{t.question}</td>
                      <td className="py-2.5 pr-3 whitespace-nowrap">
                        {t.status === "indexed" ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-800 font-medium">
                            <CheckCircle2 className="w-3 h-3" /> В базе FAQ
                          </span>
                        ) : t.status === "sent" ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-amber-100 text-amber-800 font-medium">
                            Отправлено по SMTP
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-rose-100 text-rose-800 font-medium">
                            {t.status}
                          </span>
                        )}
                      </td>
                      <td className="py-2.5 text-slate-600 max-w-sm">
                        {t.expert_reply_clean ? (
                          <div>
                            <div className="text-[10px] text-emerald-700 font-medium mb-0.5">
                              От: {t.expert_email}
                            </div>
                            {t.expert_reply_clean}
                          </div>
                        ) : (
                          <span className="text-slate-400">Ожидает ответа эксперта по email</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Verified FAQ KB */}
        <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
          <h2 className="text-sm font-semibold text-slate-900 flex items-center gap-2 mb-3">
            <Award className="w-4 h-4 text-emerald-600" />
            Проверенная база знаний (FAQ от Службы качества)
          </h2>
          {faqs.length === 0 ? (
            <div className="text-xs text-slate-400 py-4">
              База FAQ пополняется автоматически при получении ответа по почте от уполномоченного адреса Службы качества.
            </div>
          ) : (
            <div className="space-y-2.5">
              {faqs.map((f) => (
                <div
                  key={f.id}
                  className="p-3.5 rounded-lg border border-slate-200 bg-slate-50/70 flex items-start justify-between gap-4"
                >
                  <div className="text-xs space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="font-mono font-semibold text-emerald-700">
                        {f.ticket_code}
                      </span>
                      <span className="text-slate-400">·</span>
                      <span className="text-slate-500">Эксперт: {f.verified_by}</span>
                      <span className="text-slate-400">·</span>
                      <span className="text-slate-600 font-medium">
                        Использовано в ответах: {f.usage_count}
                      </span>
                    </div>
                    <div className="font-semibold text-slate-900">{f.question}</div>
                    <div className="text-slate-700 whitespace-pre-wrap">{f.answer}</div>
                  </div>
                  <button
                    onClick={() => handleToggleFaq(f)}
                    className={`px-2.5 py-1 rounded-md text-[11px] font-medium shrink-0 border transition ${
                      f.status === "active"
                        ? "bg-emerald-50 border-emerald-200 text-emerald-800 hover:bg-rose-50 hover:text-rose-700"
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

        {/* Indexed official documents */}
        <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
          <h2 className="text-sm font-semibold text-slate-900 mb-3">
            Индексированные официальные регламенты (Read-Only для агента)
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
            {analytics?.documents.map((doc) => (
              <div
                key={doc.doc_id}
                className="p-3 rounded-lg border border-slate-200 bg-slate-50 text-xs flex items-center justify-between gap-2"
              >
                <div>
                  <div className="font-semibold text-slate-900">{doc.doc_title}</div>
                  <div className="text-[11px] text-slate-500 mt-0.5">
                    {doc.directorate_label} · {doc.filename}
                  </div>
                </div>
                <span className="px-2 py-0.5 rounded-full bg-blue-100 text-blue-800 font-semibold shrink-0">
                  {doc.chunk_count} пп.
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};
