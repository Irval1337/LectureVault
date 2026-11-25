import { lazy, Suspense } from 'react'
import type { ArticleProps, TocEntry } from '../quartz/reader'
import { ReaderOverflow } from './reader-overflow'

const Article = lazy(() => import('../quartz/reader').then(m => ({ default: m.QuartzArticle })))
const Toc = lazy(() => import('../quartz/reader').then(m => ({ default: m.QuartzToc })))

export function QuartzArticle(props: ArticleProps) {
  return <Suspense fallback={<p className="field-hint" role="status">Открываем конспект…</p>}><ReaderOverflow content={props.markdown}><Article {...props} /></ReaderOverflow></Suspense>
}
export function QuartzToc(props: { entries: TocEntry[]; onNavigate?: () => void }) {
  return <Suspense fallback={<p className="field-hint">Оглавление…</p>}><Toc {...props} /></Suspense>
}
