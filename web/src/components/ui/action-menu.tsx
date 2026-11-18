import { useEffect, useRef, type ReactNode } from 'react'
import { MoreHorizontal } from 'lucide-react'

export function ActionMenu({ label, children }: { label: string; children: ReactNode }) {
  const root = useRef<HTMLDetailsElement>(null)
  useEffect(() => {
    const close = (event: PointerEvent) => {
      if (root.current?.open && !root.current.contains(event.target as Node)) root.current.open = false
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && root.current?.open) {
        root.current.open = false
        root.current.querySelector('summary')?.focus()
        event.preventDefault()
      }
    }
    document.addEventListener('pointerdown', close)
    document.addEventListener('keydown', escape)
    return () => { document.removeEventListener('pointerdown', close); document.removeEventListener('keydown', escape) }
  }, [])
  return <details ref={root} className="action-menu">
    <summary className="icon-action" aria-label={label} title={label}><MoreHorizontal size={18} /></summary>
    <div className="action-menu-popover" onClick={event => { if ((event.target as HTMLElement).closest('button, a') && root.current) { root.current.open = false; root.current.querySelector('summary')?.focus() } }}>{children}</div>
  </details>
}
