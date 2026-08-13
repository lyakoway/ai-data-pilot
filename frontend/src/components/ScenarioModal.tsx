import { useState } from 'react'
import type { Scenario, ScenarioParameter } from '../lib/api'

export function ScenarioModal({
  scenario,
  lang,
  onRun,
  onClose,
}: {
  scenario: Scenario
  lang: 'ru' | 'en'
  onRun: (values: Record<string, string | number>) => void
  onClose: () => void
}) {
  const params = scenario.parameters ?? []
  // Initialise state from parameter defaults.
  const [values, setValues] = useState<Record<string, string | number>>(() => {
    const init: Record<string, string | number> = {}
    for (const p of params) {
      init[p.name] = p.default ?? (p.type === 'number' ? 0 : '')
    }
    return init
  })

  function handleParam(p: ScenarioParameter, raw: string) {
    setValues((prev) => ({
      ...prev,
      [p.name]: p.type === 'number' ? Number(raw) || 0 : raw,
    }))
  }

  const label = lang === 'en' ? 'Run' : 'Запустить'
  const cancel = lang === 'en' ? 'Cancel' : 'Отмена'

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box" onClick={(e) => e.stopPropagation()}>
        <h3 className="modal-title">{scenario.name}</h3>
        {scenario.description && <p className="modal-desc">{scenario.description}</p>}
        <div className="modal-params">
          {params.map((p) => (
            <label key={p.name} className="modal-field">
              <span className="modal-field-label">{p.label || p.name}</span>
              {p.type === 'select' && p.options ? (
                <select
                  className="modal-input"
                  value={String(values[p.name] ?? '')}
                  onChange={(e) => handleParam(p, e.target.value)}
                >
                  {p.options.map((opt) => (
                    <option key={opt} value={opt}>{opt}</option>
                  ))}
                </select>
              ) : (
                <input
                  className="modal-input"
                  type={p.type === 'number' ? 'number' : 'text'}
                  value={values[p.name] ?? ''}
                  onChange={(e) => handleParam(p, e.target.value)}
                />
              )}
            </label>
          ))}
        </div>
        <div className="modal-actions">
          <button type="button" className="btn btn-ghost" onClick={onClose}>{cancel}</button>
          <button type="button" className="btn btn-primary" onClick={() => onRun(values)}>
            {label} →
          </button>
        </div>
      </div>
    </div>
  )
}
