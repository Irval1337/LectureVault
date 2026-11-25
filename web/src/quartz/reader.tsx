import { useLayoutEffect, useMemo, useRef } from 'react'
import { useLocation, useNavigate, useNavigationType } from 'react-router-dom'
import { h, render, type FunctionComponent } from 'preact'
import { ContentBody } from '@quartz-community/content-page/components'
import { TableOfContents } from '@quartz-community/table-of-contents/components'
import type { QuartzComponentProps } from '@quartz-community/types'
import { unified } from 'unified'
import remarkParse from 'remark-parse'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import remarkRehype from 'remark-rehype'
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize'
import rehypeKatex from 'rehype-katex'
import rehypeSlug from 'rehype-slug'
import { imageDetails } from '../lib/image-size'
import { visit } from 'unist-util-visit'
import { toString } from 'hast-util-to-string'
import type { Root, Element } from 'hast'

const processor = unified().use(remarkParse).use(remarkGfm).use(remarkMath).use(remarkRehype)
  .use(rehypeSanitize, { ...defaultSchema, attributes: { ...defaultSchema.attributes, code: [...(defaultSchema.attributes?.code ?? []), ['className', /^language-./, 'math-inline', 'math-display']] } })
  .use(rehypeKatex, { trust: false, strict: 'ignore', output: 'htmlAndMathml' })
  .use(rehypeSlug, { prefix: 'section-' })

export type TocEntry = { depth: number; slug: string; text: string }
export function compileMarkdown(markdown: string, title?: string) {
  const tree = processor.runSync(processor.parse(markdown)) as Root
  if (tree.children[0]?.type === 'element' && tree.children[0].tagName === 'h1' && toString(tree.children[0]).trim() === title?.trim()) tree.children.shift()
  const toc: TocEntry[] = []
  let slides = 0
  visit(tree, 'element', (node: Element) => {
    if (/^h[2-4]$/.test(node.tagName)) toc.push({ depth: Number(node.tagName[1]) - 2, slug: String(node.properties.id), text: toString(node) })
    if (node.tagName === 'img' && node.properties.src !== '/demo-replication.svg' && !(typeof node.properties.src === 'string' && /^\/api\/assets\/[a-f0-9-]{36}(?:\/slides\/[1-9]\d{0,2}\.jpg)?$/.test(node.properties.src))) {
      node.tagName = 'span'; node.children = [{ type: 'text', value: String(node.properties.alt || 'Внешнее изображение отключено') }]; node.properties = {}
    }
    if (node.tagName === 'img') {
      const { alt, caption, ratio } = imageDetails(String(node.properties.alt ?? ''), String(node.properties.title ?? ''))
      node.properties.alt = alt
      node.properties.title = caption || undefined
      if (String(node.properties.src).includes('/slides/')) {
        node.properties.className = ['reader-slide-image']
        node.properties.loading = slides++ < 2 ? 'eager' : 'lazy'
        if (ratio !== 1) node.properties.style = `--slide-width:${Math.min(100, ratio * 100)}%`
      } else {
        node.properties.loading = 'lazy'
        if (ratio !== 1) node.properties.dataImageScale = ratio
      }
    }
    if (node.tagName === 'a' && typeof node.properties.href === 'string' && /^https?:/.test(node.properties.href)) {
      node.properties.target = '_blank'; node.properties.rel = ['noopener', 'noreferrer']
    }
  })
  return { tree, toc }
}

const OriginalContent = ContentBody() as FunctionComponent<QuartzComponentProps>
const OriginalToc = TableOfContents({ layout: 'legacy' }) as FunctionComponent<QuartzComponentProps>
const readingPositions = new Map<string, number>()

export type ArticleProps = { markdown: string; title: string; onOutline?: (entries: TocEntry[]) => void; preview?: boolean }

export function QuartzArticle({ markdown, title, onOutline, preview = false }: ArticleProps) {
  const host = useRef<HTMLDivElement>(null)
  const compiled = useMemo(() => compileMarkdown(markdown, title), [markdown, title])
  const { key, hash } = useLocation()
  const navigationType = useNavigationType()
  const outline = useRef(onOutline)
  useLayoutEffect(() => { outline.current = onOutline }, [onOutline])
  useLayoutEffect(() => {
    const element = host.current!
    render(h(OriginalContent, { fileData: {}, tree: compiled.tree } as unknown as QuartzComponentProps), element)
    const applySize = (img: HTMLImageElement) => {
      const ratio = Number(img.dataset.imageScale)
      if (!ratio || !img.naturalWidth) return
      img.style.width = `min(${Math.min(100, ratio * 100)}%, ${img.naturalWidth * ratio}px)`
    }
    const onLoad = (event: Event) => { if (event.target instanceof HTMLImageElement) applySize(event.target) }
    element.addEventListener('load', onLoad, true)
    element.querySelectorAll<HTMLImageElement>('img[data-image-scale]').forEach(applySize)
    return () => { element.removeEventListener('load', onLoad, true); render(null, element) }
  }, [compiled])
  useLayoutEffect(() => { outline.current?.(compiled.toc) }, [compiled, key])
  useLayoutEffect(() => {
    if (preview) return
    // The route can be ready before the lazy article has rendered.
    const previous = navigationType === 'POP' ? readingPositions.get(key) : undefined
    let settled = false
    const remember = () => {
      if (!settled) return
      if (!readingPositions.has(key) && readingPositions.size >= 50) {
        readingPositions.delete(readingPositions.keys().next().value!)
      }
      readingPositions.set(key, window.scrollY)
    }
    const frame = requestAnimationFrame(() => {
      if (previous !== undefined) {
        window.scrollTo({ top: previous, behavior: 'instant' })
      } else if (hash) {
        let id: string
        try { id = decodeURIComponent(hash.slice(1)) } catch { id = '' }
        const target = document.getElementById(id)
        if (target && (host.current?.contains(target) || id === 'presentation')) {
          target.scrollIntoView({ block: 'start', behavior: 'instant' })
          target.tabIndex = -1
          target.focus({ preventScroll: true })
        }
      } else if (navigationType !== 'POP') {
        window.scrollTo({ top: 0, behavior: 'instant' })
      }
      settled = true
      remember()
    })
    window.addEventListener('scroll', remember, { passive: true })
    return () => { cancelAnimationFrame(frame); window.removeEventListener('scroll', remember) }
  }, [hash, key, navigationType, preview])
  return <div className="quartz-reader" ref={host} />
}

export function QuartzToc({ entries, onNavigate }: { entries: TocEntry[]; onNavigate?: () => void }) {
  const host = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()
  const location = useLocation()
  useLayoutEffect(() => {
    const element = host.current!
    const follow = (event: MouseEvent) => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return
      const hash = (event.target as HTMLElement).closest('a')?.getAttribute('href')
      if (!hash?.startsWith('#')) return
      event.preventDefault()
      onNavigate?.()
      navigate({ pathname: location.pathname, search: location.search, hash }, { state: location.state })
    }
    element.addEventListener('click', follow)
    return () => element.removeEventListener('click', follow)
  }, [location, navigate, onNavigate])
  useLayoutEffect(() => {
    const element = host.current!
    render(h(OriginalToc, { cfg: { locale: 'ru-RU' }, fileData: { toc: entries, collapseToc: false } } as unknown as QuartzComponentProps), element)
    // The upstream inline script expects Quartz SPA events. Connect its existing
    // anchors to the dynamic reader without importing the global SPA runtime.
    let frame = 0
    const updateActive = () => {
      const headings = entries.map(entry => document.getElementById(entry.slug)).filter((heading): heading is HTMLElement => Boolean(heading))
      const current = headings.filter(heading => heading.getBoundingClientRect().top <= 100).at(-1) ?? headings[0]
      element.querySelectorAll('a').forEach(anchor => {
        if (anchor.getAttribute('href') === `#${current?.id}`) anchor.setAttribute('aria-current', 'location')
        else anchor.removeAttribute('aria-current')
      })
    }
    const schedule = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(updateActive) }
    schedule()
    window.addEventListener('scroll', schedule, { passive: true })
    window.addEventListener('hashchange', schedule)
    return () => { cancelAnimationFrame(frame); window.removeEventListener('scroll', schedule); window.removeEventListener('hashchange', schedule); render(null, element) }
  }, [entries])
  return <nav className="quartz-toc" aria-label="Оглавление конспекта" ref={host} />
}
