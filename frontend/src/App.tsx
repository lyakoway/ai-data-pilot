import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { AgentTrace } from './components/AgentTrace'
import { PostgresModal } from './components/PostgresModal'
import { ProviderErrorModal, isProviderError } from './components/ProviderErrorModal'
import { ResultCard } from './components/ResultCard'
import { ScenarioModal } from './components/ScenarioModal'
import {
  api,
  type AgentId,
  type ChatResult,
  type DataSourceInfo,
  type Kpis,
  type ModelInfo,
  type Scenario,
  type Step,
} from './lib/api'
import { loadLang, loadTheme, saveLang, saveTheme, type Lang, type Theme } from './lib/prefs'
import './App.css'

type Turn = {
  id: string
  role: 'user' | 'assistant'
  text?: string
  result?: ChatResult
  liveSteps?: Step[]  // streaming steps shown before the final result arrives
}

const COPY = {
  ru: {
    title: 'AI Data Pilot',
    subtitleAuto: 'Авто-роутер · данные → Олег, документация → Ксюша',
    autoModeLabel: 'Авто-выбор агента',
    autoModeHint: 'Роутер сам направляет вопрос Олегу или Ксюше',
    emptyAuto:
      'Задайте вопрос — роутер сам направит его аналитику Олегу (SQL, базы данных) или Ксюше (документация).',
    subtitleOleg: 'Аналитик Олег · SQL, метрики, Excel',
    subtitleKsyusha: 'Ксюша · документация и backend-логика',
    scenarios: 'Сценарии',
    run: 'Запустить',
    kpis: 'Обзор RideGo',
    topCities: 'Топ городов по поездкам',
    byRegion: 'Выручка по регионам',
    placeholderOleg: 'Спросите про выручку, города, подписки…',
    placeholderKsyusha: 'Спросите про utilization, Redis, anti-fraud…',
    emptyTitle: 'Дашборд аналитических агентов',
    dataSource: 'Источник данных',
    uploadCsv: 'Загрузить файл',
    uploading: 'Загрузка…',
    uploadHint: 'CSV или Excel (.xlsx) с заголовком. Максимум 25 МБ.',
    uploadError: 'Не удалось загрузить файл',
    loadingSuggestions: 'Подбираю вопросы по вашим данным…',
    emptyOleg:
      'Олег ходит в демо-БД RideGo: строит SQL, таблицу, график и Excel. Запустите сценарий слева или задайте вопрос.',
    emptyKsyusha:
      'Ксюша отвечает по фейковой внутренней документации (метрики, lineage, backend).',
    sendHint: 'Enter — отправить · Shift+Enter — новая строка',
    loading: 'Агент думает…',
    saveName: 'Название сценария',
    menu: 'Меню',
    newChat: 'Новый чат',
  },
  en: {
    title: 'AI Data Pilot',
    subtitleAuto: 'Auto-router · data → Oleg, docs → Ksyusha',
    autoModeLabel: 'Auto-select agent',
    autoModeHint: 'The router sends each question to Oleg or Ksyusha',
    emptyAuto:
      'Ask anything — the router sends data questions to Oleg (SQL) and docs questions to Ksyusha.',
    subtitleOleg: 'Analyst Oleg · SQL, metrics, Excel',
    subtitleKsyusha: 'Ksyusha · docs & backend logic',
    scenarios: 'Scenarios',
    run: 'Run',
    kpis: 'RideGo overview',
    topCities: 'Top cities by rides',
    byRegion: 'Revenue by region',
    placeholderOleg: 'Ask about revenue, cities, subscriptions…',
    placeholderKsyusha: 'Ask about utilization, Redis, anti-fraud…',
    emptyTitle: 'Analytical agents dashboard',
    dataSource: 'Data source',
    uploadCsv: 'Upload file',
    uploading: 'Uploading…',
    uploadHint: 'CSV or Excel (.xlsx) with a header row. Max 25 MB.',
    uploadError: 'Failed to upload file',
    loadingSuggestions: 'Picking questions for your data…',
    emptyOleg:
      'Oleg queries the RideGo demo DB: SQL, table, chart, Excel. Run a scenario or ask a question.',
    emptyKsyusha:
      'Ksyusha answers from a fake internal docs base (metrics, lineage, backend).',
    sendHint: 'Enter to send · Shift+Enter for newline',
    loading: 'Agent is thinking…',
    saveName: 'Scenario name',
    menu: 'Menu',
    newChat: 'New chat',
  },
} as const

export default function App() {
  const [theme, setTheme] = useState<Theme>(() => loadTheme())
  const [lang, setLang] = useState<Lang>(() => loadLang())
  const t = COPY[lang]

  // `agent` is the *highlighted* agent (switched automatically by the router);
  // `agentMode` is hidden: 'auto' (default) routes per question, 'manual' pins
  // the agent until the user clicks the active button again.
  const [agent, setAgent] = useState<AgentId>('oleg')
  const [agentMode, setAgentMode] = useState<'auto' | 'manual'>('auto')
  const [models, setModels] = useState<ModelInfo[]>([])
  const [model, setModel] = useState('mock')
  const [scenarios, setScenarios] = useState<Scenario[]>([])
  const [datasources, setDatasources] = useState<DataSourceInfo[]>([])
  const [datasourceId, setDatasourceId] = useState('ridego')
  const [uploading, setUploading] = useState(false)
  const [kpis, setKpis] = useState<Kpis | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [lastUserPrompt, setLastUserPrompt] = useState('')
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [scenarioModal, setScenarioModal] = useState<Scenario | null>(null)
  const [pgModal, setPgModal] = useState(false)
  const [providerError, setProviderError] = useState<string | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const csvInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    saveTheme(theme)
  }, [theme])

  useEffect(() => {
    saveLang(lang)
  }, [lang])

  useEffect(() => {
    api.models().then((m) => {
      setModels(m)
      const first = m.find((x) => x.available)
      if (first) setModel(first.id)
    })
    api.scenarios().then(setScenarios)
    api.kpis().then(setKpis).catch(() => undefined)
    api.datasources().then(setDatasources).catch(() => undefined)
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [turns, loading])

  const visibleScenarios = useMemo(
    () => (agentMode === 'auto' ? scenarios : scenarios.filter((s) => s.agent === agent)),
    [scenarios, agent, agentMode],
  )

  // Schema-based suggestions for the active data source. Seeded from the
  // heuristic list, then upgraded via the LLM endpoint (mock → heuristic).
  const [sourceSuggestions, setSourceSuggestions] = useState<string[]>([])
  const [suggestionsLoading, setSuggestionsLoading] = useState(false)
  useEffect(() => {
    const fallback = datasources.find((d) => d.id === datasourceId)?.suggestions?.[lang] ?? []
    setSourceSuggestions(fallback)
    if (datasourceId && datasourceId !== 'ridego') {
      setSuggestionsLoading(true)
      api
        .sourceSuggestions(datasourceId, model, lang)
        .then((r) => setSourceSuggestions(r.suggestions))
        .catch(() => undefined)
        .finally(() => setSuggestionsLoading(false))
    } else {
      setSuggestionsLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [datasourceId, model, lang])

  function selectAgent(target: AgentId) {
    // Clicking an agent pins it and turns auto-routing off (checkbox unchecks);
    // re-enabling auto is done via the checkbox itself.
    setAgent(target)
    setAgentMode('manual')
  }

  async function ask(message: string, forceExcel = false) {
    const msg = message.trim()
    if (!msg || loading) return
    setLoading(true)
    setInput('')
    setLastUserPrompt(msg)
    const userTurn: Turn = { id: crypto.randomUUID(), role: 'user', text: msg }
    setTurns((prev) => [...prev, userTurn])

    // Both Олег and Ксюша stream execution-trace steps in real time.
    const assistantId = crypto.randomUUID()
    setTurns((prev) => [...prev, { id: assistantId, role: 'assistant', liveSteps: [] }])
    await api.chatStream(
      {
        message: msg,
        agent: agentMode === 'auto' ? 'auto' : agent,
        model,
        lang,
        force_excel: forceExcel,
        datasource_id: agent !== 'ksyusha' ? datasourceId : undefined,
      },
      {
        onStep: (step) => {
          // Auto mode: highlight the agent the router picked, right away.
          if (
            agentMode === 'auto' &&
            step.tool === 'router' &&
            step.detail &&
            typeof step.detail.decision === 'string'
          ) {
            setAgent(step.detail.decision as AgentId)
          }
          setTurns((prev) =>
            prev.map((t) => {
              if (t.id !== assistantId) return t
              const existing = t.liveSteps ?? []
              const idx = existing.findIndex((s) => s.id === step.id)
              const next = idx >= 0
                ? existing.map((s, i) => (i === idx ? step : s))
                : [...existing, step]
              return { ...t, liveSteps: next }
            }),
          )
        },
        onDone: (result) => {
          setTurns((prev) =>
            prev.map((t) => (t.id === assistantId ? { ...t, result, liveSteps: undefined } : t)),
          )
          if (agentMode === 'auto') setAgent(result.agent)
          if (result.status === 'error' && isProviderError(result.answer)) {
            setProviderError(result.answer)
          }
          setLoading(false)
        },
        onError: (errMsg) => {
          if (isProviderError(errMsg)) setProviderError(errMsg)
          setTurns((prev) =>
            prev.map((t) =>
              t.id === assistantId
                ? {
                    ...t,
                    liveSteps: undefined,
                    result: {
                      agent: agent === 'ksyusha' ? 'ksyusha' : 'oleg',
                      status: 'error',
                      warnings: [],
                      insights: {},
                      steps: [],
                      answer: errMsg,
                      sql: null,
                      explanation: null,
                      columns: [],
                      rows: [],
                      chart: null,
                      excel_url: null,
                      tables_used: [],
                      suggestions: [],
                    },
                  }
                : t,
            ),
          )
          setLoading(false)
        },
      },
    )
  }

  function runScenario(sc: Scenario) {
    // If the scenario has parameters, open the modal form; otherwise run now.
    if (sc.parameters && sc.parameters.length > 0) {
      setScenarioModal(sc)
      return
    }
    void _executeScenario(sc)
  }

  async function _executeScenario(sc: Scenario, values?: Record<string, string | number>) {
    setScenarioModal(null)
    setAgent(sc.agent as AgentId)
    setLoading(true)
    const displayPrompt = values
      ? Object.entries(values).reduce(
          (p, [k, v]) => p.replace(`{${k}}`, String(v)),
          sc.prompt,
        )
      : sc.prompt
    setLastUserPrompt(displayPrompt)
    setTurns((prev) => [
      ...prev,
      { id: crypto.randomUUID(), role: 'user', text: `▶ ${sc.name}` },
    ])
    try {
      const result = await api.runScenario(sc.id, model, lang, values)
      setTurns((prev) => [
        ...prev,
        { id: crypto.randomUUID(), role: 'assistant', result },
      ])
    } catch (e) {
      setTurns((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: 'assistant',
          result: {
            agent: sc.agent,
            answer: e instanceof Error ? e.message : 'Error',
            sql: null,
            explanation: null,
            columns: [],
            rows: [],
            chart: null,
            excel_url: null,
            tables_used: [],
            suggestions: [],
          },
        },
      ])
    } finally {
      setLoading(false)
    }
  }

  async function saveAsScenario() {
    if (!lastUserPrompt) return
    const name = window.prompt(t.saveName, lastUserPrompt.slice(0, 48))
    if (!name) return
    // In auto mode, bind the scenario to the agent that actually answered.
    const lastAgent: AgentId =
      [...turns].reverse().find((x) => x.result)?.result?.agent ??
      (agent === 'ksyusha' ? 'ksyusha' : 'oleg')
    const created = await api.createScenario({
      name,
      agent: lastAgent,
      description: '',
      prompt: lastUserPrompt,
      chart_type: 'bar',
      datasource_id: agent !== 'ksyusha' ? datasourceId : undefined,
    })
    setScenarios((prev) => [...prev, created])
  }

  async function handleFileUpload(file: File) {
    setUploading(true)
    try {
      const result = await api.uploadFile(file)
      // Refetch to pick up schema-based suggestions for the new source.
      api.datasources().then(setDatasources).catch(() => undefined)
      const first = result.sources[0]
      if (first) {
        setDatasourceId(first.id)
        // KPIs are RideGo-specific; switch them off for non-RideGo sources.
        if (first.id !== 'ridego') setKpis(null)
      }
    } catch (e) {
      window.alert(`${t.uploadError}: ${e instanceof Error ? e.message : ''}`)
    } finally {
      setUploading(false)
    }
  }

  const lastSuggestions =
    [...turns].reverse().find((x) => x.result)?.result?.suggestions ?? []

  return (
    <div className="app">
      {sidebarOpen && (
        <button
          type="button"
          className="sidebar-backdrop"
          aria-label="Close menu"
          onClick={() => setSidebarOpen(false)}
        />
      )}
      <aside className={`sidebar ${sidebarOpen ? 'open' : ''}`}>
        <div className="brand">
          <div className="brand-logo">DP</div>
          <div>
            <h1>{t.title}</h1>
            <p>RideGo · Oleg & Ksyusha</p>
          </div>
        </div>

        <button
          type="button"
          className="btn btn-ghost new-chat-btn"
          onClick={() => {
            setTurns([])
            setSidebarOpen(false)
          }}
        >
          + {t.newChat}
        </button>

        <div className="agent-switch">
          <button
            type="button"
            className={`agent-btn ${agent === 'oleg' ? 'active' : ''}`}
            onClick={() => selectAgent('oleg')}
          >
            Олег
            <small>SQL · Excel</small>
          </button>
          <button
            type="button"
            className={`agent-btn ${agent === 'ksyusha' ? 'active' : ''}`}
            onClick={() => selectAgent('ksyusha')}
          >
            Ксюша
            <small>Docs · RAG</small>
          </button>
        </div>

        <label className="auto-mode-toggle" title={t.autoModeHint}>
          <input
            type="checkbox"
            checked={agentMode === 'auto'}
            onChange={(e) => setAgentMode(e.target.checked ? 'auto' : 'manual')}
          />
          {t.autoModeLabel}
        </label>

        <div className="section-label">{t.scenarios}</div>
        <div className="scenario-list">
          {visibleScenarios.map((sc) => (
            <button
              key={sc.id}
              type="button"
              className="scenario-item"
              onClick={() => {
                setSidebarOpen(false)
                void runScenario(sc)
              }}
              disabled={loading}
            >
              <strong>{sc.name}</strong>
              <span>{sc.description || sc.prompt.slice(0, 80)}</span>
              <div className="run">{t.run} →</div>
            </button>
          ))}
        </div>

        <div className="sidebar-footer">
          <button
            type="button"
            className="icon-btn"
            onClick={() => setLang(lang === 'ru' ? 'en' : 'ru')}
            title="Language"
          >
            {lang === 'ru' ? 'EN' : 'RU'}
          </button>
          <button
            type="button"
            className="icon-btn"
            onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
            title="Theme"
          >
            {theme === 'dark' ? '☀' : '☾'}
          </button>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div className="topbar-left">
            <button
              type="button"
              className="icon-btn menu-btn"
              onClick={() => setSidebarOpen(true)}
              aria-label={t.menu}
            >
              ☰
            </button>
            <div>
              <h2>{agent === 'oleg' ? 'Аналитик Олег' : 'Ксюша'}</h2>
              <p className="sub">
                {agentMode === 'auto'
                  ? t.subtitleAuto
                  : agent === 'oleg'
                    ? t.subtitleOleg
                    : t.subtitleKsyusha}
              </p>
            </div>
          </div>
          <div className="topbar-selects">
            {agent !== 'ksyusha' && (
              <select
                className="select"
                value={datasourceId}
                onChange={(e) => {
                  const id = e.target.value
                  setDatasourceId(id)
                  // KPIs are only meaningful for the built-in RideGo source.
                  if (id !== 'ridego') {
                    setKpis(null)
                  } else {
                    api.kpis().then(setKpis).catch(() => undefined)
                  }
                }}
                title={t.dataSource}
              >
                {datasources.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                    {d.row_count != null ? ` · ${d.row_count}` : ''}
                  </option>
                ))}
              </select>
            )}
            {agent !== 'ksyusha' && (
              <>
                <input
                  ref={csvInputRef}
                  type="file"
                  accept=".csv,.xlsx"
                  className="csv-input-hidden"
                  onChange={(e) => {
                    const f = e.target.files?.[0]
                    if (f) void handleFileUpload(f)
                    e.target.value = ''
                  }}
                />
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => csvInputRef.current?.click()}
                  disabled={uploading}
                  title={t.uploadHint}
                >
                  {uploading ? t.uploading : `+ ${t.uploadCsv}`}
                </button>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => setPgModal(true)}
                  title={lang === 'en' ? 'Connect a PostgreSQL database' : 'Подключить базу PostgreSQL'}
                >
                  {lang === 'en' ? 'PostgreSQL' : 'PostgreSQL'}
                </button>
              </>
            )}
            <select
              className="select"
              value={model}
              onChange={(e) => setModel(e.target.value)}
            >
              {models.map((m) => (
                <option key={m.id} value={m.id} disabled={!m.available}>
                  {m.available ? '●' : '○'} {m.label}
                </option>
              ))}
            </select>
          </div>
        </header>

        <div className="content">
          {agent !== 'ksyusha' && kpis && (
            <>
              <div className="section-label">{t.kpis}</div>
              <div className="kpi-grid">
                <div className="kpi">
                  <div className="label">Rides</div>
                  <div className="value">{kpis.rides.toLocaleString('ru-RU')}</div>
                </div>
                <div className="kpi">
                  <div className="label">Revenue ₽</div>
                  <div className="value">{kpis.revenue_rub.toLocaleString('ru-RU')}</div>
                </div>
                <div className="kpi">
                  <div className="label">Users</div>
                  <div className="value">{kpis.users.toLocaleString('ru-RU')}</div>
                </div>
                <div className="kpi">
                  <div className="label">InHouse cities</div>
                  <div className="value">{kpis.inhouse_cities}</div>
                </div>
              </div>

              <div className="dash-charts">
                <div className="panel">
                  <h3>{t.topCities}</h3>
                  <ResponsiveContainer width="100%" height={180}>
                    <BarChart data={kpis.top_cities}>
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                      <XAxis dataKey="city" tick={{ fill: 'var(--text-muted)', fontSize: 11 }} />
                      <YAxis tick={{ fill: 'var(--text-muted)', fontSize: 11 }} />
                      <Tooltip />
                      <Bar dataKey="rides" fill="#6366f1" radius={[6, 6, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
                <div className="panel">
                  <h3>{t.byRegion}</h3>
                  <ResponsiveContainer width="100%" height={180}>
                    <BarChart data={kpis.revenue_by_region}>
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                      <XAxis dataKey="region" tick={{ fill: 'var(--text-muted)', fontSize: 11 }} />
                      <YAxis tick={{ fill: 'var(--text-muted)', fontSize: 11 }} />
                      <Tooltip />
                      <Bar dataKey="revenue" fill="#8b5cf6" radius={[6, 6, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>
            </>
          )}

          {turns.length === 0 && (
            <div className="empty">
              <h3>{t.emptyTitle}</h3>
              <p>
                {agentMode === 'auto' && turns.length === 0
                  ? t.emptyAuto
                  : agent === 'oleg'
                    ? t.emptyOleg
                    : t.emptyKsyusha}
              </p>
              {suggestionsLoading ? (
                <div className="suggestions-loading">
                  <span className="trace-spinner" /> {t.loadingSuggestions}
                </div>
              ) : (
              <div className="suggestions">
                {(sourceSuggestions.length > 0
                  ? agentMode === 'auto' && agent !== 'oleg'
                    ? [...sourceSuggestions.slice(0, 3), 'Как считается utilization?']
                    : sourceSuggestions.slice(0, 4)
                  : agentMode === 'auto'
                    ? [
                        'Топ-10 городов по поездкам',
                        'Как считается utilization?',
                        'Выручка по регионам за 30 дней',
                        'Какой TTL у Redis pricing cache?',
                      ]
                    : agent === 'oleg'
                      ? [
                          'Выручка по регионам за 30 дней',
                          'Топ-10 городов по поездкам',
                          'Проникновение подписок в InHouse городах',
                        ]
                      : [
                          'Где хранится utilization и как она считается?',
                          'Как работает Redis pricing cache?',
                          'Что делает Reset errors в админке?',
                        ]
                ).map((s) => (
                  <button key={s} type="button" className="chip" onClick={() => ask(s)}>
                    {s}
                  </button>
                ))}
              </div>
              )}
            </div>
          )}

          <div className="chat-stream">
            {turns.map((turn) =>
              turn.role === 'user' ? (
                <div key={turn.id} className="bubble-user">
                  {turn.text}
                </div>
              ) : (
                <div key={turn.id} className="bubble-ai-wrap">
                  {turn.result && (
                    <ResultCard
                      result={turn.result}
                      lang={lang}
                      onSaveScenario={saveAsScenario}
                      feedbackContext={{
                        agent: turn.result.agent,
                        message: lastUserPrompt,
                        model,
                        datasource_id: agent !== 'ksyusha' ? datasourceId : undefined,
                      }}
                    />
                  )}
                  {!turn.result && turn.liveSteps && turn.liveSteps.length > 0 && (
                    <div className="result-card live-card">
                      <AgentTrace steps={turn.liveSteps} lang={lang} />
                    </div>
                  )}
                </div>
              ),
            )}
            {loading && (
              <div className="loading">
                <span className="spinner" /> {t.loading}
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          {lastSuggestions.length > 0 && !loading && (
            <div className="suggestions">
              {lastSuggestions.map((s) => (
                <button
                  key={s}
                  type="button"
                  className="chip"
                  onClick={() => {
                    if (s.toLowerCase().includes('excel') || s.toLowerCase().includes('выгруз')) {
                      ask(lastUserPrompt || s, true)
                    } else if (s.toLowerCase().includes('сценари') || s.toLowerCase().includes('scenario')) {
                      saveAsScenario()
                    } else {
                      ask(s)
                    }
                  }}
                >
                  {s}
                </button>
              ))}
            </div>
          )}
        </div>

        <footer className="composer">
          <div className="composer-box">
            <textarea
              value={input}
              placeholder={agent === 'oleg' ? t.placeholderOleg : t.placeholderKsyusha}
              rows={2}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  ask(input)
                }
              }}
            />
            <button
              type="button"
              className="send"
              disabled={loading || !input.trim()}
              onClick={() => ask(input)}
              aria-label="Send"
            >
              ↑
            </button>
          </div>
          <div className="composer-hint">{t.sendHint}</div>
        </footer>
      </main>

      {scenarioModal && (
        <ScenarioModal
          scenario={scenarioModal}
          lang={lang}
          onRun={(values) => void _executeScenario(scenarioModal, values)}
          onClose={() => setScenarioModal(null)}
        />
      )}

      {providerError && (
        <ProviderErrorModal
          message={providerError}
          lang={lang}
          onClose={() => setProviderError(null)}
        />
      )}

      {pgModal && (
        <PostgresModal
          lang={lang}
          onAdded={(source, tables) => {
            setPgModal(false)
            // Refetch to pick up schema-based suggestions for the new source.
            api.datasources().then(setDatasources).catch(() => undefined)
            setDatasourceId(source.id)
            setKpis(null)
            window.alert(
              lang === 'en'
                ? `Connected! ${tables} tables found. Oleg can now query this database.`
                : `Подключено! Найдено таблиц: ${tables}. Олег может делать запросы к этой базе.`,
            )
          }}
          onClose={() => setPgModal(false)}
        />
      )}
    </div>
  )
}
