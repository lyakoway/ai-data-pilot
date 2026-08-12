export type AgentId = 'oleg' | 'ksyusha'

export type ModelInfo = {
  id: string
  provider: string
  label: string
  available: boolean
  description: string
}

export type Scenario = {
  id: string
  name: string
  agent: AgentId
  description: string
  prompt: string
  chart_type: string | null
  datasource_id?: string | null
}

export type DataSourceInfo = {
  id: string
  name: string
  kind: 'ridego' | 'csv'
  description: string
  row_count: number | null
  created_at: string | null
}

export type CsvUploadResult = DataSourceInfo & {
  columns: { name: string; type: string; sqlite_type: string }[]
}

export type ChartPayload = {
  type: 'bar' | 'line' | 'pie'
  x_key: string
  y_key: string
  points: { x: string; y: number }[]
}

export type AnswerStatus = 'ok' | 'demo' | 'partial' | 'error'

export type Insights = {
  is_timeseries?: boolean
  trend?: { direction: 'up' | 'down' | 'flat'; pct: number } | null
  top?: { label: string; value: number; share: number }[]
  outliers?: { label: string; value: number; z: number }[]
  summary?: Record<string, { sum: number; avg: number; min: number; max: number; median: number; count: number }>
  highlights?: string[]
}

export type ChatResult = {
  agent: AgentId
  answer: string
  status?: AnswerStatus
  warnings?: string[]
  insights?: Insights
  sql: string | null
  explanation: string | null
  columns: string[]
  rows: (string | number | null)[][]
  row_count?: number
  chart: ChartPayload | null
  excel_url: string | null
  tables_used: string[]
  sources?: { id: string; title: string; snippet: string }[]
  suggestions: string[]
}

export type Kpis = {
  rides: number
  revenue_rub: number
  users: number
  inhouse_cities: number
  top_cities: { city: string; rides: number }[]
  revenue_by_region: { region: string; revenue: number }[]
}

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || res.statusText)
  }
  return res.json() as Promise<T>
}

export const api = {
  models: () => json<ModelInfo[]>('/api/models'),
  kpis: () => json<Kpis>('/api/dashboard/kpis'),
  scenarios: () => json<Scenario[]>('/api/scenarios'),
  createScenario: (body: Omit<Scenario, 'id'>) =>
    json<Scenario>('/api/scenarios', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  deleteScenario: (id: string) =>
    json<{ ok: boolean }>(`/api/scenarios/${id}`, { method: 'DELETE' }),
  runScenario: (id: string, model: string, lang: string) =>
    json<ChatResult>(`/api/scenarios/${id}/run?model=${encodeURIComponent(model)}&lang=${lang}`, {
      method: 'POST',
    }),
  chat: (body: {
    message: string
    agent: AgentId
    model: string
    lang: string
    force_excel?: boolean
    datasource_id?: string
  }) =>
    json<ChatResult>('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  datasources: () => json<DataSourceInfo[]>('/api/datasources'),
  uploadCsv: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return json<CsvUploadResult>('/api/datasources/upload', { method: 'POST', body: form })
  },
  deleteDatasource: (id: string) =>
    json<{ ok: boolean }>(`/api/datasources/${id}`, { method: 'DELETE' }),
}
