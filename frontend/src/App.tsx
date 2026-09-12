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
import { ClickHouseModal } from './components/ClickHouseModal'
import { DocumentsPanel } from './components/DocumentsPanel'
import { Dropdown, type DropdownOption } from './components/Dropdown'
import { FeedbackPanel } from './components/FeedbackPanel'
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
import { scenarioDescription, scenarioName } from './lib/scenarioI18n'
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
    brandSub: 'RideGo · Oleg & Ksyusha',
    agentOleg: 'Олег',
    agentKsyusha: 'Ксюша',
    titleOleg: 'Аналитик Олег',
    titleKsyusha: 'Ксюша',
    autoModeLabel: 'Авто-выбор агента',
    autoModeHint: 'Роутер сам направляет вопрос Олегу или Ксюше',
    emptyAuto:
      'Задайте вопрос — роутер сам направит его аналитику Олегу (SQL, базы данных) или Ксюше (документация).',
    subtitleOleg: 'Аналитик Олег · SQL, метрики, Excel',
    subtitleKsyusha: 'Ксюша · документация и backend-логика',
    scenarios: 'Сценарии',
    docsLabel: 'Документы',
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
    settings: 'Настройки',
    modelLabel: 'Модель',
    close: 'Закрыть',
    themeLight: 'Светлая тема',
    themeDark: 'Тёмная тема',
    langSwitch: 'English',
  },
  en: {
    title: 'AI Data Pilot',
    subtitleAuto: 'Auto-router · data → Data Agent, docs → Agent (RAG)',
    brandSub: 'RideGo · Data Agent & Agent (RAG)',
    agentOleg: 'Data Agent',
    agentKsyusha: 'Agent (RAG)',
    titleOleg: 'Data Agent',
    titleKsyusha: 'Agent (RAG)',
    autoModeLabel: 'Auto-select agent',
    autoModeHint: 'The router sends each question to Data Agent or Agent (RAG)',
    emptyAuto:
      'Ask anything — the router sends data questions to Data Agent (SQL) and docs questions to Agent (RAG).',
    subtitleOleg: 'Data Agent · SQL, metrics, Excel',
    subtitleKsyusha: 'Agent (RAG) · docs & backend logic',
    scenarios: 'Scenarios',
    docsLabel: 'Documents',
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
      'Data Agent queries the RideGo demo DB: SQL, table, chart, Excel. Run a scenario or ask a question.',
    emptyKsyusha:
      'Agent (RAG) answers from a fake internal docs base (metrics, lineage, backend).',
    sendHint: 'Enter to send · Shift+Enter for newline',
    loading: 'Agent is thinking…',
    saveName: 'Scenario name',
    menu: 'Menu',
    newChat: 'New chat',
    settings: 'Settings',
    modelLabel: 'Model',
    close: 'Close',
    themeLight: 'Light theme',
    themeDark: 'Dark theme',
    langSwitch: 'Русский',
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
  const [datasourceId, setDatasourceId] = useState('auto')
  const [uploading, setUploading] = useState(false)
  const [kpis, setKpis] = useState<Kpis | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [lastUserPrompt, setLastUserPrompt] = useState('')
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [scenarioModal, setScenarioModal] = useState<Scenario | null>(null)
  const [pgModal, setPgModal] = useState(false)
  const [chModal, setChModal] = useState(false)
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

  const closeDrawers = () => {
    setSidebarOpen(false)
    setSettingsOpen(false)
  }

  // The settings drawer only exists below 1420px; if the viewport grows past
  // the breakpoint while it is open, close it so it cannot "reappear" stuck
  // open on the next resize down.
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 1419px)')
    const onChange = () => {
      if (!mq.matches) setSettingsOpen(false)
    }
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  useEffect(() => {
    if (!sidebarOpen && !settingsOpen) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeDrawers()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sidebarOpen, settingsOpen])

  const visibleScenarios = useMemo(
    () => (agentMode === 'auto' ? scenarios : scenarios.filter((s) => s.agent === agent)),
    [scenarios, agent, agentMode],
  )

  // Schema-based suggestions for the active data source. Seeded from the
  // heuristic list, then upgraded via the LLM endpoint (mock → heuristic).
  const [sourceSuggestions, setSourceSuggestions] = useState<string[]>([])
  const [suggestionsLoading, setSuggestionsLoading] = useState(false)
  useEffect(() => {
    if (!datasourceId || datasourceId === 'auto') {
      setSourceSuggestions([])
      setSuggestionsLoading(false)
      return
    }
    const fallback = datasources.find((d) => d.id === datasourceId)?.suggestions?.[lang] ?? []
    setSourceSuggestions(fallback)
    if (datasourceId !== 'ridego') {
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
          // Auto source: show which data source the router picked.
          if (
            step.tool === 'source_router' &&
            step.detail &&
            typeof step.detail.decision === 'string'
          ) {
            setDatasourceId(step.detail.decision)
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
      { id: crypto.randomUUID(), role: 'user', text: `▶ ${scenarioName(sc, lang)}` },
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

  const providerLabels: Record<string, string> = {
    mock: lang === 'en' ? 'Demo' : 'Демо',
    openai: 'OpenAI',
    anthropic: 'Anthropic',
    zai: 'Z.ai',
    ollama: 'Ollama',
  }
  const datasourceOptions: DropdownOption[] = [
    { value: 'auto', label: lang === 'en' ? 'Auto source' : 'Авто-источник' },
    ...datasources.map((d) => ({
      value: d.id,
      label: d.name,
      hint: d.row_count != null
        ? `${d.row_count} ${lang === 'en' ? 'rows' : 'строк'}`
        : undefined,
    })),
  ]
  const modelOptions: DropdownOption[] = models.map((m) => ({
    value: m.id,
    label: m.label,
    hint: providerLabels[m.provider] ?? m.provider,
    disabled: !m.available,
    badge: <span className={`dot ${m.available ? 'dot-on' : 'dot-off'}`} />,
  }))

  // The same controls render in the topbar (desktop) and in the right-hand
  // settings drawer (≤1419px); the topbar copy is hidden via CSS there.
  const datasourceControls = (
    <>
      <Dropdown
        value={datasourceId}
        options={datasourceOptions}
        onChange={(id) => {
          setDatasourceId(id)
          // KPIs are only meaningful for the built-in RideGo source.
          if (id !== 'ridego') {
            setKpis(null)
          } else {
            api.kpis().then(setKpis).catch(() => undefined)
          }
        }}
        icon={
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <ellipse cx="12" cy="5" rx="8" ry="3" />
            <path d="M4 5v14c0 1.66 3.58 3 8 3s8-1.34 8-3V5" />
            <path d="M4 12c0 1.66 3.58 3 8 3s8-1.34 8-3" />
          </svg>
        }
        label={t.dataSource}
      />
      <button
        type="button"
        className="btn btn-ghost btn-sm db-btn"
        onClick={() => setPgModal(true)}
        title={lang === 'en'
          ? 'PostgreSQL — for transactional data: users, orders, records. Best for point lookups and updates.'
          : 'PostgreSQL — для транзакционных данных: пользователи, заказы, записи. Быстрый поиск и обновление.'}
      >
        PostgreSQL
      </button>
      <button
        type="button"
        className="btn btn-ghost btn-sm db-btn"
        onClick={() => setChModal(true)}
        title={lang === 'en'
          ? 'ClickHouse — for analytics on billions of rows: reports, trends, aggregations. Blazing fast GROUP BY.'
          : 'ClickHouse — для аналитики на миллиардах строк: отчёты, тренды, агрегации. Мгновенный GROUP BY.'}
      >
        ClickHouse
      </button>
    </>
  )
  const modelControl = (
    <Dropdown
      value={model}
      options={modelOptions}
      onChange={setModel}
      align="right"
      icon={
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" aria-hidden="true">
          <path d="M12 3l1.9 5.8L20 10.5l-5.4 3.6L16 20l-4-3.5L8 20l1.4-5.9L4 10.5l6.1-1.7z" />
        </svg>
      }
      label={t.modelLabel}
    />
  )

  return (
    <div className="app">
      {(sidebarOpen || settingsOpen) && (
        <button
          type="button"
          className="sidebar-backdrop"
          aria-label={t.close}
          onClick={closeDrawers}
        />
      )}
      <aside className={`sidebar ${sidebarOpen ? 'open' : ''}`}>
        <div className="brand">
          <div className="brand-logo">DP</div>
          <div>
            <h1>{t.title}</h1>
            <p>{t.brandSub}</p>
          </div>
          <button
            type="button"
            className="icon-btn brand-close"
            aria-label={t.close}
            onClick={() => setSidebarOpen(false)}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
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
            {t.agentOleg}
            <small>SQL · Excel</small>
          </button>
          <button
            type="button"
            className={`agent-btn ${agent === 'ksyusha' ? 'active' : ''}`}
            onClick={() => selectAgent('ksyusha')}
          >
            {t.agentKsyusha}
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

        <div className="section-label">{t.docsLabel}</div>
        <DocumentsPanel
          lang={lang}
          onUploaded={() => {
            // Refresh data sources too (CSV/Excel files create SQL sources).
            api.datasources().then(setDatasources).catch(() => undefined)
          }}
        />

        <FeedbackPanel lang={lang} />

        <div className="section-label" style={{ marginTop: 8 }}>{t.scenarios}</div>
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
              <strong>{scenarioName(sc, lang)}</strong>
              <span>{scenarioDescription(sc, lang)}</span>
              <div className="run">{t.run} →</div>
            </button>
          ))}
        </div>

        <div className="sidebar-footer">
          <button
            type="button"
            className="theme-toggle"
            onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
            title={theme === 'dark' ? t.themeLight : t.themeDark}
          >
            {theme === 'dark' ? (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                <circle cx="12" cy="12" r="4" />
                <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
              </svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" aria-hidden="true">
                <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
              </svg>
            )}
            {theme === 'dark' ? t.themeLight : t.themeDark}
          </button>
          <button
            type="button"
            className="theme-toggle"
            onClick={() => setLang(lang === 'ru' ? 'en' : 'ru')}
            title={t.langSwitch}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
              <circle cx="12" cy="12" r="10" />
              <path d="M2 12h20" />
              <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
            </svg>
            {/* Like ai-RAG-chat: show the language you will switch to. */}
            {lang === 'ru' ? 'English' : 'Русский'}
          </button>
        </div>
      </aside>

      <aside
        className={`settings-drawer ${settingsOpen ? 'open' : ''}`}
        aria-label={t.settings}
      >
        <div className="settings-head">
          <strong>{t.settings}</strong>
          <button
            type="button"
            className="icon-btn"
            aria-label={t.close}
            onClick={() => setSettingsOpen(false)}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>
        <div className="section-label">{t.dataSource}</div>
        <div className="settings-group">{datasourceControls}</div>
        <div className="section-label">{t.modelLabel}</div>
        <div className="settings-group">{modelControl}</div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div className="topbar-left">
            <button
              type="button"
              className="icon-btn menu-btn"
              onClick={() => {
                setSidebarOpen(true)
                setSettingsOpen(false)
              }}
              aria-label={t.menu}
            >
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" aria-hidden="true">
                <path d="M4 7h16M4 12h16M4 17h16" />
              </svg>
            </button>
            <div>
              <h2>{agent === 'oleg' ? t.titleOleg : t.titleKsyusha}</h2>
              <p className="sub">
                {agentMode === 'auto'
                  ? t.subtitleAuto
                  : agent === 'oleg'
                    ? t.subtitleOleg
                    : t.subtitleKsyusha}
              </p>
            </div>
          </div>
          <div className="topbar-right">
            <div className="topbar-selects">
              {datasourceControls}
              {modelControl}
            </div>
            <button
              type="button"
              className="icon-btn settings-toggle"
              aria-label={t.settings}
              aria-expanded={settingsOpen}
              onClick={() => {
                setSettingsOpen(true)
                setSidebarOpen(false)
              }}
            >
              <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                <path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58a.49.49 0 0 0 .12-.61l-1.92-3.32a.49.49 0 0 0-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54a.484.484 0 0 0-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96a.49.49 0 0 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.09.63-.09.94s.02.64.07.94l-2.03 1.58a.49.49 0 0 0-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32a.49.49 0 0 0-.12-.61l-2.01-1.58zM12 15.6A3.6 3.6 0 1 1 12 8.4a3.6 3.6 0 0 1 0 7.2z" />
              </svg>
            </button>
          </div>
        </header>

        <div className="content">
          {kpis && (
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
                    ? [...sourceSuggestions.slice(0, 3),
                       lang === 'en' ? 'How is utilization calculated?' : 'Как считается utilization?']
                    : sourceSuggestions.slice(0, 4)
                  : agentMode === 'auto'
                    ? (lang === 'en'
                        ? [
                            'Top-10 cities by rides',
                            'How is utilization calculated?',
                            'Revenue by region for 30 days',
                            'What is the TTL of the Redis pricing cache?',
                          ]
                        : [
                            'Топ-10 городов по поездкам',
                            'Как считается utilization?',
                            'Выручка по регионам за 30 дней',
                            'Какой TTL у Redis pricing cache?',
                          ])
                    : agent === 'oleg'
                      ? (lang === 'en'
                          ? [
                              'Revenue by region for 30 days',
                              'Top-10 cities by rides',
                              'Subscription penetration in InHouse cities',
                            ]
                          : [
                              'Выручка по регионам за 30 дней',
                              'Топ-10 городов по поездкам',
                              'Проникновение подписок в InHouse городах',
                            ])
                      : (lang === 'en'
                          ? [
                              'Where is utilization stored and how is it calculated?',
                              'How does the Redis pricing cache work?',
                              'What does Reset errors do in the admin?',
                            ]
                          : [
                              'Где хранится utilization и как она считается?',
                              'Как работает Redis pricing cache?',
                              'Что делает Reset errors в админке?',
                            ])
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
            api.datasources().then(setDatasources).catch(() => undefined)
            setDatasourceId(source.id)
            setKpis(null)
            window.alert(
              lang === 'en'
                ? `Connected! ${tables} tables found.`
                : `Подключено! Найдено таблиц: ${tables}.`,
            )
          }}
          onClose={() => setPgModal(false)}
        />
      )}

      {chModal && (
        <ClickHouseModal
          lang={lang}
          onAdded={(source, tables) => {
            setChModal(false)
            api.datasources().then(setDatasources).catch(() => undefined)
            setDatasourceId(source.id)
            setKpis(null)
            window.alert(
              lang === 'en'
                ? `Connected! ${tables} tables found.`
                : `Подключено! Найдено таблиц: ${tables}.`,
            )
          }}
          onClose={() => setChModal(false)}
        />
      )}
    </div>
  )
}
