import { useLayoutEffect, useMemo, useRef } from 'react'
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
  visit(tree, 'element', (node: Element) => {
    if (/^h[2-4]$/.test(node.tagName)) toc.push({ depth: Number(node.tagName[1]) - 2, slug: String(node.properties.id), text: toString(node) })
    if (node.tagName === 'img' && node.properties.src !== '/demo-replication.svg' && !(typeof node.properties.src === 'string' && /^\/api\/assets\/[a-f0-9-]{36}(?:\/slides\/[1-9]\d{0,2}\.jpg)?$/.test(node.properties.src))) {
      node.tagName = 'span'; node.children = [{ type: 'text', value: String(node.properties.alt || 'Внешнее изображение отключено') }]; node.properties = {}
    }
    if (node.tagName === 'img') {
      const { alt, caption, ratio } = imageDetails(String(node.properties.alt ?? ''), String(node.properties.title ?? ''))
      node.properties.alt = alt
      node.properties.title = caption || undefined
      node.properties.loading = 'lazy'
      if (ratio !== 1) node.properties.dataImageScale = ratio
    }
    if (node.tagName === 'a' && typeof node.properties.href === 'string' && /^https?:/.test(node.properties.href)) {
      node.properties.target = '_blank'; node.properties.rel = ['noopener', 'noreferrer']
    }
  })
  return { tree, toc }
}

const OriginalContent = ContentBody() as FunctionComponent<QuartzComponentProps>
const OriginalToc = TableOfContents({ layout: 'legacy' }) as FunctionComponent<QuartzComponentProps>

export type ArticleProps = { markdown: string; title: string; onOutline: (entries: TocEntry[]) => void }

export function QuartzArticle({ markdown, title, onOutline }: ArticleProps) {
  const host = useRef<HTMLDivElement>(null)
  const compiled = useMemo(() => compileMarkdown(markdown, title), [markdown, title])
  useLayoutEffect(() => {
    const element = host.current!
    render(h(OriginalContent, { fileData: {}, tree: compiled.tree } as unknown as QuartzComponentProps), element)
    const applySize = (img: HTMLImageElement) => {
      const ratio = Number(img.dataset.imageScale)
      if (!ratio || !img.naturalWidth) return
      img.style.width = `min(${Math.min(100, ratio * 100)}%, ${img.naturalWidth * ratio}px)`
      if (img.src.includes('/slides/')) img.style.maxHeight = `${52 * Math.min(1, ratio)}vh`
    }
    const onLoad = (event: Event) => { if (event.target instanceof HTMLImageElement) applySize(event.target) }
    element.addEventListener('load', onLoad, true)
    element.querySelectorAll<HTMLImageElement>('img[data-image-scale]').forEach(applySize)
    onOutline(compiled.toc)
    // The document can arrive after the browser's initial fragment navigation.
    const fragment = window.location.hash.slice(1)
    const frame = requestAnimationFrame(async () => {
      if (!fragment) return
      let id: string
      try { id = decodeURIComponent(fragment) } catch { return }
      const target = document.getElementById(id)
      if (!target || (!element.contains(target) && id !== 'presentation')) return
      // Font substitution can move a heading by several paragraphs in a long note.
      await document.fonts.ready
      if (target.isConnected && window.location.hash.slice(1) === fragment) target.scrollIntoView({ block: 'start' })
    })
    return () => { cancelAnimationFrame(frame); element.removeEventListener('load', onLoad, true); render(null, element) }
  }, [compiled, onOutline])
  return <div className="quartz-reader" ref={host} />
}

export function QuartzToc({ entries }: { entries: TocEntry[] }) {
  const host = useRef<HTMLDivElement>(null)
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
