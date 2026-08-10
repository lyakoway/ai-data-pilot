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
import { ResultCard } from './components/ResultCard'
import {
  api,
  type AgentId,
  type ChatResult,
  type Kpis,
  type ModelInfo,
  type Scenario,
} from './lib/api'
import { loadLang, loadTheme, saveLang, saveTheme, type Lang, type Theme } from './lib/prefs'
import './App.css'

type Turn = {
  id: string
  role: 'user' | 'assistant'
  text?: string
  result?: ChatResult
}

const COPY = {
  ru: {
    title: 'AI Data Pilot',
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
    emptyOleg:
      'Олег ходит в демо-БД RideGo: строит SQL, таблицу, график и Excel. Запустите сценарий слева или задайте вопрос.',
    emptyKsyusha:
      'Ксюша отвечает по фейковой внутренней документации (метрики, lineage, backend).',
    sendHint: 'Enter — отправить · Shift+Enter — новая строка',
    loading: 'Агент думает…',
    saveName: 'Название сценария',
  },
  en: {
    title: 'AI Data Pilot',
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
    emptyOleg:
      'Oleg queries the RideGo demo DB: SQL, table, chart, Excel. Run a scenario or ask a question.',
    emptyKsyusha:
      'Ksyusha answers from a fake internal docs base (metrics, lineage, backend).',
    sendHint: 'Enter to send · Shift+Enter for newline',
    loading: 'Agent is thinking…',
    saveName: 'Scenario name',
  },
} as const

export default function App() {
  const [theme, setTheme] = useState<Theme>(() => loadTheme())
  const [lang, setLang] = useState<Lang>(() => loadLang())
  const t = COPY[lang]

  const [agent, setAgent] = useState<AgentId>('oleg')
  const [models, setModels] = useState<ModelInfo[]>([])
  const [model, setModel] = useState('mock')
  const [scenarios, setScenarios] = useState<Scenario[]>([])
  const [kpis, setKpis] = useState<Kpis | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [lastUserPrompt, setLastUserPrompt] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)

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
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [turns, loading])

  const visibleScenarios = useMemo(
    () => scenarios.filter((s) => s.agent === agent),
    [scenarios, agent],
  )

  async function ask(message: string, forceExcel = false) {
    const msg = message.trim()
    if (!msg || loading) return
    setLoading(true)
    setInput('')
    setLastUserPrompt(msg)
    const userTurn: Turn = { id: crypto.randomUUID(), role: 'user', text: msg }
    setTurns((prev) => [...prev, userTurn])
    try {
      const result = await api.chat({
        message: msg,
        agent,
        model,
        lang,
        force_excel: forceExcel,
      })
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
            agent,
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

  async function runScenario(sc: Scenario) {
    setAgent(sc.agent as AgentId)
    setLoading(true)
    setLastUserPrompt(sc.prompt)
    setTurns((prev) => [
      ...prev,
      { id: crypto.randomUUID(), role: 'user', text: `▶ ${sc.name}` },
    ])
    try {
      const result = await api.runScenario(sc.id, model, lang)
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
    const created = await api.createScenario({
      name,
      agent,
      description: '',
      prompt: lastUserPrompt,
      chart_type: 'bar',
    })
    setScenarios((prev) => [...prev, created])
  }

  const lastSuggestions =
    [...turns].reverse().find((x) => x.result)?.result?.suggestions ?? []

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-logo">DP</div>
          <div>
            <h1>{t.title}</h1>
            <p>RideGo · Oleg & Ksyusha</p>
          </div>
        </div>

        <div className="agent-switch">
          <button
            type="button"
            className={`agent-btn ${agent === 'oleg' ? 'active' : ''}`}
            onClick={() => setAgent('oleg')}
          >
            Олег
            <small>SQL · Excel</small>
          </button>
          <button
            type="button"
            className={`agent-btn ${agent === 'ksyusha' ? 'active' : ''}`}
            onClick={() => setAgent('ksyusha')}
          >
            Ксюша
            <small>Docs · RAG</small>
          </button>
        </div>

        <div className="section-label">{t.scenarios}</div>
        <div className="scenario-list">
          {visibleScenarios.map((sc) => (
            <button
              key={sc.id}
              type="button"
              className="scenario-item"
              onClick={() => runScenario(sc)}
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
          <div>
            <h2>{agent === 'oleg' ? 'Аналитик Олег' : 'Ксюша'}</h2>
            <p className="sub">{agent === 'oleg' ? t.subtitleOleg : t.subtitleKsyusha}</p>
          </div>
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
        </header>

        <div className="content">
          {agent === 'oleg' && kpis && (
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
              <p>{agent === 'oleg' ? t.emptyOleg : t.emptyKsyusha}</p>
              <div className="suggestions">
                {(agent === 'oleg'
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
                    />
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
    </div>
  )
}
