export const YANDEX_METRIKA_ID = import.meta.env.VITE_YANDEX_METRIKA_ID
export const GA4_MEASUREMENT_ID = import.meta.env.VITE_GA_MEASUREMENT_ID

/**
 * Event names — shared by GA4 events and Yandex Metrika reachGoal goals
 * (same pattern as ai-RAG-chat). Full docs: docs/analytics-events.md.
 */
export const AnalyticsEvent = {
  /** Question sent (composer, suggestion chip, or Excel flow) */
  QUESTION_SENT: 'question_sent',
  /** Successful answer rendered (stream finished without error) */
  ANSWER_RECEIVED: 'answer_received',
  /** Question failed: stream error or error-status result */
  QUESTION_ERROR: 'question_error',
  /** «+ New chat» clears the conversation */
  NEW_CHAT: 'new_chat',
  /** Agent pinned in the sidebar (Атлас / Док) */
  AGENT_PIN: 'agent_pin',
  /** Auto-routing checkbox switched */
  AGENT_MODE_CHANGE: 'agent_mode_change',
  /** LLM picked in the model dropdown */
  MODEL_CHANGE: 'model_change',
  /** Data source picked in the source dropdown */
  DATASOURCE_CHANGE: 'datasource_change',
  /** PostgreSQL / ClickHouse connection modal opened */
  DB_MODAL_OPEN: 'db_modal_open',
  /** CSV/Excel uploaded as a SQL data source */
  FILE_UPLOAD: 'file_upload',
  FILE_UPLOAD_ERROR: 'file_upload_error',
  /** Saved the last answer as a reusable scenario */
  SCENARIO_CREATED: 'scenario_created',
  /** Saved scenario executed */
  SCENARIO_RUN: 'scenario_run',
  /** Sidebar theme switch */
  THEME_TOGGLE: 'theme_toggle',
  /** Sidebar language switch */
  LANGUAGE_TOGGLE: 'language_toggle',
  /** 👍/👎 vote on an answer */
  ANSWER_FEEDBACK: 'answer_feedback',
  /** Source filename clicked → document viewer */
  SOURCE_CLICK: 'source_click',
  /** [n] citation marker clicked in the answer text */
  CITATION_CLICK: 'citation_click',
  /** Excel export downloaded from the result card */
  EXCEL_DOWNLOAD: 'excel_download',
  /** Knowledge-base document uploaded / deleted (Docs panel) */
  DOCUMENT_UPLOAD: 'document_upload',
  DOCUMENT_UPLOAD_ERROR: 'document_upload_error',
  DOCUMENT_DELETE: 'document_delete',
  /** "Model unavailable" modal shown / contact clicked */
  PROVIDER_ERROR_MODAL: 'provider_error_modal',
  PROVIDER_ERROR_CONTACT: 'provider_error_contact',
} as const

export type AnalyticsEventName =
  (typeof AnalyticsEvent)[keyof typeof AnalyticsEvent]
