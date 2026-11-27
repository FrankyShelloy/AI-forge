import React, { useEffect, useState } from "react";
import {
  deleteSession,
  fetchHealth,
  fetchMessages,
  fetchSessions,
  streamChat,
  syncMailNow,
} from "./api";
import { ChatView } from "./components/ChatView";
import { QualityDashboard } from "./components/QualityDashboard";
import { Sidebar } from "./components/Sidebar";
import { SourceDrawer } from "./components/SourceDrawer";
import type { ChatMessage, Citation, HealthInfo, SessionInfo } from "./types";

export default function App() {
  const [activeTab, setActiveTab] = useState<"chat" | "quality">("chat");
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
  const [syncNotice, setSyncNotice] = useState<string | null>(null);

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
    setSyncNotice(null);
    try {
      const res = await syncMailNow();
      if (res.error) {
        setSyncNotice(`Почта: ${res.error}`);
      } else if (res.processed > 0) {
        setSyncNotice(`Добавлено в FAQ: ${res.processed} ответ(ов) Качества!`);
        if (currentSessionId) {
          const updated = await fetchMessages(currentSessionId);
          setMessages(updated);
        }
        fetchHealth().then(setHealth).catch(() => {});
      } else {
        setSyncNotice("Новых ответов от Службы качества пока нет");
      }
    } catch {
      setSyncNotice("Ошибка синхронизации почты");
    } finally {
      setSyncingMail(false);
    }
  };

  const handleSend = async (text: string) => {
    setIsSending(true);
    setStreamingText("");
    setStreamingStatus("Поиск по регламентам и базе знаний...");
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
            clarify: "Определяю недостающий контекст подразделения...",
            escalate: "Правила нет в базе — отправляю письмо в Службу качества...",
            complex_answer: "Сопоставляю требования нескольких дирекций раздельно...",
            faq_answer: "Найдено проверенное разъяснение Службы качества...",
            answer: "Формирую подтверждённый ответ со ссылками на пункты...",
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
          if (activeSid) {
            setCurrentSessionId(activeSid);
          }
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
    <div className="h-screen w-screen overflow-hidden flex bg-slate-50">
      <Sidebar
        activeTab={activeTab}
        onSelectTab={setActiveTab}
        sessions={sessions}
        currentSessionId={currentSessionId}
        onSelectSession={handleSelectSession}
        onNewSession={handleNewSession}
        onDeleteSession={handleDeleteSession}
        userEmail={userEmail}
        onChangeUserEmail={setUserEmail}
        onSyncMail={handleSyncMail}
        syncingMail={syncingMail}
        syncNotice={syncNotice}
        health={health}
      />

      {activeTab === "chat" ? (
        <>
          <ChatView
            messages={messages}
            streamingText={streamingText}
            streamingStatus={streamingStatus}
            isSending={isSending}
            onSend={handleSend}
            onSelectCitation={setSelectedCitation}
            onSyncMail={handleSyncMail}
            syncingMail={syncingMail}
          />
          <SourceDrawer
            citation={selectedCitation}
            onClose={() => setSelectedCitation(null)}
          />
        </>
      ) : (
        <QualityDashboard onSyncMail={handleSyncMail} syncingMail={syncingMail} />
      )}
    </div>
  );
}
