export type AgentId = 'oleg' | 'ksyusha'

/** 'auto' lets the backend router pick the agent per question. */
export type AgentMode = AgentId | 'auto'

export type DocumentItem = {
  id: string
  filename: string
  content_type: string
  size_bytes: number
  page_count: number
  chunk_count: number
  status: 'processing' | 'ready' | 'error'
  error: string | null
  datasource_id?: string | null
  created_at: string
}

export type ModelInfo = {
  id: string
  provider: string
  label: string
  available: boolean
  description: string
}

export type ScenarioParameter = {
  name: string
  label?: string
  type: 'number' | 'text' | 'select'
  default?: string | number
  options?: string[]
}

export type Scenario = {
  id: string
  name: string
  agent: AgentId
  description: string
  prompt: string
  chart_type: string | null
  datasource_id?: string | null
  parameters?: ScenarioParameter[] | null
}

export type DataSourceInfo = {
  id: string
  name: string
  kind: 'ridego' | 'csv' | 'postgres' | 'clickhouse' | 'virtual'
  description: string
  row_count: number | null
  created_at: string | null
  suggestions?: { ru?: string[]; en?: string[] }
}

export type CsvUploadResult = DataSourceInfo & {
  columns: { name: string; type: string; sqlite_type: string }[]
}

export type UploadResponse = {
  sources: CsvUploadResult[]
  count: number
}

export type ChartPayload = {
  type: 'bar' | 'line' | 'pie'
  x_key: string
  y_key: string
  points: { x: string; y: number }[]
}

export type AnswerStatus = 'ok' | 'demo' | 'partial' | 'error'

export type Step = {
  id: string
  title: string
  tool: 'planner' | 'database_query' | 'analyze' | 'chart' | 'answer' | string
  status: 'running' | 'done' | 'error'
  summary: string | null
  detail: Record<string, unknown> | null
  duration_ms: number | null
}

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
  steps?: Step[]
  sql: string | null
  explanation: string | null
  columns: string[]
  rows: (string | number | null)[][]
  row_count?: number
  chart: ChartPayload | null
  excel_url: string | null
  tables_used: string[]
  sources?: {
    id: string
    title: string
    snippet: string
    full_text?: string
    score?: number
    document_id?: string | null
    filename?: string | null
    page?: number | null
  }[]
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
  runScenario: (id: string, model: string, lang: string, values?: Record<string, string | number>) =>
    json<ChatResult>(`/api/scenarios/${id}/run?model=${encodeURIComponent(model)}&lang=${lang}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ values: values ?? {} }),
    }),
  chat: (body: {
    message: string
    agent: AgentMode
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
  chatStream: async (
    body: {
      message: string
      agent: AgentMode
      model: string
      lang: string
      force_excel?: boolean
      datasource_id?: string
    },
    handlers: {
      onStep: (step: Step) => void
      onDone: (result: ChatResult) => void
      onError: (message: string) => void
    },
  ): Promise<void> => {
    const res = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!res.ok || !res.body) {
      handlers.onError(`HTTP ${res.status}`)
      return
    }
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let currentEvent = 'message'
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      // SSE frames are separated by a blank line.
      const frames = buffer.split('\n\n')
      buffer = frames.pop() ?? ''
      for (const frame of frames) {
        const lines = frame.split('\n')
        let dataLine = ''
        for (const line of lines) {
          if (line.startsWith('event:')) currentEvent = line.slice(6).trim()
          else if (line.startsWith('data:')) dataLine += line.slice(5).trim()
        }
        if (!dataLine) continue
        try {
          const payload = JSON.parse(dataLine)
          if (currentEvent === 'step') handlers.onStep(payload as Step)
          else if (currentEvent === 'done') {
            handlers.onDone(payload as ChatResult)
            return
          } else if (currentEvent === 'error') {
            handlers.onError(payload.message ?? 'Stream error')
            return
          }
        } catch {
          // ignore malformed frames
        }
      }
    }
  },
  datasources: () => json<DataSourceInfo[]>('/api/datasources'),
  uploadFile: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return json<UploadResponse>('/api/datasources/upload', { method: 'POST', body: form })
  },
  deleteDatasource: (id: string) =>
    json<{ ok: boolean }>(`/api/datasources/${id}`, { method: 'DELETE' }),
  addPostgres: (body: {
    name: string
    host: string
    port: number
    database: string
    username: string
    password: string
  }) =>
    json<{ id: string; name: string; kind: string; description: string; tables: number }>(
      '/api/datasources/postgres',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
    ),
  refreshDatasource: (id: string) =>
    json<{ id: string; name: string; tables: number }>(`/api/datasources/${id}/refresh`, {
      method: 'POST',
    }),
  sourceSuggestions: (id: string, model: string, lang: string) =>
    json<{ suggestions: string[] }>(
      `/api/datasources/${id}/suggestions?model=${encodeURIComponent(model)}&lang=${lang}`,
    ),
  listDocuments: () => json<DocumentItem[]>('/api/documents'),
  uploadDocument: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return json<DocumentItem>('/api/documents', { method: 'POST', body: form })
  },
  deleteDocument: (id: string) =>
    json<{ ok: boolean }>(`/api/documents/${id}`, { method: 'DELETE' }),
  documentFileUrl: (id: string) => `/api/documents/${id}/file`,
  feedback: (body: {
    vote: 'up' | 'down'
    agent: AgentId
    message?: string
    answer?: string
    datasource_id?: string
    model?: string
    lang?: 'ru' | 'en'
  }) =>
    json<{ id: number; ok: boolean }>('/api/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
}
