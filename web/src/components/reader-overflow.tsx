import { useEffect, useRef, type ReactNode } from 'react'

// Enhance the existing Quartz DOM. Markdown/HAST and upstream components stay intact.
export function ReaderOverflow({ children, content }: { children: ReactNode; content: string }) {
  const root = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const host = root.current!
    let frame = 0
    let disposed = false
    const tracked = new Map<HTMLElement, { cleanup: () => void; setOverflow: (value: boolean) => void }>()
    const restore = (element: HTMLElement, name: string, value: string | null) => {
      if (value === null) element.removeAttribute(name)
      else element.setAttribute(name, value)
    }
    const refresh = () => {
      for (const [element, observer] of tracked) {
        if (!host.contains(element)) { observer.cleanup(); tracked.delete(element); continue }
        observer.setOverflow(element.clientWidth > 0 && element.scrollWidth > element.clientWidth + 1)
      }
    }
    const schedule = () => {
      if (disposed) return
      if (!frame) frame = requestAnimationFrame(() => { frame = 0; refresh() })
    }
    const resize = new ResizeObserver(schedule)
    const scan = () => {
      host.querySelectorAll<HTMLElement>('.table-container, pre, pre > code, .katex-display, .katex').forEach(element => {
        if (element.classList.contains('katex') && element.parentElement?.classList.contains('katex-display')) return
        if (tracked.has(element)) return
        const original = ['role', 'aria-label', 'tabindex'].map(name => element.getAttribute(name))
        const fit = () => ['role', 'aria-label', 'tabindex'].forEach((name, index) => restore(element, name, original[index]))
        const kind = element.classList.contains('table-container') ? 'Таблица' : ['PRE', 'CODE'].includes(element.tagName) ? 'Код' : 'Формула'
        const child = element.firstElementChild
        const setOverflow = (value: boolean) => {
          if (value === element.hasAttribute('data-horizontal-scroll')) return
          element.toggleAttribute('data-horizontal-scroll', value)
          if (value) {
            element.tabIndex = 0
            element.setAttribute('role', 'region')
            element.setAttribute('aria-label', `${kind}: горизонтальная прокрутка`)
          } else fit()
        }
        const key = (event: KeyboardEvent) => {
          if (event.target !== element || !element.hasAttribute('data-horizontal-scroll')) return
          if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
            event.preventDefault()
            element.scrollBy({ left: event.key === 'ArrowRight' ? 80 : -80 })
          }
        }
        element.addEventListener('keydown', key)
        resize.observe(element)
        if (child) resize.observe(child)
        tracked.set(element, { setOverflow, cleanup() {
          setOverflow(false)
          element.removeEventListener('keydown', key)
          resize.unobserve(element)
          if (child) resize.unobserve(child)
        } })
      })
      schedule()
    }
    const mutation = new MutationObserver(scan)
    mutation.observe(host, { childList: true, subtree: true })
    host.addEventListener('load', schedule, true)
    resize.observe(host)
    document.fonts.ready.then(schedule)
    scan()
    return () => {
      disposed = true
      cancelAnimationFrame(frame)
      mutation.disconnect()
      resize.disconnect()
      host.removeEventListener('load', schedule, true)
      tracked.forEach(observer => observer.cleanup())
    }
  }, [content])
  return <div className="reader-overflow-root" ref={root}>{children}</div>
}
