import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Crepe } from '@milkdown/crepe'
import { editorViewCtx } from '@milkdown/kit/core'
import type { Node as ProseNode } from '@milkdown/kit/prose/model'
import '@milkdown/crepe/theme/common/style.css'
import '@milkdown/crepe/theme/frame-dark.css'
import { ArrowLeft, Save } from 'lucide-react'
import { library, useSession } from '../data/store'
import { api, ApiError } from '../data/api'
import type { Note } from '../data/model'
import { downloadText } from '../lib/utils'
import { Button } from './ui/button'
import { TabBar } from './ui/tab-bar'
import { SourceEditor } from './source-editor'

function CrepeEditor({ initial, noteId, onChange, onReady, onError }: { initial: string; noteId: string; onChange: (markdown: string) => void; onReady: (read: () => string) => void; onError: (message: string) => void }) {
  const host = useRef<HTMLDivElement>(null)
  const callbacks = useRef({ onChange, onReady, onError })
  callbacks.current = { onChange, onReady, onError }
  useEffect(() => {
    let disposed = false
    let ready = false
    let initialDoc: ProseNode | undefined
    let lastDoc: ProseNode | undefined
    const upload = (file: File) => library.uploadImage(noteId, file).catch(error => { callbacks.current.onError((error as Error).message); throw error })
    const crepe = new Crepe({ root: host.current, defaultValue: initial, features: { [Crepe.Feature.AI]: false, [Crepe.Feature.TopBar]: true }, featureConfigs: {
      [Crepe.Feature.TopBar]: { headingOptions: [{ level: null, label: 'Текст' }, { level: 1, label: 'Заголовок 1' }, { level: 2, label: 'Заголовок 2' }, { level: 3, label: 'Заголовок 3' }, { level: 4, label: 'Заголовок 4' }, { level: 5, label: 'Заголовок 5' }, { level: 6, label: 'Заголовок 6' }] },
      [Crepe.Feature.CodeMirror]: { previewOnlyByDefault: true, previewLabel: 'Формула', previewToggleText: previewOnly => previewOnly ? 'Изменить' : 'Скрыть исходник', copyText: 'Скопировать', searchPlaceholder: 'Язык кода', noResultText: 'Язык не найден' },
      [Crepe.Feature.Placeholder]: { text: 'Начните писать или введите / для выбора блока', mode: 'doc' },
      [Crepe.Feature.Latex]: { katexOptions: { trust: false, strict: 'ignore' }, inlineEditConfirm: 'Применить' },
      [Crepe.Feature.ImageBlock]: { onUpload: upload, inlineOnUpload: upload, blockOnUpload: upload, proxyDomURL: url => /^\/api\/assets\/[a-f0-9-]{36}$/.test(url) || url === '/demo-replication.svg' ? url : '/favicon.svg', blockUploadButton: 'Загрузить изображение', inlineUploadButton: 'Загрузить', blockCaptionPlaceholderText: 'Подпись изображения' },
      [Crepe.Feature.Toolbar]: { boldLabel: 'Полужирный', italicLabel: 'Курсив', codeLabel: 'Код', linkLabel: 'Ссылка', latexLabel: 'Формула', strikethroughLabel: 'Зачёркнутый' },
    } })
    // A focus change or a title edit must not normalize untouched source Markdown.
    const read = () => crepe.editor.action(ctx => initialDoc?.eq(ctx.get(editorViewCtx).state.doc)) ? initial : crepe.getMarkdown()
    crepe.on(listener => listener.blur(() => {
      if (ready && !disposed) callbacks.current.onChange(read())
    }).updated((_ctx, doc) => {
      if (!ready || disposed || lastDoc?.eq(doc)) return
      lastDoc = doc
      callbacks.current.onChange(read())
    }))
    const created = crepe.create().then(() => {
      if (disposed) return
      lastDoc = crepe.editor.action(ctx => ctx.get(editorViewCtx).state.doc)
      initialDoc = lastDoc
      ready = true; callbacks.current.onReady(read)
    }).catch(error => { if (!disposed) callbacks.current.onError(`Не удалось открыть редактор: ${(error as Error).message}`) })
    return () => { disposed = true; ready = false; void created.then(() => crepe.destroy()) }
  }, [initial, noteId])
  return <div className="crepe-host" ref={host} aria-label="Редактор Markdown Milkdown Crepe" />
}

// An editor reopened during its previous save must initialize against the committed
// revision and the reconciled browser draft, even if the user switched routes.
const pendingSaves = new Map<string, Promise<void>>()
type EditorProps = { note: Note; canEdit: boolean }

export default function NoteEditor(props: EditorProps) {
  const session = useSession()
  const draftKey = `lecturevault.note-draft.${session.user?.id}.${props.note.id}`
  const [pending, setPending] = useState(() => pendingSaves.get(draftKey))
  useEffect(() => {
    let mounted = true
    void pending?.then(() => { if (mounted) setPending(undefined) })
    return () => { mounted = false }
  }, [pending])
  if (pending) return <p className="field-hint" role="status">Завершаем сохранение заметки…</p>
  return <EditableNote {...props} draftKey={draftKey} />
}

function EditableNote({ note, canEdit, draftKey: key }: EditorProps & { draftKey: string }) {
  const [original] = useState(() => {
    try {
      const draft = JSON.parse(localStorage.getItem(key) || 'null')
      if (draft && typeof draft.markdown === 'string' && typeof draft.title === 'string') return draft as { title: string; markdown: string; revision: number }
    } catch { /* retain the server document */ }
    return { title: note.title, markdown: note.markdown, revision: note.revision ?? 1 }
  })
  const [title, setTitle] = useState(original.title)
  const [markdown, setMarkdown] = useState(original.markdown)
  const [revision, setRevision] = useState(original.revision)
  const [ready, setReady] = useState(false)
  const [mode, setMode] = useState<'visual' | 'source'>('visual')
  const [editorInitial, setEditorInitial] = useState(original.markdown)
  const [editorVersion, setEditorVersion] = useState(0)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [conflict, setConflict] = useState(false)
  const [saved, setSaved] = useState({ title: note.title, markdown: note.markdown })
  // Milkdown's change listener is debounced. Read its current document when
  // saving or switching modes so a quick click never drops the latest edit.
  const readVisual = useRef<(() => string) | undefined>(undefined)
  const [draftWriter] = useState(() => crypto.randomUUID())
  const savingNow = useRef(false)
  const dirty = title !== saved.title || markdown !== saved.markdown
  const remember = (value = markdown) => {
    try {
      if (savingNow.current || title !== saved.title || value !== saved.markdown) {
        localStorage.setItem(key, JSON.stringify({ title, markdown: value, revision, writer: draftWriter }))
      }
      else localStorage.removeItem(key)
    } catch { setError('Браузер не смог сохранить черновик. Сохраните на сервере или скачайте Markdown перед выходом.') }
  }
  const updateMarkdown = (value: string) => { setMarkdown(value); remember(value) }
  useEffect(() => { remember() }, [key, title, markdown, revision, dirty])
  const save = async () => {
    if (!canEdit || savingNow.current || !ready) return
    if (!title.trim()) { setError('Введите название заметки.'); return }
    savingNow.current = true
    let finishSave!: () => void
    pendingSaves.set(key, new Promise<void>(resolve => { finishSave = resolve }))
    setSaving(true); setError(''); setConflict(false)
    try {
      const current = mode === 'visual' ? readVisual.current?.() ?? markdown : markdown
      setMarkdown(current)
      const result = await library.saveNote(note.id, title.trim(), current, revision)
      setRevision(result.revision!)
      setTitle(latestTitle => latestTitle === title ? result.title : latestTitle)
      setSaved({ title: result.title, markdown: result.markdown })
      // Navigation and further edits are allowed while the server is saving.
      // Advance only the submitted draft's base revision, retaining subsequent edits.
      try {
        const draft = JSON.parse(localStorage.getItem(key) || 'null')
        if (draft?.revision === revision && draft.writer === draftWriter) {
          if (draft.title === title && draft.markdown === current) localStorage.removeItem(key)
          else localStorage.setItem(key, JSON.stringify({ ...draft, revision: result.revision }))
        }
      } catch { /* The mounted editor also persists the latest draft. */ }
    } catch (error) { setError((error as Error).message); setConflict(error instanceof ApiError && error.status === 409) }
    finally {
      savingNow.current = false; setSaving(false)
      pendingSaves.delete(key); finishSave()
    }
  }
  const resetToCurrent = async () => {
    if (!window.confirm('Заменить этот черновик сохранённой версией? При необходимости сначала скачайте черновик.')) return
    try {
      const current = await api<Note>(`/notes/${note.id}`)
      setReady(false); setTitle(current.title); setMarkdown(current.markdown); setEditorInitial(current.markdown)
      setRevision(current.revision!); setSaved({ title: current.title, markdown: current.markdown }); setConflict(false); setError('')
      // Recreate the editor even if the remote Markdown happens to match.
      setMode('source'); setEditorVersion(version => version + 1)
    } catch (error) { setError((error as Error).message) }
  }
  const preserveBeforeExit = () => {
    const current = mode === 'visual' ? readVisual.current?.() ?? markdown : markdown
    remember(current)
    return title !== saved.title || current !== saved.markdown
  }
  const latest = useRef({ save, preserveBeforeExit })
  latest.current = { save, preserveBeforeExit }
  useEffect(() => {
    const keydown = (event: KeyboardEvent) => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); void latest.current.save() } }
    const beforeUnload = (event: BeforeUnloadEvent) => { if (latest.current.preserveBeforeExit()) event.preventDefault() }
    window.addEventListener('keydown', keydown); window.addEventListener('beforeunload', beforeUnload)
    return () => { window.removeEventListener('keydown', keydown); window.removeEventListener('beforeunload', beforeUnload) }
  }, [])
  if (!canEdit) return <div className="quiet-empty"><h1>Только чтение</h1><p>Для редактирования нужна роль editor или owner.</p><Link to={`/notes/${note.id}`}>К заметке</Link></div>
  return <section className="note-editor"><div className="editor-controls"><Link className="editor-back" to={`/notes/${note.id}`}><ArrowLeft size={15} />Чтение</Link><span role="status">{saving ? 'Сохраняем…' : dirty ? 'Черновик в этом браузере' : 'Сохранено на сервере'}</span><Button size="sm" disabled={saving || !ready} onClick={() => void save()}><Save size={15} />Сохранить</Button></div><label htmlFor="editor-title" className="field-label editor-title-label">Название заметки</label><input id="editor-title" className="editor-title" value={title} maxLength={120} onChange={event => setTitle(event.target.value)} />{error && <div className="editor-error" role="alert"><p>{error}</p><Button variant="outline" size="sm" onClick={() => downloadText(markdown, `${title || 'Черновик'}.md`)}>Скачать черновик</Button>{conflict && <Button variant="outline" size="sm" onClick={() => void resetToCurrent()}>Загрузить актуальную версию</Button>}</div>}{!ready && <p role="status" className="field-hint">Открываем редактор…</p>}<TabBar label="Режим редактора" panelId="note-editor-panel" value={mode} onValueChange={value => { if (value === mode) return; const current = mode === 'visual' ? readVisual.current?.() ?? markdown : markdown; setMarkdown(current); setEditorInitial(current); readVisual.current = undefined; setMode(value); setReady(false) }} items={[{ value: 'visual', label: 'Визуально' }, { value: 'source', label: 'Markdown' }]} /><div id="note-editor-panel" role="tabpanel" aria-labelledby={`note-editor-panel-${mode}-tab`}>{mode === 'visual' ? <CrepeEditor key={editorVersion} initial={editorInitial} noteId={note.id} onChange={updateMarkdown} onReady={read => { readVisual.current = read; setReady(true) }} onError={setError} /> : <SourceEditor key={editorVersion} initial={editorInitial} onChange={updateMarkdown} onReady={() => setReady(true)} />}</div></section>
}
