import type { Scenario, ScenarioParameter } from './api'

type ParamI18n = { label?: string; options?: string[] }
type ScenarioI18n = {
  name?: string
  description?: string
  params?: Record<string, ParamI18n>
}

// English display strings for the seeded demo scenarios (their ids are
// stable). User-created scenarios have no entry here and fall back to the
// stored text. Select option VALUES stay Russian — they are substituted into
// the Russian LLM prompts; only the visible label is translated.
const EN: Record<string, ScenarioI18n> = {
  'sales-by-region': {
    name: 'Revenue by group',
    description: 'Revenue for a period with grouping and a chart',
    params: {
      period: { label: 'Period (days)' },
      group_by: { label: 'Group by', options: ['region', 'city', 'month'] },
    },
  },
  'top-cities-rides': {
    name: 'Top cities',
    description: 'City ranking by the selected metric',
    params: {
      limit: { label: 'How many cities' },
      metric: { label: 'Metric', options: ['rides', 'revenue', 'unique users'] },
    },
  },
  'subscription-penetration': {
    name: 'Subscription penetration (InHouse)',
    description: 'Share of active subscribers among city users',
  },
  'cancel-reasons': {
    name: 'Boost subscription cancellations',
    description: 'Distribution of cancel_reason for brand=boost',
  },
  'ksyusha-schema': {
    name: 'Where is utilization stored?',
    description: 'Docs question (Agent RAG)',
  },
}

export function scenarioName(sc: Scenario, lang: 'ru' | 'en'): string {
  return lang === 'en' ? (EN[sc.id]?.name ?? sc.name) : sc.name
}

export function scenarioDescription(sc: Scenario, lang: 'ru' | 'en'): string {
  const base = sc.description || sc.prompt.slice(0, 80)
  return lang === 'en' ? (EN[sc.id]?.description ?? base) : base
}

export function scenarioParamLabel(
  sc: Scenario,
  p: ScenarioParameter,
  lang: 'ru' | 'en',
): string {
  if (lang !== 'en') return p.label || p.name
  return EN[sc.id]?.params?.[p.name]?.label ?? p.label ?? p.name
}

// Translate only the visible label; the submitted value stays as-is.
export function scenarioOptionLabel(
  sc: Scenario,
  p: ScenarioParameter,
  opt: string,
  lang: 'ru' | 'en',
): string {
  if (lang !== 'en') return opt
  const en = EN[sc.id]?.params?.[p.name]?.options
  const i = p.options?.indexOf(opt) ?? -1
  return en && i >= 0 && i < en.length ? en[i] : opt
}
