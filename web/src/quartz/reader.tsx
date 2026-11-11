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
import { visit } from 'unist-util-visit'
import { toString } from 'hast-util-to-string'
import type { Root, Element } from 'hast'

const processor = unified().use(remarkParse).use(remarkGfm).use(remarkMath).use(remarkRehype)
  .use(rehypeSanitize, { ...defaultSchema, attributes: { ...defaultSchema.attributes, code: [...(defaultSchema.attributes?.code ?? []), ['className', /^language-./, 'math-inline', 'math-display']] } })
  .use(rehypeKatex, { trust: false, strict: 'ignore', output: 'htmlAndMathml' })
  .use(rehypeSlug, { prefix: 'section-' })

export type TocEntry = { depth: number; slug: string; text: string }
export function compileMarkdown(markdown: string) {
  const tree = processor.runSync(processor.parse(markdown)) as Root
  if (tree.children[0]?.type === 'element' && tree.children[0].tagName === 'h1') tree.children.shift()
  const toc: TocEntry[] = []
  visit(tree, 'element', (node: Element) => {
    if (/^h[2-4]$/.test(node.tagName)) toc.push({ depth: Number(node.tagName[1]) - 2, slug: String(node.properties.id), text: toString(node) })
    if (node.tagName === 'img' && node.properties.src !== '/demo-replication.svg') {
      node.tagName = 'span'; node.children = [{ type: 'text', value: String(node.properties.alt || 'Внешнее изображение отключено') }]; node.properties = {}
    }
    if (node.tagName === 'a' && typeof node.properties.href === 'string' && /^https?:/.test(node.properties.href)) {
      node.properties.target = '_blank'; node.properties.rel = ['noopener', 'noreferrer']
    }
  })
  return { tree, toc }
}

const OriginalContent = ContentBody() as FunctionComponent<QuartzComponentProps>
const OriginalToc = TableOfContents({ layout: 'legacy' }) as FunctionComponent<QuartzComponentProps>

export function QuartzArticle({ markdown }: { markdown: string }) {
  const host = useRef<HTMLDivElement>(null)
  const compiled = useMemo(() => compileMarkdown(markdown), [markdown])
  useLayoutEffect(() => {
    const element = host.current!
    render(h(OriginalContent, { fileData: {}, tree: compiled.tree } as unknown as QuartzComponentProps), element)
    return () => render(null, element)
  }, [compiled])
  return <div className="quartz-reader" ref={host} />
}

export function QuartzToc({ markdown }: { markdown: string }) {
  const host = useRef<HTMLDivElement>(null)
  const compiled = useMemo(() => compileMarkdown(markdown), [markdown])
  useLayoutEffect(() => {
    const element = host.current!
    render(h(OriginalToc, { cfg: { locale: 'ru-RU' }, fileData: { toc: compiled.toc, collapseToc: false } } as unknown as QuartzComponentProps), element)
    // The upstream inline script expects Quartz SPA events. Connect its existing
    // anchors to the dynamic reader without importing the global SPA runtime.
    let frame = 0
    const updateActive = () => {
      const headings = compiled.toc.map(entry => document.getElementById(entry.slug)).filter((heading): heading is HTMLElement => Boolean(heading))
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
  }, [compiled])
  return <nav className="quartz-toc" aria-label="Оглавление конспекта" ref={host} />
}
