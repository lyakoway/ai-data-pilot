import { useState } from 'react'
import { api } from '../lib/api'
import type { DataSourceInfo } from '../lib/api'

export function ClickHouseModal({
  lang,
  onAdded,
  onClose,
}: {
  lang: 'ru' | 'en'
  onAdded: (source: DataSourceInfo, tables: number) => void
  onClose: () => void
}) {
  const [form, setForm] = useState({
    name: 'ClickHouse (demo)',
    host: 'play.clickhouse.com',
    port: 443,
    database: 'default',
    username: 'explorer',
    password: '',
    secure: true,
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const t = {
    title: lang === 'en' ? 'Connect ClickHouse' : 'Подключить ClickHouse',
    hint:
      lang === 'en'
        ? 'Prefilled with the public ClickHouse Playground — press Connect, or enter your own server.'
        : 'Предзаполнено публичным ClickHouse Playground — нажмите «Подключить», или введите свой сервер.',
    name: lang === 'en' ? 'Display name' : 'Название',
    host: 'Host',
    port: 'Port',
    database: lang === 'en' ? 'Database' : 'База данных',
    username: lang === 'en' ? 'User' : 'Пользователь',
    password: lang === 'en' ? 'Password' : 'Пароль',
    connect: lang === 'en' ? 'Connect' : 'Подключить',
    connecting: lang === 'en' ? 'Connecting…' : 'Подключаюсь…',
    cancel: lang === 'en' ? 'Cancel' : 'Отмена',
  }

  async function handleConnect() {
    if (!form.host || !form.database) {
      setError('Host and database are required')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const res = await fetch('/api/datasources/clickhouse', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: form.name.trim() || `${form.database}@${form.host}`,
          host: form.host.trim(),
          port: Number(form.port) || 8123,
          database: form.database.trim(),
          username: form.username.trim(),
          password: form.password,
          secure: form.secure,
        }),
      }).then((r) => r.json())
      if (res.detail) throw new Error(res.detail)
      onAdded(
        {
          id: res.id,
          name: res.name,
          kind: 'clickhouse',
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
        <p className="modal-desc">{t.hint}</p>
        <div className="modal-params">
          <label className="modal-field">
            <span className="modal-field-label">{t.name}</span>
            <input
              className="modal-input"
              value={form.name}
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
