import { useEffect, useRef, useState, type ReactNode } from 'react'
import { MoreHorizontal } from 'lucide-react'

type Point = { x: number; y: number }
const origins = new WeakMap<HTMLDetailsElement, Point | null>()

function positionMenu(root: HTMLDetailsElement) {
  const panel = root.querySelector<HTMLElement>('.action-menu-popover')
  const trigger = root.querySelector('summary')
  if (!panel || !trigger) return
  panel.showPopover?.()
  const point = origins.get(root)
  const anchor = trigger.getBoundingClientRect()
  const width = panel.offsetWidth, height = panel.offsetHeight
  const x = point?.x ?? anchor.right - width
  const y = point?.y ?? anchor.bottom + 4
  const top = y + height + 6 <= innerHeight ? y : (point?.y ?? anchor.top) - height - 4
  panel.style.left = `${Math.max(6, Math.min(x, innerWidth - width - 6))}px`
  panel.style.top = `${Math.max(6, top)}px`
  if (origins.has(root)) panel.querySelector<HTMLElement>('a,button')?.focus({ preventScroll: true })
}

export function openRowActions(row: HTMLElement, point?: Point) {
  const root = row.querySelector<HTMLDetailsElement>('details.action-menu')
  if (!root) return
  origins.set(root, point ?? null)
  if (root.open) positionMenu(root)
  else root.open = true
}

export function ActionMenu({ label, children, trigger, className = '' }: {
  label: string; children: ReactNode; trigger?: ReactNode; className?: string
}) {
  const ref = useRef<HTMLDetailsElement>(null)
  const [open, setOpen] = useState(false)
  useEffect(() => {
    const root = ref.current
    if (!open || !root) return
    function close() { if (root) root.open = false }
    const outside = (event: PointerEvent) => { if (!root.contains(event.target as Node)) close() }
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        close()
        root.querySelector('summary')?.focus({ preventScroll: true })
        event.preventDefault()
      }
    }
    document.addEventListener('pointerdown', outside)
    document.addEventListener('keydown', escape)
    window.addEventListener('resize', close)
    return () => {
      document.removeEventListener('pointerdown', outside)
      document.removeEventListener('keydown', escape)
      window.removeEventListener('resize', close)
    }
  }, [open])
  return <details ref={ref} className={`action-menu ${className}`} onKeyDown={event => {
    if (event.key === 'Escape' && event.currentTarget.open) {
      event.preventDefault()
      event.stopPropagation()
      event.currentTarget.open = false
      event.currentTarget.querySelector<HTMLElement>('[popover]')?.hidePopover?.()
      event.currentTarget.querySelector('summary')?.focus({ preventScroll: true })
    }
  }} onToggle={e => {
    const root = e.currentTarget
    if (e.target !== root) return
    setOpen(root.open)
    if (root.open) positionMenu(root)
    else { origins.delete(root); root.querySelector<HTMLElement>('[popover]')?.hidePopover?.() }
  }}>
    <summary className="icon-action" aria-label={label} title={label} onClick={event => {
      const root = ref.current
      if (!root) return
      event.preventDefault()
      root.open = !root.open
      if (root.open) positionMenu(root)
      else root.querySelector<HTMLElement>('[popover]')?.hidePopover?.()
    }}>{trigger ?? <MoreHorizontal size={18}/>}</summary>
    <div popover="manual" className="action-menu-popover" onClick={event => {
      if ((event.target as HTMLElement).closest('button, a') && ref.current) {
        ref.current.open = false
        ref.current.querySelector('summary')?.focus({ preventScroll: true })
      }
    }}>{children}</div>
  </details>
}
