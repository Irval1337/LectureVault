import { useEffect, useRef, useState, type DragEvent, type FormEvent } from 'react'
import { ArrowRight, Check, FileText, Folder, Upload, X } from 'lucide-react'
import { Link, useSearchParams } from 'react-router-dom'
import { unified } from 'unified'
import remarkParse from 'remark-parse'
import { visit } from 'unist-util-visit'
import { library } from '../data/store'
import type { AppState } from '../data/model'
import { plural } from '../lib/utils'
import { CategoryOptions, folderLink } from './organization'
import { QuartzArticle } from './reader'
import { Button } from './ui/button'
import { PageHeading } from './ui/page-heading'

const MAX_FILES = 25
const MAX_FILE_CHARS = 1_000_000
const MAX_SOURCE_BYTES = 5 * 1024 * 1024
const MAX_REQUEST_BYTES = 6 * 1024 * 1024
const markdownParser = unified().use(remarkParse)
const byteCount = new TextEncoder()

type Document = { id: string; name: string; title: string; markdown: string; size: number; hasLocalRefs: boolean }
type Result = { courseId: string; category: string; label: string; documents: Awaited<ReturnType<typeof library.importMarkdown>> }

async function readMarkdown(file: File): Promise<Document> {
  if (!/\.(md|markdown)$/i.test(file.name)) throw new Error('Нужен файл .md или .markdown.')
  if (file.size > MAX_SOURCE_BYTES) throw new Error('Файл больше 5 МБ.')
  let markdown: string
  try { markdown = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(await file.arrayBuffer()) }
  catch { throw new Error('Не удалось прочитать файл в кодировке UTF-8.') }
  if (/\x00/.test(markdown)) throw new Error('В файле есть двоичные данные.')
  if (!markdown.trim()) throw new Error('Файл пуст.')
  if (markdown.length > MAX_FILE_CHARS) throw new Error('В файле больше 1 000 000 символов.')
  const tree = markdownParser.parse(markdown.replace(/^\uFEFF/, ''))
  const heading = tree.children.find(node => node.type === 'heading' && node.depth === 1)
  const words: string[] = []
  if (heading) visit(heading, node => {
    if (node.type === 'text' || node.type === 'inlineCode') words.push(node.value)
    if (node.type === 'image' && node.alt) words.push(node.alt)
  })
  let hasLocalRefs = false
  visit(tree, node => {
    if (node.type === 'text' && /!?\[\[[^\]]+\]\]/.test(node.value)) hasLocalRefs = true
    if (node.type !== 'image' && node.type !== 'link' && node.type !== 'definition') return
    if (!/^(?:[a-z][a-z\d+.-]*:|\/\/|#|\/api\/assets\/)/i.test(node.url)) hasLocalRefs = true
  })
  const name = file.name.replace(/\.(md|markdown)$/i, '')
  const title = (words.join('').trim() || name).replace(/\s+/g, ' ').trim() || 'Без названия'
  return { id: crypto.randomUUID(), name: file.name, title: title.slice(0, 120).replace(/[\uD800-\uDBFF]$/, ''), markdown, size: file.size, hasLocalRefs }
}

export default function MarkdownImportPage({ state }: { state: AppState }) {
  const [params] = useSearchParams()
  const writable = state.courses.filter(course => course.role === 'owner' || course.role === 'editor')
  const [destination, setDestination] = useState(() => {
    const root = writable.find(course => course.id === params.get('course'))
    const folder = params.get('folder') || ''
    return root ? root.id + (root.folders?.includes(folder) ? '/' + folder : '') : writable[0]?.id ?? ''
  })
  const [documents, setDocuments] = useState<Document[]>([])
  const [selected, setSelected] = useState('')
  const [issues, setIssues] = useState<string[]>([])
  const [error, setError] = useState('')
  const [reading, setReading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [result, setResult] = useState<Result>()
  const [dragging, setDragging] = useState(false)
  const [newCategory, setNewCategory] = useState(!writable.length)
  const [categoryName, setCategoryName] = useState('')
  const [categoryError, setCategoryError] = useState('')
  const [creating, setCreating] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const titleInput = useRef<HTMLInputElement>(null)
  const resultPanel = useRef<HTMLElement>(null)
  const dragDepth = useRef(0)
  const inFlight = useRef(false)
  const active = documents.find(document => document.id === selected) ?? documents[0]
  const [courseId, ...folders] = destination.split('/')
  const course = writable.find(item => item.id === courseId)
  const category = folders.join('/')
  const validDestination = course && (!category || course.folders?.includes(category))
  const busy = reading || saving || creating
  const count = plural(documents.length, ['файл', 'файла', 'файлов'])

  useEffect(() => {
    if (result) resultPanel.current?.focus({ preventScroll: true })
  }, [result])

  async function addFiles(files: File[]) {
    if (!files.length || inFlight.current || creating) return
    inFlight.current = true
    setReading(true)
    setIssues([])
    setError('')
    const next = [...documents]
    const failed: string[] = []
    let total = next.reduce((size, doc) => size + doc.size, 0)
    let added: Document | undefined
    for (const file of files.slice(0, MAX_FILES)) {
      if (next.length >= MAX_FILES) { failed.push('За один раз можно импортировать до 25 файлов.'); break }
      try {
        if (total + file.size > MAX_SOURCE_BYTES) throw new Error('Общий размер файлов превысит 5 МБ.')
        const document = await readMarkdown(file)
        if (next.some(item => item.name === document.name && item.markdown === document.markdown)) {
          throw new Error('Файл уже в списке.')
        }
        next.push(document)
        total += file.size
        added ??= document
      } catch (cause) { failed.push(`${file.name} · ${(cause as Error).message}`) }
    }
    if (files.length > MAX_FILES && next.length < MAX_FILES) failed.push('Выберите ещё файлы отдельной группой.')
    setDocuments(next)
    if (!active && added) setSelected(added.id)
    setIssues(failed)
    inFlight.current = false
    setReading(false)
  }

  function drop(event: DragEvent) {
    event.preventDefault()
    dragDepth.current = 0
    setDragging(false)
    void addFiles(Array.from(event.dataTransfer.files))
  }

  async function createCategory(event: FormEvent) {
    event.preventDefault()
    if (busy || !categoryName.trim()) return
    setCreating(true)
    setCategoryError('')
    try {
      const id = await library.createCourse(categoryName, 'personal')
      setDestination(id)
      setNewCategory(false)
      setCategoryName('')
      setError('')
    } catch (cause) { setCategoryError((cause as Error).message) }
    finally { setCreating(false) }
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (busy || inFlight.current || !documents.length || !validDestination || newCategory) return
    const missing = documents.find(document => !document.title.trim())
    if (missing) { setSelected(missing.id); setError('Введите название для каждого файла.'); titleInput.current?.focus(); return }
    const payload = JSON.stringify({ courseId, category, documents: documents.map(({ title, markdown }) => ({ title: title.trim(), markdown })) })
    if (byteCount.encode(payload).length > MAX_REQUEST_BYTES) { setError('Данные не помещаются в один запрос. Уберите один или несколько файлов.'); return }
    inFlight.current = true
    setSaving(true)
    setError('')
    try {
      const imported = await library.importMarkdown(payload, courseId)
      setResult({ courseId, category, label: [course.title, ...folders].join(' › '), documents: imported })
      window.scrollTo({ top: 0, behavior: 'instant' })
    } catch (cause) { setError((cause as Error).message) }
    finally { setSaving(false); inFlight.current = false }
  }

  if (result) {
    const imported = result.documents.filter(document => document.status === 'imported').length
    const existing = result.documents.length - imported
    return <section ref={resultPanel} tabIndex={-1} className="markdown-import markdown-import-result" aria-label="Результат импорта">
      <PageHeading title={imported ? 'Импорт завершён' : 'Эти заметки уже есть'} description={result.label}/>
      <p role="status" className="import-result-message"><Check size={16}/>Добавлено {imported}{existing > 0 && ` · Уже в категории ${existing}`}</p>
      <ul className="import-results">{result.documents.map((document, index) => <li key={`${document.id}-${index}`}>
        <Link to={'/notes/' + document.id}><FileText size={18}/><span>{document.title}</span><ArrowRight size={15}/></Link>
        {document.status === 'existing' && <span className="field-hint">Уже в категории</span>}
      </li>)}</ul>
      <div className="import-result-actions">
        <Button asChild variant="outline"><Link to={folderLink(result.courseId, result.category)}>Перейти в категорию</Link></Button>
        <Button onClick={() => { setResult(undefined); setDocuments([]); setSelected(''); setIssues([]) }}>Импортировать ещё</Button>
      </div>
    </section>
  }

  return <section className="markdown-import">
    <PageHeading title="Импорт Markdown" />
    <div className="import-destination">
      {writable.length > 0 && !newCategory && <>
        <label className="field-label" htmlFor="import-destination">Куда сохранить</label>
        <div className="import-destination-controls">
          <div className="import-select"><Folder size={17}/><select id="import-destination" value={validDestination ? destination : ''} disabled={busy}
            onChange={event => { setDestination(event.target.value); setError('') }}>
            {!validDestination && <option value="" disabled>Выберите категорию</option>}
            <CategoryOptions courses={writable}/>
          </select></div>
          <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => setNewCategory(true)}>Новая категория</Button>
        </div>
        {course?.workspace === 'shared' && <p className="field-hint">Заметки будут доступны участникам общей категории.</p>}
      </>}
      {newCategory && <form onSubmit={createCategory} className="import-create-category">
        <label className="field-label" htmlFor="import-category-name">Новая личная категория</label>
        <div className="import-destination-controls">
          <input id="import-category-name" value={categoryName} disabled={busy} maxLength={80} placeholder="Название категории" onChange={event => { setCategoryName(event.target.value); setCategoryError('') }} />
          <Button type="submit" size="sm" variant="outline" disabled={busy || !categoryName.trim()}>Создать</Button>
          {writable.length > 0 && <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => { setNewCategory(false); setCategoryError('') }}>Отмена</Button>}
        </div>
        {categoryError && <p role="alert" className="field-error">{categoryError}</p>}
      </form>}
    </div>

    <form onSubmit={submit} noValidate aria-busy={busy}>
      <input ref={input} type="file" multiple hidden aria-label="Файлы Markdown" accept=".md,.markdown" onChange={event => {
        const files = Array.from(event.currentTarget.files ?? [])
        event.currentTarget.value = ''
        void addFiles(files)
      }}/>
      <button type="button" className={`import-drop${documents.length ? ' import-drop-compact' : ''}${dragging ? ' is-dragging' : ''}`}
        disabled={busy} onClick={() => input.current?.click()} onDrop={drop} onDragOver={event => event.preventDefault()}
        onDragEnter={event => { if (event.dataTransfer.types.includes('Files')) { dragDepth.current++; setDragging(true) } }}
        onDragLeave={() => { dragDepth.current = Math.max(0, dragDepth.current - 1); if (!dragDepth.current) setDragging(false) }}>
        <span className="import-drop-icon"><Upload size={20}/></span>
        <span><strong>{documents.length ? 'Добавить файлы' : 'Выберите или перетащите Markdown-файлы'}</strong>
          <small>.md, .markdown · до 25 файлов, 5 МБ суммарно</small></span>
      </button>
      {reading && <p className="field-hint" role="status">Читаем файлы…</p>}
      {issues.length > 0 && <ul className="import-issues field-error" role="alert">{issues.map((issue, index) => <li key={index}>{issue}</li>)}</ul>}

      {active && <div className="import-review">
        <section className="import-file-pane" aria-labelledby="import-files-title">
          <h2 id="import-files-title">Выбранные файлы <span>{documents.length}</span></h2>
          <ul className="import-files">{documents.map(document => <li key={document.id}>
            <button type="button" disabled={busy} aria-pressed={document.id === active.id} onClick={() => { setSelected(document.id); setError('') }}>
              <FileText size={17}/><span title={document.name}>{document.name}</span>
            </button>
            <button type="button" disabled={busy} className="icon-action" aria-label={'Убрать ' + document.name} onClick={() => {
              setDocuments(previous => previous.filter(item => item.id !== document.id)); setError('')
            }}><X size={15}/></button>
          </li>)}</ul>
        </section>
        <section className="import-preview" aria-labelledby="import-preview-title">
          <h2 id="import-preview-title">Предпросмотр</h2>
          <label className="field-label" htmlFor="import-note-title">Название заметки</label>
          <input ref={titleInput} id="import-note-title" maxLength={120} disabled={busy} value={active.title} onChange={event => {
            const title = event.target.value
            setDocuments(previous => previous.map(item => item.id === active.id ? { ...item, title } : item)); setError('')
          }} />
          <div className="import-article" tabIndex={0} role="region" aria-label="Текст заметки" onClick={event => {
            if ((event.target as Element).closest('a')) event.preventDefault()
          }}><QuartzArticle title="" markdown={active.markdown} preview/></div>
          {active.hasLocalRefs && <p className="field-hint">В тексте есть локальные ссылки. Они сохранятся в Markdown, а файлы по этим ссылкам нужно прикрепить отдельно.</p>}
        </section>
      </div>}

      {error && <p className="field-error" role="alert">{error}</p>}
      {documents.length > 0 && <div className="import-footer">
        <p className="field-hint">Заметки с тем же названием и текстом в этой категории будут пропущены.</p>
        <Button type="submit" disabled={busy || !validDestination || newCategory}>{saving ? 'Импортируем…' : `Импортировать ${count}`}</Button>
      </div>}
    </form>
  </section>
}
