import { useRef, type ReactNode, type KeyboardEvent } from 'react'

export type TabItem<T extends string> = { value: T; label: ReactNode }
export function TabBar<T extends string>({ label, value, items, onValueChange, panelId, className = '' }: {
  label: string; value: T; items: TabItem<T>[]; onValueChange: (value: T) => void; panelId?: string; className?: string
}) {
  const buttons = useRef<Array<HTMLButtonElement | null>>([])
  function navigate(event: KeyboardEvent, index: number) {
    const next = event.key === 'ArrowRight' ? (index + 1) % items.length
      : event.key === 'ArrowLeft' ? (index + items.length - 1) % items.length
      : event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : -1
    if (next < 0) return
    event.preventDefault()
    onValueChange(items[next].value)
    buttons.current[next]?.focus()
  }
  return <div className={`tab-bar ${className}`} role="tablist" aria-label={label}>{items.map((item, index) => <button key={item.value} ref={element => { buttons.current[index] = element }} type="button" role="tab" id={panelId ? `${panelId}-${item.value}-tab` : undefined} aria-controls={panelId} aria-selected={value === item.value} tabIndex={value === item.value ? 0 : -1} onClick={() => onValueChange(item.value)} onKeyDown={event => navigate(event, index)}>{item.label}</button>)}</div>
}
