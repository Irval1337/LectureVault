import { useEffect, useId, useState, type FormEvent, type ReactNode } from 'react'
import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom'
import { Folder, FolderOpen, BookOpen, Code, FlaskConical, Sigma, Music, Pencil, FileText, ChevronRight, ChevronDown } from 'lucide-react'
import { canDeleteNote, type Course, type Note } from '../data/model'
import { plural } from '../lib/utils'
import { library, useSession } from '../data/store'
import { Button } from './ui/button'
import { Dialog } from './ui/dialog'
import { ActionMenu, openRowActions } from './ui/action-menu'

const symbols = {
  folder: [Folder, 'Папка'], book: [BookOpen, 'Книга'], code: [Code, 'Код'],
  science: [FlaskConical, 'Наука'], math: [Sigma, 'Математика'], music: [Music, 'Музыка'],
} as const

function symbolFor(course: Course, path: string) {
  return (path ? course.folderIcons?.[path] : course.icon) || 'folder'
}
export function CourseIcon({ course, path = '', open = false }: { course: Course; path?: string; open?: boolean }) {
  const key = symbolFor(course, path) as keyof typeof symbols
  const Icon = open && key === 'folder' ? FolderOpen : symbols[key]?.[0] || Folder
  return <Icon size={16} aria-hidden="true"/>
}

export function IconPicker({ course, path = '' }: { course: Course; path?: string }) {
  const [open, setOpen] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  async function choose(id: string) {
    setPending(true)
    try {
      await library.setCourseIcon(course.id, id, path)
      setOpen(false)
    } catch (e) { setError((e as Error).message) }
    finally { setPending(false) }
  }
  return <>
    <button className="edit-category-symbol" onClick={() => { setError(''); setOpen(true) }}><Pencil size={12}/>Сменить значок</button>
    <Dialog open={open} onOpenChange={setOpen} title="Значок категории" description={[course.title,path].filter(Boolean).join(' › ')}>
      <div className="course-symbols">{Object.entries(symbols).map(([id, [Icon, label]]) =>
        <Button variant="outline" key={id} aria-pressed={id === symbolFor(course,path)} disabled={pending} onClick={() => void choose(id)}>
          <Icon size={18}/>{label}
        </Button>)}</div>
      {error && <p className="field-error" role="alert">{error}</p>}
    </Dialog>
  </>
}

type NoteActionsProps = { note: Note; course: Course; paths: string[]; children: ReactNode; tableRow?: boolean }

export function NoteActions({ note, course, paths, children, tableRow }: NoteActionsProps) {
  const [move, setMove] = useState(false)
  const [path, setPath] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const inputId = useId()
  const navigate = useNavigate()
  const location = useLocation()
  const session = useSession()
  const writable = course.role === 'owner' || course.role === 'editor'
  const url = `/notes/${note.id}`

  function chooseCategory() { setPath(note.category || ''); setError(''); setMove(true) }
  async function saveCategory(event: FormEvent) {
    event.preventDefault()
    setSaving(true)
    try {
      await library.moveNote(note.id, path)
      setMove(false)
    } catch (e) { setError((e as Error).message) }
    finally { setSaving(false) }
  }
  async function deleteNote() {
    if (!window.confirm(`Удалить заметку «${note.title}»?`)) return
    try {
      await library.deleteNote(note.id)
      if (location.pathname === url || location.pathname === url + '/edit') navigate(folderLink(course.id, note.category))
    } catch (e) { library.reportError(e) }
  }

  return <div className={`note-actions-row${tableRow ? ' note-list-row' : ''}`} role={tableRow ? 'row' : undefined}
    onContextMenu={event => {
      if ((event.target as Element).closest('[role=dialog]')) return
      event.preventDefault()
      openRowActions(event.currentTarget, { x: event.clientX, y: event.clientY })
    }}
    onKeyDown={event => {
      if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) {
        event.preventDefault()
        openRowActions(event.currentTarget)
      }
    }}>
    {children}
    <div className="note-menu-cell" role={tableRow ? 'cell' : undefined}>
      <ActionMenu label={`Действия заметки «${note.title}»`}>
        <Button asChild variant="ghost" size="sm"><Link to={url}>Открыть</Link></Button>
        {writable && <Button asChild variant="ghost" size="sm"><Link to={url + '/edit'}>Редактировать</Link></Button>}
        <Button asChild variant="ghost" size="sm"><a download href={'/api' + url + '/export'}>Скачать Markdown</a></Button>
        {writable && <Button variant="ghost" size="sm" onClick={chooseCategory}>Переместить в категорию</Button>}
        {canDeleteNote(course, note, session.user?.id) && <Button variant="ghost" size="sm" className="danger-action" onClick={() => void deleteNote()}>Удалить заметку</Button>}
      </ActionMenu>
    </div>
    {move && <Dialog open={move} onOpenChange={setMove} title="Категория заметки" description={note.title}>
      <form onSubmit={saveCategory}>
        <label className="field-label" htmlFor={inputId}>Куда переместить</label>
        <select id={inputId} value={path} onChange={e => setPath(e.target.value)}>
          <option value="">{course.title}</option>
          {paths.map(p => <option key={p} value={p}>{[course.title, ...p.split('/')].join(' › ')}</option>)}
        </select>
        {error && <p className="field-error" role="alert">{error}</p>}
        <div className="dialog-actions"><Button type="submit" disabled={saving}>Сохранить</Button></div>
      </form>
    </Dialog>}
  </div>
}

export function folderLink(root: string, path = '') {
  return '/courses/' + root + (path ? '?' + new URLSearchParams({folder: path}) : '')
}

export function CategoryOptions({ courses, parentsOnly = false }: { courses: Course[]; parentsOnly?: boolean }) {
  return <>{courses.flatMap(root => ['', ...(root.folders ?? [])].filter(path => !parentsOnly || path.split('/').length < 6).map(path =>
    <option key={root.id + '/' + path} value={root.id + (path ? '/' + path : '')}>
      {[root.title, ...path.split('/').filter(Boolean)].join(' › ')}
    </option>))}</>
}

export function childFolders(root: Course, parent: string) {
  return (root.folders ?? []).filter(path => path.split('/').slice(0, -1).join('/') === parent)
}

export type CategoryAction = 'rename' | 'remove-level' | 'delete'
export type ManageCategory = (course: Course, path: string, action: CategoryAction) => void

export function CategoryMenu({ course, path = '', label, manage, children }: {
  course: Course; path?: string; label: string; manage: ManageCategory; children?: ReactNode
}) {
  return <ActionMenu label={label}>
    {children}
    {course.role === 'owner' && <>
      <Button size="sm" variant="ghost" onClick={() => manage(course,path,'rename')}>Переименовать</Button>
      {path
        ? <Button size="sm" variant="ghost" onClick={() => manage(course,path,'remove-level')}>Объединить с родителем</Button>
        : <Button size="sm" variant="ghost" className="danger-action" onClick={() => manage(course,path,'delete')}>Удалить категорию</Button>}
    </>}
  </ActionMenu>
}

export function DirectoryRow({ course, path = '', count, manage, startNew, addFolder, children }: {
  course: Course; path?: string; count: number; manage: ManageCategory; children?: ReactNode;
  startNew: (id: string, path: string) => void; addFolder: (root: Course, path: string) => void
}) {
  const name = path.split('/').at(-1) || course.title
  return <div className="directory-row">
    <div className="directory-title" onContextMenu={event => {
      event.preventDefault()
      openRowActions(event.currentTarget, {x:event.clientX, y:event.clientY})
    }} onKeyDown={event => {
      if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) {event.preventDefault(); openRowActions(event.currentTarget)}
    }}>
      <Link to={folderLink(course.id,path)}><span className="folder-symbol"><CourseIcon course={course} path={path}/></span><strong>{name}</strong></Link>
      <span>{plural(count,['заметка','заметки','заметок'])}</span>
      <CategoryMenu label={'Действия: ' + name} course={course} path={path} manage={manage}>
        <Button asChild size="sm" variant="ghost"><Link to={folderLink(course.id,path)}>Открыть категорию</Link></Button>
        {course.role !== 'viewer' && <>
          <Button size="sm" variant="ghost" onClick={() => startNew(course.id,path)}>Добавить заметку</Button>
          {path.split('/').length < 6 && <Button size="sm" variant="ghost" onClick={() => addFolder(course,path)}>Создать подкатегорию</Button>}
        </>}
      </CategoryMenu>
    </div>
    {children}
  </div>
}

type BranchProps = {
  course: Course; notes: Note[]; search: string; selected: string; manage: ManageCategory;
  addFolder: (course: Course, parent: string) => void; startNew: (id: string, path: string) => void
}

function FolderBranch({ course, notes, search, selected, addFolder, startNew, manage, path }: BranchProps & { path: string }) {
  const location = useLocation()
  const currentBranch = selected === path || selected.startsWith(path + '/')
  const [opened, setOpened] = useState(currentBranch)
  useEffect(() => { if (currentBranch) setOpened(true) }, [currentBranch, selected])
  const expanded = !!search || opened
  const name = path.split('/').at(-1)!
  const current = location.pathname === '/courses/' + course.id && selected === path
  return <div className="folder-branch">
    <div className={`course-tree-row ${current ? 'is-current' : ''}`} onContextMenu={event => {
      event.preventDefault()
      openRowActions(event.currentTarget, {x:event.clientX,y:event.clientY})
    }} onKeyDown={event => {
      if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) { event.preventDefault(); openRowActions(event.currentTarget) }
    }}>
      <button className="tree-toggle" aria-label={(expanded ? 'Свернуть ' : 'Развернуть ') + name} aria-expanded={expanded} onClick={() => setOpened(!opened)}>{expanded ? <ChevronDown size={13}/> : <ChevronRight size={13}/>}</button>
      <Link to={folderLink(course.id,path)} className="course-tree-link" aria-current={current ? 'page' : undefined} title={name}><CourseIcon course={course} path={path} open={expanded}/><span>{name}</span></Link>
      <CategoryMenu label={'Действия: ' + name} course={course} path={path} manage={manage}>
        <Button asChild size="sm" variant="ghost"><Link to={folderLink(course.id,path)}>Открыть категорию</Link></Button>
        {course.role !== 'viewer' && <>
          <Button size="sm" variant="ghost" onClick={() => startNew(course.id,path)}>Добавить заметку</Button>
          {path.split('/').length < 6 && <Button size="sm" variant="ghost" onClick={() => addFolder(course,path)}>Создать подкатегорию</Button>}
        </>}
      </CategoryMenu>
    </div>
    {expanded && <div className="tree-children"><NoteBranches course={course} notes={notes} search={search} selected={selected} addFolder={addFolder} startNew={startNew} manage={manage} parent={path}/></div>}
  </div>
}

export function NoteBranches(props: BranchProps & { parent?: string }) {
  const { notes, course, parent = '', search } = props
  const folders = childFolders(course,parent).filter(path => !search || (course.title + '/' + path).toLowerCase().includes(search.toLowerCase()) ||
    course.folders?.some(child => child.startsWith(path + '/') && child.toLowerCase().includes(search.toLowerCase())) ||
    notes.some(n => n.category === path || n.category?.startsWith(path + '/')))
  const leaves = notes.filter(n => (n.category || '') === parent)
  return <>{folders.map(path => <FolderBranch key={path} {...props} path={path}/>)}
    {leaves.map(note => <NoteActions key={note.id} note={note} course={course} paths={course.folders ?? []}>
      <NavLink title={note.title} to={'/notes/' + note.id}><FileText size={13}/><span>{note.title}</span></NavLink>
    </NoteActions>)}
    {!leaves.length && !folders.length && <span className="tree-empty-note">Пусто</span>}
  </>
}
