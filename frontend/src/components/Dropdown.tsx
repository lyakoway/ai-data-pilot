import { useEffect, useRef, useState, type ReactNode } from 'react'

export interface DropdownOption {
  value: string
  label: string
  hint?: string
  disabled?: boolean
  badge?: ReactNode
}

interface Props {
  value: string
  options: DropdownOption[]
  onChange: (value: string) => void
  icon?: ReactNode
  label?: string // shown before the selected value
  align?: 'left' | 'right'
}

// Custom select styled after the Dropdown component of ai-RAG-chat.
// In the topbar the menu floats below the trigger; inside the settings
// drawer it expands in place (see .settings-drawer .dropdown-menu in CSS).
export function Dropdown({ value, options, onChange, icon, label, align = 'left' }: Props) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const selected = options.find((o) => o.value === value)

  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        // Stop the event from reaching App's drawer-level Escape handler:
        // with a menu open, Escape must close only the menu, not the drawer.
        e.stopPropagation()
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', close)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div className="dropdown" ref={ref}>
      <button
        type="button"
        className="dropdown-trigger"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {icon}
        {label && <span className="dropdown-label">{label}</span>}
        <span className="dropdown-value">{selected?.label ?? value}</span>
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
          className={`dropdown-caret ${open ? 'up' : ''}`}
        >
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>
      {open && (
        <div className={`dropdown-menu ${align}`}>
          {options.map((o) => (
            <button
              key={o.value}
              type="button"
              className={`dropdown-option ${o.value === value ? 'active' : ''}`}
              disabled={o.disabled}
              onClick={() => {
                onChange(o.value)
                setOpen(false)
              }}
            >
              <span className="dropdown-option-main">
                <span className="dropdown-option-label">{o.label}</span>
                {o.hint && <span className="dropdown-option-hint">{o.hint}</span>}
              </span>
              {o.badge}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
