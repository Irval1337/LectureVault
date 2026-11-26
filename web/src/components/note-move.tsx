import { createContext, useContext, useEffect, useId, useMemo, useRef, useState, type ComponentProps, type FormEvent, type ReactNode } from 'react'
import { Slot } from '@radix-ui/react-slot'
import { useLocation, useNavigate } from 'react-router-dom'
import { ApiError } from '../data/api'
import { canDeleteNote, type Course, type Note } from '../data/model'
import { library, useSession } from '../data/store'
import { Button } from './ui/button'
import { Dialog } from './ui/dialog'

export const noteDragType = 'application/x-lecturevault-note'
const categoryDragType = 'application/x-lecturevault-category'
const writable = (course?: Course) => course?.role === 'owner' || course?.role === 'editor'

type CategoryLocation = { course: Course; path: string }
type CategoryDestination = CategoryLocation | 'library'
type MoveDialog = {
  source: CategoryLocation
  destination?: CategoryDestination
  confirmation?: { token: string; message: string }
}
const inBranch = (path: string, branch: string) => !branch || path === branch || path.startsWith(branch + '/')
const locationKey = ({ course, path }: CategoryLocation) => [course.id, path].filter(Boolean).join('/')
const locationLabel = ({ course, path }: CategoryLocation) => [course.title, ...path.split('/').filter(Boolean)].join(' › ')
const destinationLabel = (target: CategoryDestination) => target === 'library' ? 'Корень библиотеки' : locationLabel(target)
const categoryName = ({ course, path }: CategoryLocation) => path.split('/').at(-1) || course.title
const canPromote = ({ course, path }: CategoryLocation) => course.role === 'owner' && !!path
const allowsCategoryParent = (source: CategoryLocation, { course, path }: CategoryLocation) =>
  source.course.role === 'owner' && course.role === 'owner' &&
  (source.course.id !== course.id || !inBranch(path, source.path))

function canMoveCategory(source: CategoryLocation, target: CategoryLocation) {
  if (!allowsCategoryParent(source, target)) return false
  const name = categoryName(source)
  if (name.includes('/') || [...name].length > 60 || name === '.' || name === '..') return false
  const destination = [target.path, name].filter(Boolean).join('/')
  const folders = (source.course.folders ?? []).filter(path => inBranch(path, source.path))
  if (target.course.id !== source.course.id && folders.length + Number(!source.path) + (target.course.folders?.length ?? 0) > 500) return false
  return (source.path ? folders : ['', ...folders]).every(old => {
    const next = [destination, old.slice(source.path.length).replace(/^\//, '')].filter(Boolean).join('/')
    return [...next].length <= 250 && next.split('/').length <= 6 && !target.course.folders?.includes(next)
  })
}

type NoteMove = {
  courses: Course[]
  dragged?: Note
  setDragged: (note?: Note) => void
  canMove: (note: Note, course: Course) => boolean
  move: (note: Note, course: Course, path: string) => Promise<void>
  draggedCategory?: CategoryLocation
  setDraggedCategory: (source?: CategoryLocation) => void
  canRelocate: (source: CategoryLocation) => boolean
  chooseCategory: (source: CategoryLocation) => void
  dropCategory: (source: CategoryLocation, destination: CategoryDestination) => Promise<boolean>
}
const NoteMoveContext = createContext<NoteMove | null>(null)

export function NoteMoveProvider({ courses, children }: { courses: Course[]; children: ReactNode }) {
  const user = useSession().user
  const navigate = useNavigate()
  const location = useLocation()
  const currentLocation = useRef(location)
  currentLocation.current = location
  const coursesById = useMemo(() => new Map(courses.map(course => [course.id, course])), [courses])
  const targets = useMemo(() => courses.filter(course => course.role === 'owner').flatMap(course =>
    ['', ...(course.folders ?? [])].map(path => ({ course, path }))), [courses])
  const [dragged, setDragged] = useState<Note>()
  const [draggedCategory, setDraggedCategory] = useState<CategoryLocation>()
  const [announcement, setAnnouncement] = useState('')
  const [dialog, setDialog] = useState<MoveDialog>()
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const inputId = useId()

  const canMove = (note: Note, course: Course) => {
    const source = coursesById.get(note.courseId)
    return writable(source) && writable(course) && (source?.id === course.id || canDeleteNote(source, note, user?.id))
  }
  async function move(note: Note, course: Course, path: string) {
    if (!canMove(note, course) || (note.courseId === course.id && (note.category ?? '') === path)) return
    setAnnouncement('')
    await library.moveNote(note.id, course.id, path)
    setAnnouncement(`«${note.title}» перемещена в «${[course.title, path].filter(Boolean).join(' › ')}».`)
  }

  const canRelocate = (source: CategoryLocation) => canPromote(source) ||
    (source.course.role === 'owner' && targets.some(target => canMoveCategory(source, target)))
  function chooseCategory(source: CategoryLocation) {
    setError('')
    setDialog({ source })
  }

  async function relocate({ source, destination, confirmation }: MoveDialog, fromDialog: boolean) {
    if (!destination) return false
    setAnnouncement('')
    setError('')
    try {
      const targetId = destination === 'library' ? null : destination.course.id
      const parent = destination === 'library' ? '' : destination.path
      const moved = await library.moveCategory(source.course.id, source.path, targetId, parent, confirmation?.token)
      const page = currentLocation.current
      const params = new URLSearchParams(page.search)
      const folder = params.get('folder') ?? ''
      if (page.pathname === '/courses/' + source.course.id && inBranch(folder, source.path)) {
        const suffix = folder.slice(source.path.length).replace(/^\//, '')
        const newPath = [moved.path, suffix].filter(Boolean).join('/')
        if (newPath) params.set('folder', newPath)
        else params.delete('folder')
        navigate({ pathname: '/courses/' + moved.courseId, search: params.toString(), hash: page.hash }, { replace: true })
      }
      setDialog(undefined)
      setAnnouncement(destination === 'library'
        ? `Категория «${categoryName(source)}» теперь на первом уровне библиотеки.`
        : `Категория «${categoryName(source)}» перемещена в «${locationLabel(destination)}».`)
      return true
    } catch (error) {
      if (error instanceof ApiError && error.code === 'CATEGORY_ACCESS_CHANGE' && error.confirmation) {
        setDialog({ source, destination, confirmation: { token: error.confirmation, message: error.message } })
      } else if (fromDialog) {
        setError((error as Error).message)
      } else {
        library.reportError(error)
      }
      return false
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!dialog || saving) return
    setSaving(true)
    try { await relocate(dialog, true) }
    finally { setSaving(false) }
  }

  return <NoteMoveContext.Provider value={{ courses, dragged, setDragged, canMove, move, draggedCategory, setDraggedCategory,
    canRelocate, chooseCategory, dropCategory: (source, destination) => relocate({ source, destination }, false) }}>
    {children}
    {dialog && <Dialog open onOpenChange={open => { if (!open && !saving) setDialog(undefined) }}
      title={dialog.confirmation ? 'Подтвердить перенос' : 'Переместить категорию'} description={locationLabel(dialog.source)}>
      <form className="course-form" onSubmit={submit}>
        {dialog.confirmation && dialog.destination
          ? <><p className="field-label">Категория назначения</p><p>{destinationLabel(dialog.destination)}</p>
            <p className="field-hint">{dialog.confirmation.message}</p></>
          : <>
            <label className="field-label" htmlFor={inputId}>Куда переместить</label>
            <select id={inputId} value={dialog.destination === 'library' ? 'library' : dialog.destination ? locationKey(dialog.destination) : ''} onChange={event => {
              const destination = event.target.value === 'library' ? 'library' : targets.find(target => locationKey(target) === event.target.value)
              setDialog({ ...dialog, destination })
              setError('')
            }}>
              <option value="" disabled>Выберите категорию</option>
              {canPromote(dialog.source) && <option value="library">В корень библиотеки</option>}
              {targets.filter(target => canMoveCategory(dialog.source, target)).map(target =>
                <option value={locationKey(target)} key={locationKey(target)}>{locationLabel(target)}</option>)}
            </select>
            <p className="field-hint">Вместе с категорией переместятся её подкатегории, заметки и материалы.</p>
          </>}
        {error && <p className="field-error" role="alert">{error}</p>}
        <div className="dialog-actions">
          <Button type="button" variant="ghost" disabled={saving} onClick={() => setDialog(undefined)}>Отмена</Button>
          <Button type="submit" disabled={!dialog.destination || saving}>{saving ? 'Переносим…' : 'Переместить'}</Button>
        </div>
      </form>
    </Dialog>}
    <p aria-live="polite" className="sr-only">{announcement}</p>
  </NoteMoveContext.Provider>
}

export function useNoteMove() { return useContext(NoteMoveContext)! }

export function CategoryExplorer({ children }: { children: ReactNode }) {
  const { draggedCategory, setDraggedCategory, dropCategory } = useNoteMove()
  const [over, setOver] = useState(false)
  const active = !!draggedCategory && canPromote(draggedCategory)
  useEffect(() => { if (!active) setOver(false) }, [active])
  function isRootSpace(element: EventTarget | null, explorer: HTMLElement) {
    return element === explorer || (element instanceof Element && !!element.closest('.category-root-drop'))
  }
  return <nav className="explorer" aria-label="Категории и конспекты"
    onDragOver={event => {
      if (!active || !event.dataTransfer.types.includes(categoryDragType)) return
      const rootSpace = isRootSpace(event.target, event.currentTarget)
      setOver(rootSpace)
      if (rootSpace) {
        event.preventDefault()
        event.dataTransfer.dropEffect = 'move'
      }
    }}
    onDragLeave={event => {
      if (!isRootSpace(event.relatedTarget, event.currentTarget)) setOver(false)
    }}
    onDrop={event => {
      if (!active || !draggedCategory || !isRootSpace(event.target, event.currentTarget) ||
          event.dataTransfer.getData(categoryDragType) !== locationKey(draggedCategory)) return
      event.preventDefault()
      setDraggedCategory(undefined)
      void dropCategory(draggedCategory, 'library')
    }}>
    {children}
    {active && <div className="category-root-drop" data-note-drop={over ? '' : undefined}>В корень библиотеки</div>}
  </nav>
}

export function CategoryDropArea({ course, path = '', asChild, dropOnly, onExpand, ...props }: ComponentProps<'div'> & {
  course: Course; path?: string; asChild?: boolean; dropOnly?: boolean; onExpand?: () => void
}) {
  const { dragged, canMove, move, setDragged, draggedCategory, setDraggedCategory, canRelocate, dropCategory } = useNoteMove()
  const [over, setOver] = useState(false)
  const expand = useRef(onExpand)
  expand.current = onExpand
  const target = { course, path }
  const draggable = !dropOnly && canRelocate(target)
  const dragType = draggedCategory ? categoryDragType : noteDragType
  const allowed = draggedCategory ? allowsCategoryParent(draggedCategory, target) : !!dragged && canMove(dragged, course)
  const valid = draggedCategory ? canMoveCategory(draggedCategory, target) :
    allowed && !!dragged && (dragged.courseId !== course.id || (dragged.category ?? '') !== path)

  useEffect(() => {
    if (!over || !allowed) return
    const timer = window.setTimeout(() => expand.current?.(), 550)
    return () => window.clearTimeout(timer)
  }, [over, allowed])
  useEffect(() => { if (!dragged && !draggedCategory) setOver(false) }, [dragged, draggedCategory])

  const Component = asChild ? Slot : 'div'
  return <Component {...props} draggable={draggable || (asChild ? false : undefined)}
    data-category-drag={draggable ? '' : undefined} data-note-drop={over && valid ? '' : undefined}
    onDragStart={event => {
      if (!draggable) return
      if (!(event.target instanceof Element) || !event.currentTarget.contains(event.target) ||
          event.target.closest('button, summary, input, select, textarea, .action-menu-popover')) {
        event.preventDefault()
        return
      }
      event.stopPropagation()
      event.dataTransfer.setData(categoryDragType, locationKey(target))
      event.dataTransfer.effectAllowed = 'move'
      setDraggedCategory(target)
    }}
    onDragEnd={() => setDraggedCategory(undefined)}
    onDragOver={event => {
      if (!allowed || !event.dataTransfer.types.includes(dragType)) return
      event.stopPropagation()
      setOver(true)
      if (valid) { event.preventDefault(); event.dataTransfer.dropEffect = 'move' }
    }}
    onDragLeave={event => {
      if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) setOver(false)
    }}
    onDrop={event => {
      const value = event.dataTransfer.getData(dragType)
      if (!valid || value !== (draggedCategory ? locationKey(draggedCategory) : dragged?.id)) return
      event.preventDefault()
      event.stopPropagation()
      setOver(false)
      if (draggedCategory) {
        setDraggedCategory(undefined)
        void dropCategory(draggedCategory, target).then(moved => { if (moved) expand.current?.() })
      } else if (dragged) {
        setDragged(undefined)
        void move(dragged, course, path).then(() => expand.current?.()).catch(library.reportError)
      }
    }}
  />
}
