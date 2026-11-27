import type {
  AnalyticsData,
  ChatMessage,
  DocDetail,
  EscalationTicket,
  FaqEntry,
  HealthInfo,
  IndexedDoc,
  SessionInfo,
  SystemModelsInfo,
} from "./types";

export async function fetchHealth(): Promise<HealthInfo> {
  const res = await fetch("/api/health");
  if (!res.ok) throw new Error("Health check failed");
  return res.json();
}

export async function fetchSystemModels(): Promise<SystemModelsInfo> {
  const res = await fetch("/api/system/models");
  if (!res.ok) throw new Error("Failed to load model governance info");
  return res.json();
}

export async function fetchSessions(): Promise<SessionInfo[]> {
  const res = await fetch("/api/sessions");
  if (!res.ok) throw new Error("Failed to load sessions");
  return res.json();
}

export async function fetchMessages(sessionId: string): Promise<ChatMessage[]> {
  const res = await fetch(`/api/sessions/${sessionId}/messages`);
  if (!res.ok) throw new Error("Failed to load messages");
  return res.json();
}

export async function deleteSession(sessionId: string): Promise<void> {
  await fetch(`/api/sessions/${sessionId}`, { method: "DELETE" });
}

export async function manualEscalate(params: {
  session_id: string;
  question: string;
  user_email: string;
}): Promise<EscalationTicket> {
  const res = await fetch("/api/escalate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!res.ok) throw new Error("Escalation failed");
  return res.json();
}

export async function syncMailNow(): Promise<{
  checked: number;
  processed: number;
  ignored_unauth: number;
  error: string | null;
}> {
  const res = await fetch("/api/mail/sync", { method: "POST" });
  if (!res.ok) throw new Error("Mail sync failed");
  return res.json();
}

export async function fetchAnalytics(): Promise<AnalyticsData> {
  const res = await fetch("/api/analytics");
  if (!res.ok) throw new Error("Failed to load analytics");
  return res.json();
}

export async function fetchTickets(): Promise<EscalationTicket[]> {
  const res = await fetch("/api/tickets");
  if (!res.ok) throw new Error("Failed to load tickets");
  return res.json();
}

export async function fetchFaqs(): Promise<FaqEntry[]> {
  const res = await fetch("/api/faqs");
  if (!res.ok) throw new Error("Failed to load FAQs");
  return res.json();
}

export async function toggleFaqStatus(
  faqId: string,
  status: "active" | "disabled"
): Promise<void> {
  const res = await fetch(`/api/faqs/${faqId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
  if (!res.ok) throw new Error("Failed to update FAQ status");
}

export async function fetchDocuments(): Promise<IndexedDoc[]> {
  const res = await fetch("/api/documents");
  if (!res.ok) throw new Error("Failed to load documents");
  return res.json();
}

export async function fetchDocumentDetail(docId: string): Promise<DocDetail> {
  const res = await fetch(`/api/documents/${docId}`);
  if (!res.ok) throw new Error("Failed to load document detail");
  return res.json();
}

export async function uploadDocx(file: File): Promise<{
  doc_id: string;
  doc_title: string;
  directorate_label: string;
  chunk_count: number;
}> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch("/api/documents/upload", { method: "POST", body: form });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Ошибка загрузки" }));
    throw new Error(err.detail || "Ошибка загрузки файла");
  }
  return res.json();
}

export async function streamChat(
  params: { session_id: string | null; message: string; user_email: string },
  handlers: {
    onSession: (sid: string) => void;
    onStatus: (info: { action: string; directorate: string; reasoning: string }) => void;
    onDelta: (text: string) => void;
    onDone: (msg: ChatMessage) => void;
    onError: (detail: string) => void;
  }
): Promise<void> {
  const res = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!res.ok || !res.body) {
    handlers.onError(`HTTP ${res.status}: не удалось получить ответ от сервера`);
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let currentEvent = "message";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";

    for (const rawLine of lines) {
      const line = rawLine.trimEnd();
      if (line.startsWith("event:")) {
        currentEvent = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        const payloadStr = line.slice(5).trim();
        if (!payloadStr) continue;
        try {
          const data = JSON.parse(payloadStr);
          if (currentEvent === "session") handlers.onSession(data.session_id);
          else if (currentEvent === "status") handlers.onStatus(data);
          else if (currentEvent === "delta") handlers.onDelta(data.text || "");
          else if (currentEvent === "done") handlers.onDone(data.message);
          else if (currentEvent === "error") handlers.onError(data.detail || "Ошибка агента");
        } catch {
          // ignore partial JSON chunk
        }
      }
    }
  }
}
