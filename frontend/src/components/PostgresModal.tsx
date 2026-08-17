import { useState } from 'react'
import { api } from '../lib/api'
import type { DataSourceInfo } from '../lib/api'

export function PostgresModal({
  lang,
  onAdded,
  onClose,
}: {
  lang: 'ru' | 'en'
  onAdded: (source: DataSourceInfo, tables: number) => void
  onClose: () => void
}) {
  // Pre-filled with a public demo database (RNAcentral, EMBL-EBI — read-only, CC0)
  // so the demo connects in one click; every field is editable for a real DB.
  const [form, setForm] = useState({
    name: 'RNAcentral (demo)',
    host: 'hh-pgsql-public.ebi.ac.uk',
    port: 5432,
    database: 'pfmegrnargs',
    username: 'reader',
    password: 'NWDMCE5xdipIjRrp',
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const t = {
    title: lang === 'en' ? 'Connect PostgreSQL' : 'Подключить PostgreSQL',
    hint:
      lang === 'en'
        ? 'For transactional data: users, orders, inventory. Best for point lookups, joins, and frequent updates.'
        : 'Для транзакционных данных: пользователи, заказы, склад. Быстрый поиск, JOIN, частые обновления.',
    demo:
      lang === 'en'
        ? 'Prefilled with a public demo DB — press Connect to try it, or enter your own.'
        : 'Предзаполнено публичной демо-базой — нажмите «Подключить», или введите свои данные.',
    name: lang === 'en' ? 'Display name' : 'Название',
    host: 'Host',
    port: 'Port',
    database: lang === 'en' ? 'Database' : 'База данных',
    username: lang === 'en' ? 'User' : 'Пользователь',
    password: lang === 'en' ? 'Password' : 'Пароль',
    connect: lang === 'en' ? 'Connect' : 'Подключить',
    connecting: lang === 'en' ? 'Connecting…' : 'Подключаюсь…',
    cancel: lang === 'en' ? 'Cancel' : 'Отмена',
    namePlaceholder: lang === 'en' ? 'My analytics DB' : 'Моя аналитическая БД',
  }

  async function handleConnect() {
    if (!form.host || !form.database || !form.username) {
      setError(lang === 'en' ? 'Host, database and user are required' : 'Host, база и пользователь обязательны')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const res = await api.addPostgres({
        name: form.name.trim() || `${form.database}@${form.host}`,
        host: form.host.trim(),
        port: Number(form.port) || 5432,
        database: form.database.trim(),
        username: form.username.trim(),
        password: form.password,
      })
      onAdded(
        {
          id: res.id,
          name: res.name,
          kind: 'postgres',
          description: res.description,
          row_count: null,
          created_at: new Date().toISOString(),
        },
        res.tables,
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Connection failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box" onClick={(e) => e.stopPropagation()}>
        <h3 className="modal-title">{t.title}</h3>
        <div className="db-why">
          <span className="db-why-icon">💡</span>
          <span className="db-why-text">{t.hint}</span>
        </div>
        <p className="modal-desc">{t.demo}</p>
        <div className="modal-params">
          <label className="modal-field">
            <span className="modal-field-label">{t.name}</span>
            <input
              className="modal-input"
              value={form.name}
              placeholder={t.namePlaceholder}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </label>
          <div className="pg-form-row">
            <label className="modal-field pg-form-host">
              <span className="modal-field-label">{t.host}</span>
              <input
                className="modal-input"
                value={form.host}
                onChange={(e) => setForm({ ...form, host: e.target.value })}
              />
            </label>
            <label className="modal-field pg-form-port">
              <span className="modal-field-label">{t.port}</span>
              <input
                className="modal-input"
                type="number"
                value={form.port}
                onChange={(e) => setForm({ ...form, port: Number(e.target.value) })}
              />
            </label>
          </div>
          <label className="modal-field">
            <span className="modal-field-label">{t.database}</span>
            <input
              className="modal-input"
              value={form.database}
              onChange={(e) => setForm({ ...form, database: e.target.value })}
            />
          </label>
          <label className="modal-field">
            <span className="modal-field-label">{t.username}</span>
            <input
              className="modal-input"
              value={form.username}
              onChange={(e) => setForm({ ...form, username: e.target.value })}
            />
          </label>
          <label className="modal-field">
            <span className="modal-field-label">{t.password}</span>
            <input
              className="modal-input"
              type="password"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
            />
          </label>
        </div>
        {error && <div className="warnings-box pg-error">{error}</div>}
        <div className="modal-actions">
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            {t.cancel}
          </button>
          <button type="button" className="btn btn-primary" onClick={handleConnect} disabled={busy}>
            {busy ? t.connecting : t.connect}
          </button>
        </div>
      </div>
    </div>
  )
}
