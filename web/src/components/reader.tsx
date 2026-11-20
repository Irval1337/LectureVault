import { lazy, Suspense } from 'react'
import { ReaderOverflow } from './reader-overflow'

const Article = lazy(() => import('../quartz/reader').then(m => ({ default: m.QuartzArticle })))
const Toc = lazy(() => import('../quartz/reader').then(m => ({ default: m.QuartzToc })))

export function QuartzArticle({ markdown, title }: { markdown: string; title: string }) {
  return <Suspense fallback={<p className="field-hint" role="status">Открываем конспект…</p>}><ReaderOverflow content={markdown}><Article title={title} markdown={markdown} /></ReaderOverflow></Suspense>
}
export function QuartzToc({ markdown }: { markdown: string }) {
  return <Suspense fallback={<p className="field-hint">Оглавление…</p>}><Toc markdown={markdown} /></Suspense>
}
