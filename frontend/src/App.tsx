import React, { useEffect, useState } from "react";
import { CheckCircle2, KeyRound, ShieldCheck, UserCheck, X } from "lucide-react";
import {
  deleteSession,
  fetchHealth,
  fetchMessages,
  fetchSessions,
  getQualityKey,
  manualEscalate,
  setQualityKey,
  streamChat,
  syncMailNow,
  verifyQualityPin,
} from "./api";
import { ChatView } from "./components/ChatView";
import { DocumentsView } from "./components/DocumentsView";
import { QualityDashboard } from "./components/QualityDashboard";
import { SecurityModal } from "./components/SecurityModal";
import { Sidebar } from "./components/Sidebar";
import { SourceDrawer } from "./components/SourceDrawer";
import { TopNav, type ActiveTab } from "./components/TopNav";
import type { ChatMessage, Citation, HealthInfo, SessionInfo } from "./types";

export default function App() {
  const [activeTab, setActiveTab] = useState<ActiveTab>("chat");
  const [health, setHealth] = useState<HealthInfo | null>(null);
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [userEmail, setUserEmail] = useState("employee@corp.local");

  const [isSending, setIsSending] = useState(false);
  const [streamingText, setStreamingText] = useState("");
  const [streamingStatus, setStreamingStatus] = useState<string | null>(null);
  const [selectedCitation, setSelectedCitation] = useState<Citation | null>(null);

  const [syncingMail, setSyncingMail] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [securityOpen, setSecurityOpen] = useState(false);

  // RBAC Role State ("Сотрудник" read-only vs "Служба качества" write KB/FAQ)
  const [isQualityRole, setIsQualityRole] = useState<boolean>(() => Boolean(getQualityKey()));
  const [roleModalOpen, setRoleModalOpen] = useState(false);
  const [pinInput, setPinInput] = useState("");
  const [pinError, setPinError] = useState<string | null>(null);

  // Deep-link state when jumping from a chat citation into full Document Explorer
  const [jumpDocId, setJumpDocId] = useState<string | null>(null);
  const [jumpClause, setJumpClause] = useState<string | null>(null);

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => {
      setToast((prev) => (prev === msg ? null : prev));
    }, 5000);
  };

  const loadSessions = async () => {
    try {
      const list = await fetchSessions();
      setSessions(list);
    } catch {
      // ignore
    }
  };

  useEffect(() => {
    fetchHealth()
      .then((h) => {
        setHealth(h);
        if (h.default_employee_email) setUserEmail(h.default_employee_email);
      })
      .catch(() => {});
    loadSessions();
  }, []);

  // Auto-poll inbox every 8s when the current session has an escalated message waiting for Quality reply
  const hasPendingEscalation =
    messages.some((m) => m.outcome === "escalated") &&
    !messages.some((m) => m.outcome === "faq_updated");

  useEffect(() => {
    if (!hasPendingEscalation || !currentSessionId) return;
    const timer = setInterval(async () => {
      try {
        const res = await syncMailNow();
        if (res.processed > 0) {
          const updated = await fetchMessages(currentSessionId);
          setMessages(updated);
          fetchHealth().then(setHealth).catch(() => {});
          showToast(
            `Получен проверенный ответ Службы качества (${res.processed}) и добавлен в базу FAQ!`
          );
        }
      } catch {
        // ignore silent poll tick
      }
    }, 8000);
    return () => clearInterval(timer);
  }, [hasPendingEscalation, currentSessionId]);

  const handleUnlockQuality = async (pinToVerify: string) => {
    setPinError(null);
    try {
      await verifyQualityPin(pinToVerify);
      setQualityKey(pinToVerify);
      setIsQualityRole(true);
      setRoleModalOpen(false);
      setPinInput("");
      showToast("Активирована роль «Служба качества»: открыто управление FAQ и загрузка .docx");
    } catch (err: unknown) {
      setPinError(err instanceof Error ? err.message : "Неверный ключ");
    }
  };

  const handleSwitchToEmployee = () => {
    setQualityKey(null);
    setIsQualityRole(false);
    setRoleModalOpen(false);
    showToast("Включён режим «Сотрудник» (только чтение БЗ и приватный чат 1-на-1)");
  };

  const handleSelectSession = async (sid: string) => {
    setCurrentSessionId(sid);
    setSelectedCitation(null);
    const msgs = await fetchMessages(sid);
    setMessages(msgs);
  };

  const handleNewSession = () => {
    setCurrentSessionId(null);
    setMessages([]);
    setSelectedCitation(null);
  };

  const handleDeleteSession = async (sid: string) => {
    await deleteSession(sid);
    if (currentSessionId === sid) handleNewSession();
    await loadSessions();
  };

  const handleSyncMail = async () => {
    setSyncingMail(true);
    try {
      const res = await syncMailNow();
      if (res.error) {
        showToast(`Ошибка почты: ${res.error}`);
      } else if (res.processed > 0) {
        showToast(
          `Синхронизировано: добавлено ${res.processed} проверенных разъяснений Службы качества в FAQ!`
        );
        if (currentSessionId) {
          const updated = await fetchMessages(currentSessionId);
          setMessages(updated);
        }
        fetchHealth().then(setHealth).catch(() => {});
      } else if (res.ignored_unauth > 0) {
        showToast(
          `Отклонено ${res.ignored_unauth} письмо(а) с адреса вне whitelist Службы качества`
        );
      } else {
        showToast("Новых ответов от Службы качества в почте пока нет");
      }
    } catch {
      showToast("Не удалось синхронизировать почту");
    } finally {
      setSyncingMail(false);
    }
  };

  const handleManualEscalate = async (question: string) => {
    if (!currentSessionId) return;
    try {
      const ticket = await manualEscalate({
        session_id: currentSessionId,
        question,
        user_email: userEmail,
      });
      const updated = await fetchMessages(currentSessionId);
      setMessages(updated);
      showToast(`Обращение ${ticket.ticket_code} отправлено в Службу качества (вы в копии)`);
    } catch {
      showToast("Не удалось отправить обращение в Службу качества");
    }
  };

  const handleOpenFullDocument = (docId: string, clauseNum: string) => {
    setJumpDocId(docId);
    setJumpClause(clauseNum);
    setActiveTab("docs");
  };

  const handleSend = async (text: string) => {
    setIsSending(true);
    setStreamingText("");
    setStreamingStatus("Расширение по глоссарию и гибридный поиск (Qdrant + BM25)...");
    setSelectedCitation(null);

    const optimisticUser: ChatMessage = {
      id: `tmp-${Date.now()}`,
      session_id: currentSessionId || "",
      role: "user",
      content: text,
      citations: [],
      clarification_options: [],
    };
    setMessages((prev) => [...prev, optimisticUser]);

    let activeSid = currentSessionId;
    await streamChat(
      { session_id: currentSessionId, message: text, user_email: userEmail },
      {
        onSession: (sid) => {
          activeSid = sid;
          setCurrentSessionId(sid);
        },
        onStatus: (info) => {
          const map: Record<string, string> = {
            retrieving: "Гибридный поиск по 611 пунктам регламентов и базе FAQ...",
            clarify: "Выявлена зависимость от дирекции — формулирую уточнение...",
            escalate: "Нормы нет в базе — отправляю реальное письмо в Службу качества...",
            complex_answer: "Сопоставляю требования нескольких дирекций раздельно...",
            faq_answer: "Найдено проверенное разъяснение Службы качества...",
            answer: "Генерация подтверждённого ответа с проверкой ссылок...",
          };
          setStreamingStatus(map[info.action] || "Формирую ответ...");
        },
        onDelta: (piece) => {
          setStreamingText((prev) => prev + piece);
        },
        onDone: (finalMsg) => {
          setMessages((prev) => [...prev, finalMsg]);
          setStreamingText("");
          setStreamingStatus(null);
          setIsSending(false);
          loadSessions();
          if (activeSid) setCurrentSessionId(activeSid);
        },
        onError: (detail) => {
          setMessages((prev) => [
            ...prev,
            {
              id: `err-${Date.now()}`,
              session_id: activeSid || "",
              role: "assistant",
              content: `Произошла ошибка при обработке запроса: ${detail}`,
              citations: [],
              clarification_options: [],
            },
          ]);
          setStreamingText("");
          setStreamingStatus(null);
          setIsSending(false);
        },
      }
    );
  };

  return (
    <div className="h-screen w-screen overflow-hidden flex flex-col bg-[#F6F7FB]">
      <TopNav
        activeTab={activeTab}
        onSelectTab={setActiveTab}
        onSyncMail={handleSyncMail}
        syncingMail={syncingMail}
        onOpenSecurity={() => setSecurityOpen(true)}
        isQualityRole={isQualityRole}
        onOpenRoleModal={() => {
          setPinError(null);
          setRoleModalOpen(true);
        }}
        onNewSession={handleNewSession}
        health={health}
      />

      {/* Global Toast Notification */}
      {toast && (
        <div className="fixed bottom-5 right-5 z-50 max-w-md bg-slate-900 text-white px-4 py-3 rounded-xl shadow-2xl border border-slate-700 flex items-center gap-2.5 text-xs animate-fadeSlideUp">
          <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
          <span className="leading-snug">{toast}</span>
          <button
            onClick={() => setToast(null)}
            className="text-slate-400 hover:text-white ml-1"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      {/* Main Workspace */}
      <div className="flex-1 flex min-h-0 overflow-hidden relative">
        {activeTab === "chat" && (
          <>
            <Sidebar
              sessions={sessions}
              currentSessionId={currentSessionId}
              onSelectSession={handleSelectSession}
              onNewSession={handleNewSession}
              onDeleteSession={handleDeleteSession}
              userEmail={userEmail}
              onChangeUserEmail={setUserEmail}
              health={health}
            />
            <ChatView
              messages={messages}
              streamingText={streamingText}
              streamingStatus={streamingStatus}
              isSending={isSending}
              onSend={handleSend}
              onSelectCitation={setSelectedCitation}
              onSyncMail={handleSyncMail}
              syncingMail={syncingMail}
              onManualEscalate={handleManualEscalate}
            />
            <SourceDrawer
              citation={selectedCitation}
              onClose={() => setSelectedCitation(null)}
              onOpenFullDocument={handleOpenFullDocument}
            />
          </>
        )}

        {activeTab === "docs" && (
          <DocumentsView
            initialDocId={jumpDocId}
            highlightClause={jumpClause}
            onClearJump={() => setJumpClause(null)}
            isQualityRole={isQualityRole}
            onRequestQualityRole={() => {
              setPinError(null);
              setRoleModalOpen(true);
            }}
          />
        )}

        {activeTab === "quality" && (
          <QualityDashboard
            onSyncMail={handleSyncMail}
            syncingMail={syncingMail}
            isQualityRole={isQualityRole}
            onRequestQualityRole={() => {
              setPinError(null);
              setRoleModalOpen(true);
            }}
          />
        )}
      </div>

      {/* RBAC Role Switcher Modal */}
      {roleModalOpen && (
        <div
          className="fixed inset-0 z-50 bg-slate-900/45 backdrop-blur-sm flex items-center justify-center p-4 animate-fadeSlideUp"
          onClick={() => setRoleModalOpen(false)}
        >
          <div
            className="bg-white rounded-2xl border border-slate-200 shadow-2xl max-w-md w-full p-6 space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="flex items-center gap-2.5">
                <div className="w-9 h-9 rounded-xl bg-indigo-50 border border-indigo-200 flex items-center justify-center">
                  <KeyRound className="w-4 h-4 text-indigo-600" />
                </div>
                <div>
                  <h3 className="text-sm font-bold text-slate-900">
                    Модель прав доступа (RBAC)
                  </h3>
                  <p className="text-[11px] text-slate-500">
                    Все читают регламенты · изменяет БЗ только Служба качества
                  </p>
                </div>
              </div>
              <button
                onClick={() => setRoleModalOpen(false)}
                className="p-1.5 rounded-lg text-slate-400 hover:text-slate-700 hover:bg-slate-100"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="p-3.5 rounded-xl bg-slate-50 border border-slate-200/80 text-xs text-slate-600 space-y-1.5 leading-relaxed">
              <div className="flex items-center gap-1.5 font-semibold text-slate-800">
                <ShieldCheck className="w-3.5 h-3.5 text-emerald-600" />
                Текущая роль:{" "}
                <span className={isQualityRole ? "text-indigo-700" : "text-slate-900"}>
                  {isQualityRole ? "Служба качества (чтение + управление БЗ)" : "Сотрудник (чтение + приватный чат 1-на-1)"}
                </span>
              </div>
              <p>
                Диалоги каждого посетителя изолированы по браузерному идентификатору{" "}
                <code className="px-1 py-0.5 bg-white rounded border border-slate-200 font-mono text-[11px]">
                  X-Client-Id
                </code>
                . Загрузка новых регламентов <code className="font-mono">.docx</code> и управление записями FAQ защищены ключом роли Службы качества.
              </p>
            </div>

            {isQualityRole ? (
              <button
                onClick={handleSwitchToEmployee}
                className="w-full py-2.5 px-4 rounded-xl bg-slate-900 hover:bg-slate-800 text-white text-xs font-semibold transition"
              >
                Переключиться обратно в роль «Сотрудник (Read-Only)»
              </button>
            ) : (
              <div className="space-y-3">
                <button
                  type="button"
                  onClick={() => handleUnlockQuality("Quality2026")}
                  className="w-full py-2.5 px-4 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold inline-flex items-center justify-center gap-1.5 shadow-sm transition"
                >
                  <UserCheck className="w-4 h-4" />
                  Войти как Служба качества в 1 клик (демо для жюри)
                </button>

                <div className="relative flex items-center py-0.5">
                  <div className="flex-grow border-t border-slate-200" />
                  <span className="px-2 text-[10px] uppercase tracking-wider text-slate-400">
                    или введите ключ вручную
                  </span>
                  <div className="flex-grow border-t border-slate-200" />
                </div>

                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    if (pinInput.trim()) handleUnlockQuality(pinInput.trim());
                  }}
                  className="flex items-center gap-2"
                >
                  <input
                    type="password"
                    value={pinInput}
                    onChange={(e) => setPinInput(e.target.value)}
                    placeholder="Ключ (Quality2026)"
                    className="flex-1 px-3 py-2 rounded-xl border border-slate-200 text-xs focus:outline-none focus:border-indigo-500"
                  />
                  <button
                    type="submit"
                    className="px-3.5 py-2 rounded-xl bg-slate-900 hover:bg-slate-800 text-white text-xs font-semibold"
                  >
                    Подтвердить
                  </button>
                </form>

                {pinError && (
                  <div className="text-xs text-rose-600 font-medium">{pinError}</div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      <SecurityModal open={securityOpen} onClose={() => setSecurityOpen(false)} />
    </div>
  );
}
