import { useEffect, useRef, useState } from 'react'
import { api, type DocumentItem } from '../lib/api'

export function DocumentsPanel({
  lang,
  onUploaded,
}: {
  lang: 'ru' | 'en'
  onUploaded: () => void
}) {
  const [docs, setDocs] = useState<DocumentItem[]>([])
  const [dragOver, setDragOver] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const t = {
    drop: lang === 'en' ? 'Drop files or click to upload' : 'Перетащите файлы или нажмите для загрузки',
    formats: lang === 'en' ? 'PDF, Word, Excel, TXT, MD' : 'PDF, Word, Excel, TXT, MD',
    uploading: lang === 'en' ? 'Uploading…' : 'Загрузка…',
    delete: lang === 'en' ? 'Delete' : 'Удалить',
    ready: lang === 'en' ? 'ready' : 'готов',
    processing: lang === 'en' ? 'processing…' : 'обработка…',
    pages: lang === 'en' ? 'p.' : 'стр.',
  }

  async function refresh() {
    try {
      setDocs(await api.listDocuments())
    } catch {
      // ignore
    }
  }

  // Initial load
  useEffect(() => { void refresh() }, [])

  async function upload(files: FileList | File[]) {
    setUploading(true)
    setError(null)
    try {
      for (const file of Array.from(files)) {
        await api.uploadDocument(file)
      }
      await refresh()
      onUploaded()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Upload failed')
    } finally {
      setUploading(false)
    }
  }

  async function remove(id: string) {
    try {
      await api.deleteDocument(id)
      await refresh()
    } catch {
      // ignore
    }
  }

  return (
    <div className="documents-panel">
      <div
        className={`dropzone ${dragOver ? 'over' : ''}`}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault()
          setDragOver(true)
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragOver(false)
          if (e.dataTransfer.files.length) void upload(e.dataTransfer.files)
        }}
      >
        <span className="dropzone-text">{uploading ? t.uploading : t.drop}</span>
        <span className="dropzone-formats">{t.formats}</span>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept=".pdf,.docx,.doc,.txt,.md,.xlsx,.xls"
          hidden
          onChange={(e) => {
            if (e.target.files?.length) void upload(e.target.files)
            e.target.value = ''
          }}
        />
      </div>
      {error && <div className="warnings-box" style={{ marginTop: 8 }}>{error}</div>}
      {docs.length > 0 && (
        <div className="docs-list">
          {docs.map((d) => (
            <div key={d.id} className={`doc-item doc-${d.status}`}>
              <div className="doc-info">
                <span className="doc-name" title={d.filename}>
                  {d.filename}
                </span>
                <span className="doc-meta">
                  {d.status === 'ready' && `${t.pages} ${d.page_count} · ${d.chunk_count} chunks`}
                  {d.status === 'processing' && t.processing}
                  {d.status === 'error' && (d.error || 'error')}
                </span>
              </div>
              <button
                type="button"
                className="icon-btn doc-delete"
                onClick={() => void remove(d.id)}
                title={t.delete}
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
