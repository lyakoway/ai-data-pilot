import { useState } from 'react'
import type { Step } from '../lib/api'

export function AgentTrace({ steps, lang }: { steps: Step[]; lang: 'ru' | 'en' }) {
  const [open, setOpen] = useState(true)
  const [openStep, setOpenStep] = useState<string | null>(null)
  if (steps.length === 0) return null

  const allDone = steps.every((s) => s.status === 'done' || s.status === 'error')
  const label = lang === 'en' ? 'Execution trace' : 'Ход выполнения'

  return (
    <div className="trace-box">
      <button
        type="button"
        className="trace-head"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className="trace-toggle">{open ? '▾' : '▸'}</span>
        <span className="trace-label">
          {label}
          {!allDone && <span className="trace-spinner" />}
        </span>
        <span className="trace-count">
          {steps.filter((s) => s.status === 'done').length}/{steps.length}
        </span>
      </button>
      {open && (
        <ol className="trace-list">
          {steps.map((step) => {
            const isOpen = openStep === step.id
            const hasDetail = step.detail && Object.keys(step.detail).length > 0
            return (
              <li key={step.id} className={`trace-item trace-${step.status}`}>
                <div
                  className={`trace-row ${hasDetail ? 'clickable' : ''}`}
                  onClick={() => hasDetail && setOpenStep(isOpen ? null : step.id)}
                >
                  <span className="trace-status">
                    {step.status === 'done' ? '✓' : step.status === 'error' ? '✕' : '○'}
                  </span>
                  <span className="trace-title">{step.title}</span>
                  {step.summary && <span className="trace-summary">{step.summary}</span>}
                  {step.duration_ms != null && step.duration_ms > 0 && (
                    <span className="trace-dur">{step.duration_ms}ms</span>
                  )}
                </div>
                {isOpen && hasDetail && step.detail && (
                  <div className="trace-detail">
                    {'sql' in step.detail && (
                      <pre className="trace-sql">{String(step.detail.sql)}</pre>
                    )}
                    {'highlights' in step.detail && Array.isArray(step.detail.highlights) && (
                      <ul className="trace-highlights">
                        {(step.detail.highlights as string[]).map((h, i) => (
                          <li key={i}>{h}</li>
                        ))}
                      </ul>
                    )}
                    {'logic' in step.detail && Boolean(step.detail.logic) && (
                      <p className="trace-logic">{String(step.detail.logic)}</p>
                    )}
                    {'row_count' in step.detail && (
                      <p className="muted">
                        {lang === 'en' ? 'Rows: ' : 'Строк: '}
                        {String(step.detail.row_count as unknown)}
                      </p>
                    )}
                  </div>
                )}
              </li>
            )
          })}
        </ol>
      )}
    </div>
  )
}
