import { useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { AnswerStatus, ChatResult } from '../lib/api'
import { api } from '../lib/api'
import { injectCitations } from '../lib/citations'
import { AgentTrace } from './AgentTrace'
import { ChartBlock } from './ChartBlock'

const STATUS_LABEL: Record<AnswerStatus, { ru: string; en: string }> = {
  ok: { ru: 'Реальный ответ', en: 'Live answer' },
  demo: { ru: 'Демо-режим', en: 'Demo mode' },
  partial: { ru: 'С коррекцией', en: 'Self-corrected' },
  error: { ru: 'Ошибка', en: 'Error' },
}

export type FeedbackContext = {
  agent: 'oleg' | 'ksyusha'
  message?: string
  model?: string
  datasource_id?: string
}

/** Highlight query terms in a text fragment using <mark>. */
function highlightTerms(text: string, query?: string): string {
  if (!query) return text
  const terms = query
    .toLowerCase()
    .split(/\s+/)
    .filter((t) => t.length >= 3)
    .map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  if (terms.length === 0) return text
  const re = new RegExp(`(${terms.join('|')})`, 'gi')
  return text.replace(re, '<mark>$1</mark>')
}

export function ResultCard({
  result,
  onSaveScenario,
  lang,
  feedbackContext,
}: {
  result: ChatResult
  onSaveScenario?: () => void
  lang: 'ru' | 'en'
  feedbackContext?: FeedbackContext
}) {
  const [vote, setVote] = useState<'up' | 'down' | null>(null)
  const [voteSaved, setVoteSaved] = useState(false)
  const [openSources, setOpenSources] = useState<Set<number>>(new Set())
  const sourceRefs = useRef<(HTMLDivElement | null)[]>([])
  const status = result.status ?? 'ok'
  const statusClass = `status-badge status-${status}`
  const warnings = result.warnings ?? []
  const sources = result.sources ?? []
  const query = feedbackContext?.message

  function handleCite(n: number) {
    const idx = n - 1
    const el = sourceRefs.current[idx]
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      el.classList.add('flash')
      setTimeout(() => el.classList.remove('flash'), 1400)
      // auto-expand
      setOpenSources((prev) => new Set(prev).add(idx))
    }
  }

  function toggleSource(idx: number) {
    setOpenSources((prev) => {
      const next = new Set(prev)
      if (next.has(idx)) next.delete(idx)
      else next.add(idx)
      return next
    })
  }

  // Markdown component renderers with inline-citation injection (for Ksyusha).
  const mdComponents = sources.length > 0
    ? {
        p: (p: { children?: unknown }) => <p>{injectCitations(p.children as never, sources.length, handleCite)}</p>,
        li: (li: { children?: unknown }) => <li>{injectCitations(li.children as never, sources.length, handleCite)}</li>,
      }
    : {}

  async function handleVote(v: 'up' | 'down') {
    setVote(v)
    setVoteSaved(false)
    if (feedbackContext) {
      try {
        await api.feedback({
          vote: v,
          agent: feedbackContext.agent,
          message: feedbackContext.message,
          answer: result.answer,
          datasource_id: feedbackContext.datasource_id,
          model: feedbackContext.model,
          lang,
        })
        setVoteSaved(true)
      } catch {
        // best-effort
      }
    }
  }

  return (
    <article className="result-card">
      <div className="result-head">
        <span className={statusClass} title={STATUS_LABEL[status][lang === 'en' ? 'en' : 'ru']}>
          {STATUS_LABEL[status][lang === 'en' ? 'en' : 'ru']}
        </span>
      </div>

      {warnings.length > 0 && (
        <div className="warnings-box">
          {warnings.map((w, i) => (
            <p key={i}>⚠ {w}</p>
          ))}
        </div>
      )}

      {result.steps && result.steps.length > 0 && (
        <AgentTrace steps={result.steps} lang={lang} />
      )}

      <div className="result-md">
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
          {result.answer}
        </ReactMarkdown>
      </div>

      {result.explanation && (
        <div className="meta-box">
          <strong>{lang === 'en' ? 'Methodology' : 'Методология'}</strong>
          <p>{result.explanation}</p>
          {result.tables_used?.length > 0 && (
            <p className="muted">
              {lang === 'en' ? 'Tables: ' : 'Таблицы: '}
              {result.tables_used.join(', ')}
            </p>
          )}
        </div>
      )}

      {result.chart && <ChartBlock chart={result.chart} />}

      {result.columns.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                {result.columns.map((c) => (
                  <th key={c}>{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.rows.slice(0, 12).map((row, i) => (
                <tr key={i}>
                  {row.map((cell, j) => (
                    <td key={j}>{cell === null ? '—' : String(cell)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {(result.row_count ?? result.rows.length) > 12 && (
            <div className="muted table-more">
              {lang === 'en' ? 'Showing first 12 rows' : 'Показаны первые 12 строк'}
              {result.row_count ? ` · ${result.row_count} total` : ''}
            </div>
          )}
        </div>
      )}

      {result.sql && (
        <details className="sql-block">
          <summary>{lang === 'en' ? 'Show SQL' : 'Показать SQL'}</summary>
          <pre><code>{result.sql}</code></pre>
        </details>
      )}

      {sources.length > 0 && (
        <div className="sources">
          <div className="sources-title">
            {lang === 'en' ? 'Sources' : 'Источники'} {sources.length}
          </div>
          {sources.map((s, idx) => {
            const isOpen = openSources.has(idx)
            const relevance = s.score != null ? Math.round(s.score * 100) : null
            return (
              <div
                key={s.id}
                ref={(el) => { sourceRefs.current[idx] = el }}
                className="source-item"
              >
                <div className="source-head" onClick={() => toggleSource(idx)}>
                  <span className="source-toggle">{isOpen ? '▾' : '▸'}</span>
                  <strong>[{idx + 1}] {s.title}</strong>
                  {relevance != null && (
                    <span className="source-relevance" title="Relevance">
                      {relevance}%
                    </span>
                  )}
                </div>
                {isOpen && s.full_text ? (
                  <div
                    className="source-full"
                    dangerouslySetInnerHTML={{ __html: highlightTerms(s.full_text, query) }}
                  />
                ) : (
                  <p>{s.snippet}</p>
                )}
              </div>
            )
          })}
        </div>
      )}

      <div className="result-actions">
        {result.excel_url && (
          <a className="btn btn-primary" href={result.excel_url} download>
            {lang === 'en' ? 'Download Excel' : 'Скачать Excel'}
          </a>
        )}
        {onSaveScenario && result.agent === 'oleg' && (
          <button className="btn btn-ghost" type="button" onClick={onSaveScenario}>
            {lang === 'en' ? 'Save as scenario' : 'Сохранить как сценарий'}
          </button>
        )}
        <div className="vote-row">
          <button
            type="button"
            className={`icon-btn vote ${vote === 'up' ? 'active' : ''}`}
            onClick={() => handleVote('up')}
            title="👍"
          >
            👍
          </button>
          <button
            type="button"
            className={`icon-btn vote ${vote === 'down' ? 'active' : ''}`}
            onClick={() => handleVote('down')}
            title="👎"
          >
            👎
          </button>
          {vote && (
            <span className="muted">
              {voteSaved
                ? lang === 'en'
                  ? 'Saved — thanks!'
                  : 'Сохранено — спасибо!'
                : lang === 'en'
                  ? 'Thanks for the rating!'
                  : 'Спасибо за оценку!'}
            </span>
          )}
        </div>
      </div>
    </article>
  )
}
