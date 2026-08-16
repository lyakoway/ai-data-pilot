import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../lib/api'

export type ViewerSource = {
  document_id: string
  filename: string
  page: number | null
  snippet?: string
}

function fileExt(filename: string): string {
  const parts = filename.toLowerCase().split('.')
  return parts.length > 1 ? parts[parts.length - 1] : ''
}

function viewerMode(ext: string): 'pdf' | 'docx' | 'text' | 'xlsx' | 'fallback' {
  if (ext === 'pdf') return 'pdf'
  if (ext === 'docx' || ext === 'doc') return 'docx'
  if (['txt', 'md', 'text', 'markdown'].includes(ext)) return 'text'
  if (['xlsx', 'xls'].includes(ext)) return 'xlsx'
  return 'fallback'
}

function XlsxPreview({ fileUrl }: { fileUrl: string }) {
  const [sheets, setSheets] = useState<{ name: string; rows: string[][] }[]>([])
  const [activeSheet, setActiveSheet] = useState(0)
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const MAX_ROWS = 500

  useEffect(() => {
    let cancelled = false
    Promise.all([
      import('xlsx'),
      fetch(fileUrl).then((r) => r.arrayBuffer()),
    ])
      .then(([{ utils, read }, buffer]) => {
        if (cancelled) return
        const wb = read(buffer, { type: 'array' })
        const result = wb.SheetNames.map((name) => {
          const ws = wb.Sheets[name]
          const json = utils.sheet_to_json<string[]>(ws, { header: 1, raw: false })
          return { name, rows: json.slice(0, MAX_ROWS) }
        })
        setSheets(result)
        setStatus('ready')
      })
      .catch(() => {
        if (!cancelled) setStatus('error')
      })
    return () => {
      cancelled = true
    }
  }, [fileUrl])

  if (status === 'loading') return <div className="viewer-status">Loading spreadsheet…</div>
  if (status === 'error') return <div className="viewer-status">Could not load Excel file.</div>
  if (!sheets.length) return <div className="viewer-status">Empty spreadsheet.</div>

  const sheet = sheets[Math.min(activeSheet, sheets.length - 1)]

  return (
    <div className="viewer-xlsx">
      {sheets.length > 1 && (
        <div className="viewer-xlsx-tabs">
          {sheets.map((s, i) => (
            <button
              key={s.name}
              type="button"
              className={`viewer-xlsx-tab ${i === activeSheet ? 'active' : ''}`}
              onClick={() => setActiveSheet(i)}
            >
              {s.name}
            </button>
          ))}
        </div>
      )}
      <table className="viewer-xlsx-table">
        <tbody>
          {sheet.rows.map((row, ri) => (
            <tr key={ri} className={ri === 0 ? 'header-row' : ''}>
              {row.map((cell, ci) => (
                <td key={ci}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {sheet.rows.length >= MAX_ROWS && (
        <div className="viewer-status">Showing first {MAX_ROWS} rows</div>
      )}
    </div>
  )
}

function DocxPreview({ fileUrl }: { fileUrl: string }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')

  useEffect(() => {
    let cancelled = false
    const el = containerRef.current
    if (!el) return

    Promise.all([
      import('docx-preview'),
      fetch(fileUrl).then((r) => r.arrayBuffer()),
    ])
      .then(([{ renderAsync }, buffer]) => {
        if (cancelled) return
        renderAsync(buffer, el, undefined, {
          breakPages: true,
          renderHeaders: true,
          renderFooters: true,
        })
        setStatus('ready')
      })
      .catch(() => {
        if (!cancelled) setStatus('error')
      })

    return () => {
      cancelled = true
    }
  }, [fileUrl])

  return (
    <div className="viewer-docx">
      {status === 'loading' && <div className="viewer-status">Rendering…</div>}
      {status === 'error' && <div className="viewer-status">Could not render DOCX. Download the file instead.</div>}
      <div ref={containerRef} />
    </div>
  )
}

export function DocumentViewer({
  source,
  onClose,
}: {
  source: ViewerSource
  onClose: () => void
}) {
  const ext = fileExt(source.filename)
  const mode = viewerMode(ext)
  const fileUrl = api.documentFileUrl(source.document_id)
  const pdfUrl = source.page ? `${fileUrl}#page=${source.page}&view=FitH` : fileUrl

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  return createPortal(
    <div className="viewer-overlay" onClick={onClose}>
      <div className="viewer" onClick={(e) => e.stopPropagation()}>
        <div className="viewer-header">
          <span className="viewer-filename">{source.filename}</span>
          {source.page != null && <span className="viewer-page">стр. {source.page}</span>}
          <a
            className="viewer-open"
            href={fileUrl}
            target="_blank"
            rel="noopener noreferrer"
          >
            ↗
          </a>
          <button type="button" className="viewer-close" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className="viewer-body">
          {mode === 'pdf' && <iframe className="viewer-frame" src={pdfUrl} title={source.filename} />}
          {mode === 'docx' && <DocxPreview fileUrl={fileUrl} />}
          {mode === 'text' && (
            <iframe className="viewer-frame" src={fileUrl} title={source.filename} />
          )}
          {mode === 'xlsx' && <XlsxPreview fileUrl={fileUrl} />}
          {mode === 'fallback' && (
            <div className="viewer-fallback">
              <blockquote>{source.snippet}</blockquote>
              <a href={fileUrl} target="_blank" rel="noopener noreferrer">
                Download
              </a>
            </div>
          )}
        </div>
      </div>
    </div>,
    document.body,
  )
}
