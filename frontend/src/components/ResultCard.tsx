import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { ChatResult } from '../lib/api'
import { ChartBlock } from './ChartBlock'

export function ResultCard({
  result,
  onSaveScenario,
  lang,
}: {
  result: ChatResult
  onSaveScenario?: () => void
  lang: 'ru' | 'en'
}) {
  const [vote, setVote] = useState<'up' | 'down' | null>(null)

  return (
    <article className="result-card">
      <div className="result-md">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{result.answer}</ReactMarkdown>
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

      {result.sources && result.sources.length > 0 && (
        <div className="sources">
          <div className="sources-title">
            {lang === 'en' ? 'Sources' : 'Источники'} {result.sources.length}
          </div>
          {result.sources.map((s) => (
            <div key={s.id} className="source-item">
              <strong>{s.title}</strong>
              <p>{s.snippet}</p>
            </div>
          ))}
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
            onClick={() => setVote('up')}
            title="👍"
          >
            👍
          </button>
          <button
            type="button"
            className={`icon-btn vote ${vote === 'down' ? 'active' : ''}`}
            onClick={() => setVote('down')}
            title="👎"
          >
            👎
          </button>
          {vote && (
            <span className="muted">
              {lang === 'en' ? 'Thanks for the rating!' : 'Спасибо за оценку!'}
            </span>
          )}
        </div>
      </div>
    </article>
  )
}
