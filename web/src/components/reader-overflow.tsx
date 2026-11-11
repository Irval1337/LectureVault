import { useEffect, useRef, type ReactNode } from 'react'

// Enhance the existing Quartz DOM. Markdown/HAST and upstream components stay intact.
export function ReaderOverflow({ children, content }: { children: ReactNode; content: string }) {
  const root = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const host = root.current!
    let frame = 0
    let disposed = false
    const tracked = new Map<HTMLElement, () => void>()
    const restore = (element: HTMLElement, name: string, value: string | null) => {
      if (value === null) element.removeAttribute(name)
      else element.setAttribute(name, value)
    }
    const refresh = () => {
      for (const [element, cleanup] of tracked) {
        if (!host.contains(element)) { cleanup(); tracked.delete(element); continue }
        const overflows = element.clientWidth > 0 && element.scrollWidth > element.clientWidth + 1
        element.toggleAttribute('data-horizontal-scroll', overflows)
        if (overflows) {
          element.tabIndex = 0
          element.setAttribute('role', 'region')
          const kind = element.classList.contains('table-container') ? 'Таблица' : ['PRE', 'CODE'].includes(element.tagName) ? 'Код' : 'Формула'
          element.setAttribute('aria-label', `${kind}: горизонтальная прокрутка`)
          const left = element.scrollLeft > 1
          const right = element.scrollLeft + element.clientWidth < element.scrollWidth - 1
          element.dataset.scrollHint = `${left ? '← ' : ''}Горизонтальная прокрутка${right ? ' →' : ''}`
        } else {
          element.removeAttribute('data-scroll-hint')
          element.dispatchEvent(new Event('reader-scroll-fit'))
        }
      }
    }
    const schedule = () => {
      if (disposed) return
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(refresh)
    }
    const resize = new ResizeObserver(schedule)
    const scan = () => {
      host.querySelectorAll<HTMLElement>('.table-container, pre, pre > code, .katex-display, .katex').forEach(element => {
        if (element.classList.contains('katex') && element.parentElement?.classList.contains('katex-display')) return
        if (tracked.has(element)) return
        const original = ['role', 'aria-label', 'tabindex'].map(name => element.getAttribute(name))
        const fit = () => ['role', 'aria-label', 'tabindex'].forEach((name, index) => restore(element, name, original[index]))
        const key = (event: KeyboardEvent) => {
          if (event.target !== element || !element.hasAttribute('data-horizontal-scroll')) return
          if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
            event.preventDefault()
            element.scrollBy({ left: event.key === 'ArrowRight' ? 80 : -80 })
          }
        }
        element.addEventListener('scroll', schedule, { passive: true })
        element.addEventListener('keydown', key)
        element.addEventListener('reader-scroll-fit', fit)
        resize.observe(element)
        if (element.firstElementChild) resize.observe(element.firstElementChild)
        tracked.set(element, () => {
          fit()
          element.removeAttribute('data-horizontal-scroll')
          element.removeAttribute('data-scroll-hint')
          element.removeEventListener('scroll', schedule)
          element.removeEventListener('keydown', key)
          element.removeEventListener('reader-scroll-fit', fit)
          resize.unobserve(element)
          if (element.firstElementChild) resize.unobserve(element.firstElementChild)
        })
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
      tracked.forEach(cleanup => cleanup())
    }
  }, [content])
  return <div className="reader-overflow-root" ref={root}>{children}</div>
}
