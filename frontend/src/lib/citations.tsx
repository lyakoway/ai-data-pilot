import type { ReactNode } from 'react'

const CITE_RE = /(\[\d+\])/g

/**
 * Inject clickable citation badges into text nodes.
 * Walks React children recursively, splitting strings on [n] patterns and
 * turning each valid [n] (1..count) into a <button class="cite-ref">.
 */
export function injectCitations(children: ReactNode, count: number, onCite: (n: number) => void): ReactNode {
  if (count === 0) return children

  function process(node: ReactNode, keyPrefix: string): ReactNode {
    if (typeof node === 'string') {
      const parts = node.split(CITE_RE)
      if (parts.length === 1) return node
      return parts.map((part, i) => {
        const m = /^\[(\d+)\]$/.exec(part)
        if (m) {
          const n = parseInt(m[1], 10)
          if (n >= 1 && n <= count) {
            return (
              <button
                key={`${keyPrefix}-${i}`}
                className="cite-ref"
                onClick={(e) => {
                  e.preventDefault()
                  onCite(n)
                }}
              >
                [{n}]
              </button>
            )
          }
        }
        return part
      })
    }
    return node
  }

  if (Array.isArray(children)) {
    return children.map((child, i) => process(child, `c${i}`))
  }
  return process(children, 'c0')
}
