import React, { useEffect, useState } from "react";
import { Cpu, ExternalLink, Lock, Plug, ShieldCheck, X } from "lucide-react";
import { fetchSystemModels } from "../api";
import type { SystemModelsInfo } from "../types";

interface Props {
  open: boolean;
  onClose: () => void;
}

export const SecurityModal: React.FC<Props> = ({ open, onClose }) => {
  const [info, setInfo] = useState<SystemModelsInfo | null>(null);

  useEffect(() => {
    if (open && !info) {
      fetchSystemModels().then(setInfo).catch(() => {});
    }
  }, [open, info]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 bg-slate-900/40 backdrop-blur-sm flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-2xl border border-slate-200 shadow-2xl max-w-2xl w-full overflow-hidden animate-fadeSlideUp"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between bg-gradient-to-r from-slate-50 to-rose-50/40">
          <div className="flex items-center gap-2.5">
            <div className="w-9 h-9 rounded-xl bg-emerald-100 text-emerald-700 flex items-center justify-center">
              <ShieldCheck className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-sm font-bold text-slate-900">
                Изолированный контур, Open-Weight модели и интеграция
              </h2>
              <p className="text-xs text-slate-500">
                Готовность к развёртыванию в закрытом контуре ПАО «Софтлайн» (152-ФЗ)
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-slate-400 hover:text-slate-700 hover:bg-slate-100"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-6 space-y-5 max-h-[75vh] overflow-y-auto">
          {!info ? (
            <div className="text-xs text-slate-400 py-8 text-center">Загрузка конфигурации...</div>
          ) : (
            <>
              {/* VRAM budget bar */}
              <div className="p-4 rounded-xl bg-slate-50 border border-slate-200/80">
                <div className="flex items-center justify-between text-xs mb-2">
                  <span className="font-semibold text-slate-800 flex items-center gap-1.5">
                    <Cpu className="w-4 h-4 text-brand-600" />
                    Локальный бюджет VRAM (квантование Q4)
                  </span>
                  <span className="font-mono font-semibold text-emerald-700">
                    ~{info.estimated_local_vram_gb} ГБ / {info.vram_limit_gb} ГБ макс.
                  </span>
                </div>
                <div className="w-full h-2.5 bg-slate-200 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-gradient-to-r from-emerald-500 to-teal-500 rounded-full"
                    style={{
                      width: `${Math.min(100, (info.estimated_local_vram_gb / info.vram_limit_gb) * 100)}%`,
                    }}
                  />
                </div>
                <p className="text-[11px] text-slate-500 mt-2">
                  Обе модели помещаются на 1×GPU 24–48 ГБ (RTX 4090 / L40S). В репозитории встроен{" "}
                  <code className="bg-white px-1.5 py-0.5 rounded border border-slate-200 text-slate-700">
                    docker compose --profile local up -d
                  </code>{" "}
                  для мгновенного переключения на локальный Ollama/vLLM без изменения кода.
                </p>
              </div>

              {/* Models cards */}
              <div className="space-y-2.5">
                <div className="text-xs font-semibold text-slate-700">
                  Используемые модели (Open-Weight · Apache 2.0)
                </div>
                {info.models.map((m, idx) => (
                  <div
                    key={idx}
                    className="p-3.5 rounded-xl border border-slate-200 bg-white flex items-start justify-between gap-3"
                  >
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-bold text-slate-900">{m.hf_repo}</span>
                        <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200">
                          {m.license}
                        </span>
                      </div>
                      <div className="text-[11px] text-slate-600">{m.role} · {m.architecture}</div>
                      <div className="text-[11px] text-slate-400">VRAM в Q4: ~{m.vram_q4_gb} ГБ</div>
                    </div>
                    <a
                      href={m.hf_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 text-[11px] font-medium shrink-0 transition"
                    >
                      Hugging Face <ExternalLink className="w-3 h-3" />
                    </a>
                  </div>
                ))}
              </div>

              {/* Governance & DION */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div className="p-3.5 rounded-xl bg-slate-50 border border-slate-200/80 text-xs space-y-1.5">
                  <div className="font-semibold text-slate-800 flex items-center gap-1.5">
                    <Lock className="w-3.5 h-3.5 text-brand-600" />
                    Модель прав и приоритеты
                  </div>
                  <p className="text-[11px] text-slate-600 leading-relaxed">
                    {info.integrations.official_regulation_priority}
                  </p>
                  <p className="text-[11px] text-slate-500">
                    Whitelist пополнения БЗ:{" "}
                    <span className="font-mono text-slate-800">
                      {info.integrations.mail_whitelist.join(", ")}
                    </span>
                  </p>
                </div>

                <div className="p-3.5 rounded-xl bg-slate-50 border border-slate-200/80 text-xs space-y-1.5">
                  <div className="font-semibold text-slate-800 flex items-center gap-1.5">
                    <Plug className="w-3.5 h-3.5 text-indigo-600" />
                    Готовность к DION / Webhook API
                  </div>
                  <p className="text-[11px] text-slate-600 leading-relaxed">
                    Реализован синхронный REST endpoint{" "}
                    <code className="bg-white px-1 py-0.5 rounded border border-slate-200">
                      POST {info.integrations.dion_webhook_endpoint}
                    </code>{" "}
                    для подключения корпоративного бота DION за 1 день.
                  </p>
                  <a
                    href={info.integrations.openapi_docs}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 text-[11px] font-semibold text-brand-600 hover:underline"
                  >
                    Открыть OpenAPI / Swagger <ExternalLink className="w-3 h-3" />
                  </a>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
};
