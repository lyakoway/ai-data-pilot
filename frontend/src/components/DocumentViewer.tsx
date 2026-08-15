import { useEffect, useRef, useState } from 'react'
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

  return (
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
          {mode === 'xlsx' && (
            <div className="viewer-fallback">
              <blockquote>{source.snippet}</blockquote>
              <a className="btn btn-primary" href={fileUrl} download>
                Download Excel
              </a>
            </div>
          )}
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
    </div>
  )
}
