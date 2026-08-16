import { useEffect, useState } from 'react'
import { api } from '../lib/api'

type FeedbackStats = {
  up: number
  down: number
  total: number
  satisfaction: number
  per_agent: Record<string, { up: number; down: number }>
}

type FeedbackEntry = {
  id: number
  vote: 'up' | 'down'
  agent: string
  message: string
  answer: string
  model: string | null
  lang: string
  created_at: string
}

export function FeedbackPanel({ lang }: { lang: 'ru' | 'en' }) {
  const [stats, setStats] = useState<FeedbackStats | null>(null)
  const [entries, setEntries] = useState<FeedbackEntry[]>([])
  const [filter, setFilter] = useState<'all' | 'oleg' | 'ksyusha' | 'down'>('all')
  const [expanded, setExpanded] = useState(false)

  const t = {
    title: lang === 'en' ? 'Feedback' : 'Фидбек',
    thumbsUp: lang === 'en' ? 'Helpful' : 'Полезно',
    thumbsDown: lang === 'en' ? 'Not helpful' : 'Не полезно',
    satisfaction: lang === 'en' ? 'satisfaction' : 'удовлетворённость',
    noData: lang === 'en' ? 'No feedback yet' : 'Пока нет оценок',
    showMore: lang === 'en' ? 'Show recent' : 'Последние оценки',
    showLess: lang === 'en' ? 'Hide' : 'Свернуть',
    all: lang === 'en' ? 'All' : 'Все',
    negative: lang === 'en' ? '👎 only' : 'Только 👎',
  }

  async function refresh() {
    try {
      const [s, e] = await Promise.all([
        fetch('/api/feedback/stats').then((r) => r.json()),
        fetch(`/api/feedback/list?limit=20`).then((r) => r.json()),
      ])
      setStats(s)
      setEntries(e)
    } catch {
      // ignore
    }
  }

  useEffect(() => {
    void refresh()
  }, [])

  if (!stats || stats.total === 0) {
    return (
      <div className="feedback-panel">
        <div className="section-label">{t.title}</div>
        <p className="muted" style={{ fontSize: 12, margin: '4px 0' }}>{t.noData}</p>
      </div>
    )
  }

  const filtered = entries.filter((e) => {
    if (filter === 'down') return e.vote === 'down'
    if (filter === 'oleg' || filter === 'ksyusha') return e.agent === filter
    return true
  })

  return (
    <div className="feedback-panel">
      <div className="section-label">{t.title}</div>

      <div className="feedback-summary">
        <div className="feedback-score">
          <span className="feedback-pct">{stats.satisfaction}%</span>
          <span className="feedback-sub">{t.satisfaction}</span>
        </div>
        <div className="feedback-bars">
          <div className="feedback-bar-row">
            <span className="feedback-bar-label">👍 {t.thumbsUp}</span>
            <div className="feedback-bar">
              <div
                className="feedback-bar-fill fill-up"
                style={{ width: `${(stats.up / Math.max(stats.total, 1)) * 100}%` }}
              />
            </div>
            <span className="feedback-bar-count">{stats.up}</span>
          </div>
          <div className="feedback-bar-row">
            <span className="feedback-bar-label">👎 {t.thumbsDown}</span>
            <div className="feedback-bar">
              <div
                className="feedback-bar-fill fill-down"
                style={{ width: `${(stats.down / Math.max(stats.total, 1)) * 100}%` }}
              />
            </div>
            <span className="feedback-bar-count">{stats.down}</span>
          </div>
        </div>
      </div>

      {Object.keys(stats.per_agent).length > 1 && (
        <div className="feedback-agents">
          {Object.entries(stats.per_agent).map(([agent, counts]) => (
            <span key={agent} className="feedback-agent-chip">
              {agent}: 👍{counts.up} 👎{counts.down}
            </span>
          ))}
        </div>
      )}

      <button
        type="button"
        className="btn btn-ghost btn-sm feedback-toggle"
        onClick={() => setExpanded((v) => !v)}
      >
        {expanded ? t.showLess : t.showMore}
      </button>

      {expanded && (
        <>
          <div className="feedback-filters">
            {(['all', 'oleg', 'ksyusha', 'down'] as const).map((f) => (
              <button
                key={f}
                type="button"
                className={`feedback-filter ${filter === f ? 'active' : ''}`}
                onClick={() => setFilter(f)}
              >
                {f === 'all' ? t.all : f === 'down' ? t.negative : f}
              </button>
            ))}
          </div>
          <div className="feedback-list">
            {filtered.slice(0, 10).map((e) => (
              <div key={e.id} className={`feedback-entry vote-${e.vote}`}>
                <span className="feedback-vote">{e.vote === 'up' ? '👍' : '👎'}</span>
                <div className="feedback-entry-body">
                  <span className="feedback-msg" title={e.message}>
                    {e.message || '(no question)'}
                  </span>
                  <span className="feedback-meta">
                    {e.agent} · {e.model || 'mock'} · {e.created_at?.split('T')[0]}
                  </span>
                </div>
              </div>
            ))}
            {filtered.length === 0 && (
              <p className="muted" style={{ fontSize: 11, textAlign: 'center' }}>—</p>
            )}
          </div>
        </>
      )}
    </div>
  )
}
