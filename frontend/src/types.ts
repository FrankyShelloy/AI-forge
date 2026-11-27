export interface Citation {
  tag: string;
  source_type: "official_regulation" | "quality_faq";
  doc_id: string;
  doc_title: string;
  directorate: string;
  directorate_label: string;
  section_num: string;
  section_title: string;
  clause_num: string;
  score: number;
  excerpt: string;
  parent_context: string;
  external_doc_refs: string[];
  ticket_code?: string | null;
  verified_by?: string | null;
  answered_at?: string | null;
}

export interface AgentTraceStep {
  step: string;
  title: string;
  duration_ms: number;
  detail: string;
}

export interface ChatMessage {
  id: string;
  session_id: string;
  role: "user" | "assistant";
  content: string;
  outcome?: string | null;
  citations: Citation[];
  clarification_options: string[];
  trace?: AgentTraceStep[];
  has_conflict?: boolean;
  conflict_note?: string;
  ticket_code?: string | null;
  prompt_tokens?: number;
  completion_tokens?: number;
  embedding_tokens?: number;
  cost_rub?: number;
  latency_ms?: number;
  created_at?: string;
}

export interface SessionInfo {
  id: string;
  title: string;
  user_email: string;
  clarification_round: number;
  active_directorate?: string | null;
  created_at: string;
  updated_at: string;
}

export interface EscalationTicket {
  id: string;
  ticket_code: string;
  session_id: string;
  user_email: string;
  question: string;
  context_summary: string;
  directorate?: string | null;
  status: string;
  expert_email?: string | null;
  expert_reply_clean?: string | null;
  linked_doc_title?: string | null;
  created_at: string;
  answered_at?: string | null;
}

export interface FaqEntry {
  id: string;
  ticket_code: string;
  question: string;
  answer: string;
  directorate: string;
  linked_doc_title: string;
  verified_by: string;
  status: "active" | "disabled";
  usage_count: number;
  created_at: string;
}

export interface IndexedDoc {
  doc_id: string;
  filename: string;
  doc_title: string;
  directorate: string;
  directorate_label: string;
  stage: string;
  chunk_count: number;
  indexed_at: string;
}

export interface DocClause {
  chunk_id: string;
  clause_num: string;
  raw_text: string;
  external_doc_refs: string[];
}

export interface DocSection {
  section_num: string;
  section_title: string;
  clauses: DocClause[];
}

export interface DocDetail extends IndexedDoc {
  sections: DocSection[];
  linked_faqs: FaqEntry[];
}

export interface SecurityEvent {
  id: string;
  event_type: string;
  actor: string;
  ticket_code?: string | null;
  detail: string;
  created_at: string;
}

export interface AnalyticsData {
  summary: {
    total_queries: number;
    answered: number;
    clarified: number;
    escalated: number;
    out_of_scope_or_refused: number;
    resolution_rate_pct: number;
    tickets_total: number;
    tickets_indexed: number;
    faq_active: number;
    faq_total_reuses: number;
    security_blocked_count: number;
    avg_latency_ms: number;
    avg_cost_rub: number;
    total_prompt_tokens: number;
    total_completion_tokens: number;
    total_embedding_tokens: number;
    total_cost_rub: number;
  };
  outcomes: Record<string, number>;
  directorates: Record<string, number>;
  recurring_topics: Array<{
    topic: string;
    count: number;
    escalations: number;
    needs_revision: boolean;
  }>;
  problematic_sections: Array<{
    section: string;
    problem_count: number;
    total_hits: number;
  }>;
  popular_sections: Array<{
    section: string;
    hits: number;
  }>;
  revision_signals: Array<{
    topic: string;
    escalations: number;
    total_asks: number;
    reason: string;
    severity: "high" | "medium";
  }>;
  security_events: SecurityEvent[];
  documents: IndexedDoc[];
}

export interface HealthInfo {
  status: string;
  version?: string;
  llm_model: string;
  embedding_model: string;
  docs_indexed: number;
  chunks_indexed?: number;
  faq_active: number;
  webmail_url: string;
  default_employee_email: string;
  quality_email: string;
}

export interface SystemModelsInfo {
  perimeter_mode: string;
  vram_limit_gb: number;
  estimated_local_vram_gb: number;
  models: Array<{
    role: string;
    id: string;
    hf_repo: string;
    hf_url: string;
    license: string;
    architecture: string;
    vram_q4_gb: number;
  }>;
  integrations: {
    dion_webhook_endpoint: string;
    openapi_docs: string;
    mail_whitelist: string[];
    official_regulation_priority: string;
  };
}
