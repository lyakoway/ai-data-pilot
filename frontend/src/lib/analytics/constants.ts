export const YANDEX_METRIKA_ID = import.meta.env.VITE_YANDEX_METRIKA_ID
export const GA4_MEASUREMENT_ID = import.meta.env.VITE_GA_MEASUREMENT_ID

/**
 * Event names — shared by GA4 events and Yandex Metrika reachGoal goals
 * (same pattern as ai-RAG-chat).
 */
export const AnalyticsEvent = {
  QUESTION_SENT: 'question_sent',
} as const

export type AnalyticsEventName =
  (typeof AnalyticsEvent)[keyof typeof AnalyticsEvent]
