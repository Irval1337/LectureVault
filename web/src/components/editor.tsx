import { useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Link } from 'react-router-dom'
import { isLight, subscribeTheme } from './theme'
import { setCodeMirrorTheme } from '@milkdown/crepe/feature/code-mirror'
import { oneDark } from '@codemirror/theme-one-dark'
import { Crepe } from '@milkdown/crepe'
import { editorViewCtx, editorViewOptionsCtx, prosePluginsCtx } from '@milkdown/kit/core'
import { EditorView as CodeView, ViewPlugin } from '@codemirror/view'
import { tableBlockConfig, type RenderType } from '@milkdown/kit/component/table-block'
import type { Node as ProseNode } from '@milkdown/kit/prose/model'
import { Plugin, TextSelection } from '@milkdown/kit/prose/state'
import '@milkdown/crepe/theme/common/style.css'
import '@milkdown/crepe/theme/frame-dark.css'
import type { EditorView } from '@milkdown/kit/prose/view'
import { preserveImageMetadata, blockDragPreview, pictureDetails, sizedPicture } from './editor-blocks'
import { editorPictureSizes } from './editor-picture-size'
import { tableInsertionTools } from './editor-table'
import { ActionMenu } from './ui/action-menu'
import './editor-polish.css'
import { ArrowLeft, ChevronDown, Save } from 'lucide-react'
import { library, useSession } from '../data/store'
import { api, ApiError } from '../data/api'
import type { Note } from '../data/model'
import { downloadText } from '../lib/utils'
import { Button } from './ui/button'
import { TabBar } from './ui/tab-bar'
import { SourceEditor } from './source-editor'

// Crepe also mounts CodeMirror for read-only previews. Give overflowing code a
// keyboard scroll target even when the nested editor itself is outside the tab order.
const scrollableCode = ViewPlugin.define(view => {
  const scroller = view.scrollDOM
  const mark = (overflows: boolean) => {
    if (overflows) {
      scroller.tabIndex = 0
      scroller.setAttribute('role', 'region')
      scroller.setAttribute('aria-label', 'Код с горизонтальной прокруткой')
    } else {
      scroller.removeAttribute('tabindex')
      scroller.removeAttribute('role')
      scroller.removeAttribute('aria-label')
    }
  }
  const measure = {read: () => scroller.scrollWidth > scroller.clientWidth + 1, write: mark}
  const update = () => view.requestMeasure(measure)
  const observer = new ResizeObserver(update)
  observer.observe(scroller)
  observer.observe(view.contentDOM)
  return {
    update,
    destroy: () => observer.disconnect(),
  }
})

const formatLabels: Record<string, string> = {
  bold: 'Полужирный', italic: 'Курсив', strikethrough: 'Зачёркнутый', code: 'Строчный код',
  'bullet-list': 'Маркированный список', 'ordered-list': 'Нумерованный список', 'task-list': 'Список задач',
  link: 'Ссылка', image: 'Изображение', table: 'Таблица', 'code-block': 'Блок кода', math: 'Формула',
  quote: 'Цитата', hr: 'Горизонтальная черта',
}

const tableLabels: Record<RenderType, string> = {
  add_row: 'Добавить строку', add_col: 'Добавить столбец', delete_row: 'Удалить строку', delete_col: 'Удалить столбец',
  align_col_left: 'По левому краю', align_col_center: 'По центру', align_col_right: 'По правому краю',
  col_drag_handle: 'Переместить столбец', row_drag_handle: 'Переместить строку',
}

function labelledIcon(icon: string, label: string) {
  return `<span title="${label}">${icon}<span class="sr-only">${label}</span></span>`
}

// Crepe inserts a Vue mount div between each HTML list and its native li.
function exposeListItems(editor: HTMLElement) {
  const selector = 'div.milkdown-list-item-block, div.milkdown-list-item-block > li'
  const markItem = (element: Element) => element.setAttribute('role', element.tagName === 'LI' ? 'presentation' : 'listitem')
  const mark = (element: Element) => {
    if (element.matches(selector)) markItem(element)
    element.querySelectorAll(selector).forEach(markItem)
  }
  mark(editor)
  const observer = new MutationObserver(records => {
    for (const record of records) {
      for (const node of record.addedNodes) if (node instanceof Element) mark(node)
    }
  })
  observer.observe(editor, { childList: true, subtree: true })
  return observer
}

function CrepeEditor({ initial, noteId, settingsSlot, onChange, onReady, onError }: { settingsSlot: HTMLElement | null; initial: string; noteId: string; onChange: (markdown: string) => void; onReady: (read: () => string) => void; onError: (message: string) => void }) {
  const [formatOpen, setFormatOpen] = useState(false)
  const editor = useRef<EditorView | null>(null)
  const [pictureCount, setPictureCount] = useState(0)
  const [percent, setPercent] = useState('75')
  const toolbarId = useId()
  const host = useRef<HTMLDivElement>(null)
  const callbacks = useRef({ onChange, onReady, onError })
  callbacks.current = { onChange, onReady, onError }
  useEffect(() => {
    let disposed = false
    let ready = false
    let stopTheme: (() => void) | undefined
    let initialDoc: ProseNode | undefined
    let lastDoc: ProseNode | undefined
    let listObserver: MutationObserver | undefined
    const upload = (file: File) => library.uploadImage(noteId, file).catch(error => { callbacks.current.onError((error as Error).message); throw error })
    const crepe = new Crepe({ root: host.current, defaultValue: initial, features: { [Crepe.Feature.AI]: false, [Crepe.Feature.TopBar]: true }, featureConfigs: {
      [Crepe.Feature.TopBar]: {
        headingOptions: [{ level: null, label: 'Текст' }, ...[1, 2, 3, 4, 5, 6].map(level => ({ level, label: `Заголовок ${level}` }))],
        buildTopBar: builder => {
          for (const group of builder.build()) {
            for (const item of group.items) {
              const label = formatLabels[item.key]
              if (label) item.icon = labelledIcon(item.icon, label)
            }
          }
        },
      },
      [Crepe.Feature.CodeMirror]: { extensions: [scrollableCode, CodeView.contentAttributes.of({ 'aria-label': 'Исходный текст блока' })], previewOnlyByDefault: true, previewLabel: 'Формула', previewToggleText: previewOnly => previewOnly ? 'Изменить' : 'Скрыть исходник', copyText: 'Скопировать', searchPlaceholder: 'Язык кода', noResultText: 'Язык не найден' },
      [Crepe.Feature.Placeholder]: { text: 'Начните писать или введите / для выбора блока', mode: 'doc' },
      [Crepe.Feature.Latex]: { katexOptions: { trust: false, strict: 'ignore' }, inlineEditConfirm: 'Применить' },
      [Crepe.Feature.ImageBlock]: { onUpload: upload, inlineOnUpload: upload, blockOnUpload: upload, proxyDomURL: url => /^\/api\/assets\/[a-f0-9-]{36}(?:\/slides\/[1-9]\d{0,2}\.jpg)?$/.test(url) || url === '/demo-replication.svg' ? url : '/favicon.svg', blockUploadButton: 'Загрузить изображение', inlineUploadButton: 'Загрузить', blockCaptionPlaceholderText: 'Подпись изображения' },
      [Crepe.Feature.Toolbar]: { boldLabel: 'Полужирный', italicLabel: 'Курсив', codeLabel: 'Код', linkLabel: 'Ссылка', latexLabel: 'Формула', strikethroughLabel: 'Зачёркнутый' },
    } })
    crepe.editor.config(ctx => {
      preserveImageMetadata(ctx)
      ctx.update(prosePluginsCtx, plugins => [...plugins, tableInsertionTools, new Plugin({
        view(view) {
          editor.current = view
          const stopPreview = blockDragPreview(view)
          const sizing = editorPictureSizes(view)
          const update = (current: EditorView, previous?: typeof current.state) => {
            if (previous?.doc === current.state.doc) return
            sizing.schedule()
            let count = 0
            current.state.doc.descendants(node => { if (pictureDetails(node)) count++ })
            setPictureCount(count)
          }
          update(view)
          return { update, destroy() {
            if (editor.current === view) editor.current = null
            stopPreview()
            sizing.destroy()
          } }
        },
      })])
      ctx.update(editorViewOptionsCtx, options => ({
        ...options, attributes: { 'aria-label': 'Текст заметки', 'aria-multiline': 'true' },
        handleDOMEvents: {
          ...options.handleDOMEvents,
          blur(view, event) {
            // Keyboard focus can reach the toolbar before selectionchange has
            // committed a freshly selected text range to ProseMirror.
            const range = view.dom.ownerDocument.getSelection()
            if (event.target === view.dom && event.relatedTarget instanceof Element && event.relatedTarget.closest('.milkdown-top-bar') &&
                range && !range.isCollapsed && range.anchorNode?.nodeType === Node.TEXT_NODE && range.focusNode?.nodeType === Node.TEXT_NODE &&
                view.dom.contains(range.anchorNode) && view.dom.contains(range.focusNode)) {
              const anchor = view.posAtDOM(range.anchorNode, range.anchorOffset)
              const head = view.posAtDOM(range.focusNode, range.focusOffset)
              const selection = TextSelection.between(view.state.doc.resolve(anchor), view.state.doc.resolve(head))
              if (!view.state.selection.eq(selection)) view.dispatch(view.state.tr.setSelection(selection))
            }
            return false
          },
        },
      }))
      ctx.update(tableBlockConfig.key, config => ({
        ...config, renderButton: action => labelledIcon(config.renderButton(action), tableLabels[action]),
      }))
    })
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
      const theme = () => crepe.editor.action(setCodeMirrorTheme(isLight() ? [] : oneDark))
      theme(); stopTheme = subscribeTheme(theme)
      const view = crepe.editor.action(ctx => ctx.get(editorViewCtx))
      host.current?.querySelector('.milkdown-top-bar')?.setAttribute('id', toolbarId)
      listObserver = exposeListItems(view.dom)
      lastDoc = view.state.doc
      initialDoc = lastDoc
      ready = true; callbacks.current.onReady(read)
    }).catch(error => { if (!disposed) callbacks.current.onError(`Не удалось открыть редактор: ${(error as Error).message}`) })
    return () => { disposed = true; ready = false; listObserver?.disconnect(); stopTheme?.(); void created.then(() => crepe.destroy()) }
  }, [initial, noteId])
  function resizePictures() {
    const view = editor.current
    if (!view) return
    const transaction = view.state.tr
    view.state.doc.descendants((node, pos) => {
      const details = pictureDetails(node)
      if (!details) return
      if (details.ratio !== Number(percent) / 100)
        transaction.setNodeMarkup(pos, undefined, sizedPicture(node, Number(percent) / 100))
    })
    if (transaction.docChanged) view.dispatch(transaction)
  }
  // Crepe handles these controls on pointerdown to preserve the document selection.
  // Forward the native click generated by keyboard and assistive activation.
  return <div className={`visual-editor ${formatOpen ? 'format-open' : ''}`}>
    <button type="button" className="mobile-format-toggle" aria-expanded={formatOpen} aria-controls={toolbarId} onClick={() => setFormatOpen(!formatOpen)}>Форматирование<ChevronDown size={15}/></button>
    {settingsSlot && pictureCount > 0 && createPortal(<ActionMenu label="Настройки редактора">
      <div className="editor-image-settings">
        <label>Размер всех иллюстраций<select value={percent} onChange={event => setPercent(event.target.value)}>
          {[25, 50, 75, 100].map(size => <option key={size} value={size}>{size}%</option>)}
        </select></label>
        <Button size="sm" variant="outline" onClick={resizePictures}>Применить ко всем</Button>
      </div>
    </ActionMenu>, settingsSlot)}
    <div className="crepe-host" ref={host} onClick={event => {
    if (event.detail !== 0 || !(event.target instanceof Element)) return
    const button = event.target.closest<HTMLButtonElement>('.milkdown-top-bar button')
    button?.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true }))
  }} onKeyDown={event => {
    if (event.key !== 'Escape') return
    const selector = host.current?.querySelector('.top-bar-heading-selector:has(.top-bar-heading-dropdown)')
    const trigger = selector?.querySelector<HTMLButtonElement>('.top-bar-heading-button')
    if (trigger) { trigger.click(); trigger.focus() }
  }} /></div>
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
  const [settingsSlot, setSettingsSlot] = useState<HTMLDivElement | null>(null)
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
  if (!canEdit) return <div className="quiet-empty"><h1>Только чтение</h1><p>Попросите владельца категории дать вам права на редактирование.</p><Link to={`/notes/${note.id}`}>К заметке</Link></div>
  const headingMatchesTitle = markdown.trimStart().split('\n', 1)[0]?.replace(/^#\s+/, '').trim() === title.trim() && markdown.trimStart().startsWith('# ')
  return <section className={`note-editor${headingMatchesTitle ? ' has-document-title' : ''}`}><div className="editor-controls"><Link className="editor-back" to={`/notes/${note.id}`}><ArrowLeft size={15} />Чтение</Link><span role="status">{saving ? 'Сохраняем…' : dirty ? 'Черновик' : 'Сохранено на сервере'}</span><div className="editor-settings-slot" ref={setSettingsSlot}/><Button size="sm" disabled={saving || !ready} onClick={() => void save()}><Save size={15} />Сохранить</Button></div><label htmlFor="editor-title" className="sr-only">Название заметки</label><input id="editor-title" className="editor-title" placeholder="Название заметки" value={title} maxLength={120} onChange={event => setTitle(event.target.value)} />{error && <div className="editor-error" role="alert"><p>{error}</p><Button variant="outline" size="sm" onClick={() => downloadText(markdown, `${title || 'Черновик'}.md`)}>Скачать черновик</Button>{conflict && <Button variant="outline" size="sm" onClick={() => void resetToCurrent()}>Загрузить актуальную версию</Button>}</div>}{!ready && <p role="status" className="field-hint">Открываем редактор…</p>}<TabBar label="Режим редактора" panelId="note-editor-panel" value={mode} onValueChange={value => { if (value === mode) return; const current = mode === 'visual' ? readVisual.current?.() ?? markdown : markdown; setMarkdown(current); setEditorInitial(current); readVisual.current = undefined; setMode(value); setReady(false) }} items={[{ value: 'visual', label: 'Визуально' }, { value: 'source', label: 'Markdown' }]} /><div id="note-editor-panel" role="tabpanel" aria-labelledby={`note-editor-panel-${mode}-tab`}>{mode === 'visual' ? <CrepeEditor settingsSlot={settingsSlot} key={editorVersion} initial={editorInitial} noteId={note.id} onChange={updateMarkdown} onReady={read => { readVisual.current = read; setReady(true) }} onError={setError} /> : <SourceEditor key={editorVersion} initial={editorInitial} onChange={updateMarkdown} onReady={() => setReady(true)} />}</div></section>
}
