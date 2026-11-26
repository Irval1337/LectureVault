import { Fragment, useCallback, useEffect, useState } from 'react'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { Download, FileText, Image, Video } from 'lucide-react'
import { api } from '../data/api'
import { library, useLibrary } from '../data/store'
import { reuseMaterialsUrl, type Job, type Note } from '../data/model'
import { Button } from './ui/button'
import { Dialog } from './ui/dialog'
import { ProgressMeter } from './ui/progress-meter'

type Kind = 'video' | 'pdf' | 'transcript' | 'image'
type Item = {
  id: string; name: string; size: number; kind: Kind; courseId: string; courseTitle: string
  jobId?: string; jobTitle?: string; noteId?: string
  jobs: { id: string; title: string; category: string }[]
  notes: { id: string; title: string; historical?: boolean }[]
  inUse: boolean; referenced: boolean; temporary: boolean; canArchive: boolean
  conversion?: 'queued' | 'processing' | 'failed'; conversionError?: string
}
type Storage = { limit: number; used: number; temporary: number; reserved: number; items: Item[]; groups: string[][]; editorPercent: number; editorUsed: number }
type FileUse = { url: string; title: string; detail?: string }

const kinds = { video: 'Видео', pdf: 'Презентации', transcript: 'Расшифровки', image: 'Изображения' }
const amount = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 })
function fileSize(bytes: number) {
  if (bytes > 0 && bytes < 1024) return amount.format(bytes) + ' Б'
  const unit = bytes >= 1024 ** 3 ? [1024 ** 3, 'ГБ'] as const : bytes >= 1024 ** 2 ? [1024 ** 2, 'МБ'] as const : [1024, 'КБ'] as const
  return amount.format(bytes / unit[0]) + ' ' + unit[1]
}

function fileUses(group: Item[], notes: ReadonlyMap<string, Note>, jobs: ReadonlyMap<string, Job>) {
  const uses = new Map<string, FileUse>()
  const addNote = (id: string, historical = false) => {
    const note = notes.get(id)
    const url = '/notes/' + id
    if (note && (!historical || !uses.has(url))) {
      uses.set(url, { url, title: note.title, detail: historical ? 'Предыдущая версия' : undefined })
    }
  }
  for (const item of group) {
    for (const note of item.notes) addNote(note.id, note.historical)
    for (const reference of item.jobs) {
      const job = jobs.get(reference.id)
      if (job?.noteId) addNote(job.noteId)
      else if (job && job.status !== 'ready' && job.status !== 'cancelled') {
        const url = '/queue?job=' + job.id
        uses.set(url, { url, title: job.title, detail: 'Задание' })
      }
    }
  }
  return [...uses.values()]
}

export function StorageSettings() {
  const { notes, jobs } = useLibrary()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const location = useLocation()
  const course = params.get('course') ?? ''
  const [kind, setKind] = useState<Kind | ''>('')
  const [unlinked, setUnlinked] = useState(false)
  const [editorPercent, setEditorPercent] = useState<string>()
  const [storage, setStorage] = useState<Storage>()
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [action, setAction] = useState<{ items: Item[]; type: 'archive' | 'delete' }>()
  const load = useCallback(async () => {
    try { setStorage(await api<Storage>('/storage')); setError('') }
    catch (error) { setError((error as Error).message) }
  }, [])
  useEffect(() => { void load() }, [load])
  const processing = storage?.items.some(item => item.temporary || item.conversion === 'queued' || item.conversion === 'processing')
  useEffect(() => {
    if (!processing) return
    const timer = window.setInterval(() => void load(), 2500)
    return () => clearInterval(timer)
  }, [load, processing])

  async function apply() {
    if (!action || busy) return
    setBusy(true); setError(''); setNotice('')
    try {
      const archive = action.type === 'archive'
      await api('/storage/assets/' + action.items[0].id + (archive ? '/archive' : '?allCopies=true'), { method: archive ? 'POST' : 'DELETE' })
      setAction(undefined)
      setNotice(archive ? 'Видео добавлено в очередь на распознавание.' : 'Файл удалён.')
      await load()
      void library.tick()
    } catch (error) { setError((error as Error).message) }
    finally { setBusy(false) }
  }

  async function saveEditorLimit() {
    if (editorPercent === undefined || !/^\d+$/.test(editorPercent) || Number(editorPercent) > 100) { setError('Укажите целое число от 0 до 100.'); return }
    setBusy(true); setError('')
    try {
      await api('/storage/editor-limit', { method: 'PUT', body: JSON.stringify({ percent: Number(editorPercent) }) })
      setEditorPercent(undefined); await load()
    } catch (error) { setError((error as Error).message) }
    finally { setBusy(false) }
  }

  const courses = new Map(storage?.items.map(item => [item.courseId, item.courseTitle]))
  const byId = new Map(storage?.items.map(item => [item.id, item]))
  const notesById = new Map(notes.map(note => [note.id, note]))
  const jobsById = new Map(jobs.map(job => [job.id, job]))
  const editorLimit = storage ? Math.floor(storage.limit * storage.editorPercent / 100) : 0
  const groups = storage?.groups.map(ids => ids.flatMap(id => byId.get(id) ?? []))
    .filter(group => group.length > 0 && (!course || group.some(item => item.courseId === course)) && (!kind || group[0].kind === kind))
    .map(items => ({ items, uses: fileUses(items, notesById, jobsById) }))
    .filter(({ items, uses }) => !unlinked || (!uses.length && items.every(item => !item.inUse && !item.referenced)))
  return <section className="settings-section storage-settings">
    <h2>Хранилище</h2>
    {error && !action && <p role="alert" className="field-error">{error} {!storage && <button type="button" className="inline-action" onClick={() => void load()}>Повторить</button>}</p>}
    {notice && <p role="status" className="auth-notice">{notice}</p>}
    {!storage && !error && <p role="status" className="field-hint">Загружаем данные хранилища…</p>}
    {storage && <>
      <div className="storage-summary">
        <p><strong>{fileSize(storage.used)}</strong> из {fileSize(storage.limit)}</p>
        <span>Свободно {fileSize(Math.max(0, storage.limit - storage.used - storage.reserved))}</span>
        <ProgressMeter label="Использовано хранилища" value={storage.used + storage.reserved} max={storage.limit}/>
        {storage.temporary > 0 && <p className="field-hint">Видео в обработке · {fileSize(storage.temporary)}</p>}
      </div>
      <p className="field-hint">Видео хранится во время распознавания. После обработки остаётся файл расшифровки.</p>
      <form className="storage-editor-limit" onSubmit={event => { event.preventDefault(); void saveEditorLimit() }}>
        <div>
          <label htmlFor="storage-editor-percent">Лимит для редакторов<span className="sr-only"> в процентах</span></label>
          <p className="field-hint" id="storage-editor-usage">
            {storage.editorUsed > 0 && `Занято ${fileSize(storage.editorUsed)} · `}
            {editorLimit > 0 ? `До ${fileSize(editorLimit)} на загрузки` : 'Загрузки отключены'}
          </p>
        </div>
        <div className="storage-limit-controls">
          <div className="storage-percent-field">
            <input id="storage-editor-percent" type="number" inputMode="numeric" min={0} max={100} aria-describedby="storage-editor-usage"
              value={editorPercent ?? storage.editorPercent} onChange={event => setEditorPercent(event.target.value)} />
            <span aria-hidden="true">%</span>
          </div>
          <Button type="submit" size="sm" variant="outline" aria-label="Сохранить лимит"
            disabled={busy || !editorPercent || Number(editorPercent) === storage.editorPercent}>Сохранить</Button>
        </div>
      </form>
      {(storage.items.length > 0 || course) && <div className="storage-filters">
        <label>Категория<select value={course} onChange={event => navigate({
          search: event.target.value ? new URLSearchParams({ course: event.target.value }).toString() : '', hash: '#storage',
        }, { replace: true, state: location.state })}>
          <option value="">Все категории</option>
          {course && !courses.has(course) && <option value={course}>Выбранная категория</option>}
          {[...courses].map(([id, title]) => <option key={id} value={id}>{title}</option>)}
        </select></label>
        <label>Тип файла<select value={kind} onChange={event => setKind(event.target.value as Kind | '')}>
          <option value="">Все типы</option>
          {Object.entries(kinds).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select></label>
        <label>Использование<select value={unlinked ? 'unlinked' : ''} onChange={event => setUnlinked(event.target.value === 'unlinked')}>
          <option value="">Все файлы</option>
          <option value="unlinked">Без заметок и заданий</option>
        </select></label>
      </div>}
      {!groups?.length && <p className="field-hint">{storage.items.length ? 'По выбранным фильтрам файлов нет.' : 'В ваших категориях пока нет файлов.'}</p>}
      {groups && groups.length > 0 && <ul className="storage-files">
        {groups.map(({ items: group, uses }) => {
          const item = group.find(reference => reference.courseId === course) ?? group[0]
          const copies = [item, ...group.filter(reference => reference.id !== item.id)]
          const locations = new Map(copies.map(reference => [reference.courseId + '\n' + reference.name, reference]))
          const Icon = item.kind === 'video' ? Video : item.kind === 'image' ? Image : FileText
          const status = item.conversion === 'queued' ? 'В очереди на распознавание' : item.conversion === 'processing' ? 'Распознаём видео' :
            item.temporary ? 'Временное видео' : ''
          return <li key={item.id}>
            <Icon size={18} aria-hidden="true" className="storage-file-icon"/>
            <div className="storage-file-info">
              <strong>{item.name}</strong>
              <p className="storage-file-location">{fileSize(item.size)}
                {[...locations.values()].map(reference => <Fragment key={reference.id}>
                  {' · '}<Link to={'/courses/' + reference.courseId}>{reference.courseTitle}</Link>
                  {reference.name !== item.name && ` (${reference.name})`}
                </Fragment>)}
              </p>
              {uses.length > 0 && <details className="storage-uses">
                <summary>Где используется</summary>
                <div>{uses.map(use => <p key={use.url}><Link to={use.url}>{use.title}</Link>{use.detail && ` · ${use.detail}`}</p>)}</div>
              </details>}
              {status && <p>{status}</p>}
              {item.conversionError && <p className="field-error">{item.conversionError}</p>}
            </div>
            <div className="storage-file-actions">
              {item.canArchive && <Button variant="outline" size="sm" onClick={() => { setError(''); setAction({ items: [item], type: 'archive' }) }}>Оставить расшифровку</Button>}
              {item.kind === 'transcript' && item.jobs[0] && <Button asChild variant="outline" size="sm"><Link to={reuseMaterialsUrl({ ...item.jobs[0], courseId: item.courseId })}>Создать конспект</Link></Button>}
              {!item.temporary && <Button asChild variant="ghost" size="icon"><a href={'/api/assets/' + item.id} download aria-label={'Скачать ' + item.name}><Download size={16}/></a></Button>}
              {group.every(reference => !reference.inUse && !reference.referenced) && <Button variant="ghost" size="sm" onClick={() => { setError(''); setAction({ items: copies, type: 'delete' }) }}>Удалить</Button>}
            </div>
          </li>
        })}
      </ul>}
    </>}
    <Dialog open={!!action} onOpenChange={open => { if (!open && !busy) { setAction(undefined); setError('') } }}
      title={action?.type === 'archive' ? 'Оставить расшифровку?' : 'Удалить файл?'}
      description={action?.type === 'archive' ? 'LectureVault сохранит расшифровку в TXT, затем удалит исходное видео.' : action ? `${action.items[0].name} · Освободится ${fileSize(action.items[0].size)}.` : ''}>
      {action?.type === 'delete' && <p>{[...new Set(action.items.map(item => item.courseTitle))].join(', ')}{action.items.some(item => item.jobs.length) && ' · Файл исчезнет из связанных заданий.'}</p>}
      <div className="dialog-actions">
        <Button variant="ghost" disabled={busy} onClick={() => { setAction(undefined); setError('') }}>Отмена</Button>
        <Button disabled={busy} onClick={() => void apply()}>{action?.type === 'archive' ? 'Распознать видео' : 'Удалить файл'}</Button>
      </div>
      {error && action && <p role="alert" className="field-error">{error}</p>}
    </Dialog>
  </section>
}
