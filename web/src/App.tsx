import { lazy, Suspense, useEffect, useLayoutEffect, useRef, useState, type FormEvent } from 'react'
import { Link, NavLink, Route, Routes, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { ArrowLeft, ArrowRight, BookOpen, BookOpenText, Check, CheckCircle2, ChevronDown, ChevronRight, Circle, Clock3, FileText, Folder, FolderOpen, HelpCircle, House, Link2, ListOrdered, LoaderCircle, Plus, Search, Upload, Users, X, XCircle } from 'lucide-react'
import { Button } from './components/ui/button'
import { ActionMenu } from './components/ui/action-menu'
import { Dialog } from './components/ui/dialog'
import { TabBar } from './components/ui/tab-bar'
import { PageHeading } from './components/ui/page-heading'
import { ActivityRail, WorkspaceHeader, ReaderActions, ReaderInspector } from './components/workspace'
import { library, useLibrary, useSession } from './data/store'
import { CreateNotePage } from './components/create-note'
import { CourseMembers } from './components/course-members'
import { ProviderConnections } from './components/providers'
const NoteEditor = lazy(() => import('./components/editor'))
import { SettingsPage } from './components/settings'
import { isActive, LENGTH_LABELS, STAGES, STATUS_LABELS, type AppState, type Course, type Job, type Note, type Workspace } from './data/model'
import { formatDate, plural } from './lib/utils'
import { QuartzArticle, QuartzToc } from './components/reader'

const spaceLabel = (space: Workspace) => space === 'personal' ? 'Моя библиотека' : 'Общая библиотека'

export default function App() {
  const savedState = useLibrary()
  const session = useSession()
  const [newDialog, setNewDialog] = useState(false)
  const [newCourseId, setNewCourseId] = useState('')
  const [newIntent, setNewIntent] = useState<'manual' | 'generate'>('generate')
  const [newError, setNewError] = useState('')
  const [creating, setCreating] = useState(false)
  const [imported, setImported] = useState(false)
  const location = useLocation()
  const navigate = useNavigate()
  const basePath = ''
  const state = savedState
  const [space, setSpace] = useState<Workspace>(() => localStorage.getItem(`lecturevault.space.${session.user?.id}`) === 'shared' ? 'shared' : 'personal')
  const [search, setSearch] = useState('')
  const [courseDialog, setCourseDialog] = useState(false)
  const [createAndStart, setCreateAndStart] = useState(false)
  const [mobileNav, setMobileNav] = useState(false)
  const [help, setHelp] = useState(false)
  const [navCollapsed, setNavCollapsed] = useState(false)
  const [contextOpen, setContextOpen] = useState(true)
  const searchTimer = useRef<number | undefined>(undefined)
  const [courseTitle, setCourseTitle] = useState('')
  const [courseError, setCourseError] = useState('')
  const activeNote = state.notes.find(n => location.pathname === `${basePath}/notes/${n.id}` || location.pathname === `${basePath}/notes/${n.id}/edit`)
  const isEditing = location.pathname.endsWith('/edit')
  const activeCourse = state.courses.find(c => location.pathname === `${basePath}/courses/${c.id}` || c.id === activeNote?.courseId)
  const isNew = location.pathname === '/new'
  const isQueue = location.pathname === '/queue'
  const isSettings = location.pathname === '/settings'
  const courses = state.courses.filter(c => c.workspace === space)
  const activeJobs = state.jobs.filter(j => isActive(j) || j.status === 'queued')
  const selectedJob = state.jobs.find(j => j.id === new URLSearchParams(location.search).get('job')) ?? state.jobs.find(isActive) ?? state.jobs.at(-1)
  const hasContext = Boolean((activeNote && !isEditing && contextOpen && (activeNote.jobId || /^#{2,4}\s/m.test(activeNote.markdown))) || (isQueue && selectedJob))

  useEffect(() => {
    const tick = () => { if (!document.hidden) void library.tick() }
    tick()
    const timer = window.setInterval(tick, activeJobs.length ? 1500 : 10000)
    window.addEventListener('focus', tick)
    return () => { window.clearInterval(timer); window.removeEventListener('focus', tick) }
  }, [activeJobs.length])
  useEffect(() => {
    if (activeNote && (!activeNote.contentLoaded || !isEditing)) void library.loadNote(activeNote.id, !isEditing)
  }, [activeNote?.id, activeNote?.revision, isEditing])
  useLayoutEffect(() => {
    window.scrollTo(0, 0)
  }, [location.pathname])
  useEffect(() => {
    const closeNavigation = () => setMobileNav(false)
    window.addEventListener('popstate', closeNavigation)
    return () => window.removeEventListener('popstate', closeNavigation)
  }, [])
  useEffect(() => {
    if (activeCourse) { setSpace(activeCourse.workspace); localStorage.setItem(`lecturevault.space.${session.user?.id}`, activeCourse.workspace) }
  }, [activeCourse])
  useEffect(() => {
    // Direct links to a document should reveal its location in the explorer.
    if (activeNote && !savedState.expandedCourses.includes(activeNote.courseId)) library.toggleCourse(activeNote.courseId)
  }, [activeNote?.id])
  useEffect(() => { document.title = `${activeNote?.title ?? (isNew ? 'Сгенерировать конспект' : isQueue ? 'Очередь заданий' : isSettings ? 'Настройки' : activeCourse?.title ?? 'Библиотека')} · LectureVault` }, [activeNote, activeCourse, isNew, isQueue, isSettings])
  useEffect(() => {
    if (!mobileNav) return
    const before = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const escape = (e: KeyboardEvent) => { if (e.key === 'Escape') setMobileNav(false) }
    window.addEventListener('keydown', escape)
    return () => { document.body.style.overflow = before; window.removeEventListener('keydown', escape) }
  }, [mobileNav])

  function focusSearch() {
    setNavCollapsed(false)
    const mobile = window.innerWidth < 768
    if (mobile) setMobileNav(true)
    window.clearTimeout(searchTimer.current)
    searchTimer.current = window.setTimeout(() => {
      document.querySelector<HTMLInputElement>(`${mobile ? '.mobile-sidebar-dialog' : '.desktop-sidebar'} input[aria-label="Поиск по библиотеке"]`)?.focus()
    }, 80)
  }
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k' && !document.querySelector('[role="dialog"]:not(.mobile-sidebar-dialog)')) { event.preventDefault(); focusSearch() }
    }
    window.addEventListener('keydown', key)
    return () => { window.removeEventListener('keydown', key); window.clearTimeout(searchTimer.current) }
  }, [])

  function openCourseDialog(start = false) { setMobileNav(false); setCourseTitle(''); setCourseError(''); setCreateAndStart(start); setCourseDialog(true) }
  function startNew(courseId?: string) {
    setMobileNav(false); setNewError('')
    setNewCourseId(courseId ?? (activeCourse?.role !== 'viewer' ? activeCourse?.id : undefined) ?? courses.find(c => c.role !== 'viewer')?.id ?? '')
    setNewDialog(true)
  }
  async function chooseNew(intent: 'manual' | 'generate') {
    setNewIntent(intent); setNewError('')
    if (!newCourseId) { setNewDialog(false); openCourseDialog(true); return }
    setCreating(true)
    try {
      if (intent === 'manual') {
        const note = await library.createNote(newCourseId)
        setNewDialog(false); navigate(`/notes/${note.id}/edit`)
      } else { setNewDialog(false); navigate(`/new?course=${newCourseId}`) }
    } catch (error) { setNewError((error as Error).message) }
    finally { setCreating(false) }
  }
  function switchSpace(value: Workspace) { setSpace(value); localStorage.setItem(`lecturevault.space.${session.user?.id}`, value); setSearch(''); setMobileNav(false); navigate('/') }
  function toggleCourse(id: string) { library.toggleCourse(id) }
  function toggleTheme() { library.setReaderTheme(state.readerTheme === 'dark' ? 'light' : 'dark') }
  async function submitCourse(event: FormEvent) {
    event.preventDefault()
    if (!courseTitle.trim()) { setCourseError('Введите название курса.'); return }
    if (courses.some(c => c.title.toLocaleLowerCase() === courseTitle.trim().toLocaleLowerCase())) { setCourseError('Курс с таким названием уже есть в этом пространстве.'); return }
    try {
      const id = await library.createCourse(courseTitle, space)
      if (createAndStart && newIntent === 'manual') {
        const note = await library.createNote(id); setCourseDialog(false); navigate(`/notes/${note.id}/edit`)
      } else { setCourseDialog(false); navigate(createAndStart ? `/new?course=${id}` : `/courses/${id}`) }
    } catch (error) { setCourseError((error as Error).message) }
  }

  const sidebar = <>
    <Link className="brand" to={basePath || '/'} aria-label="LectureVault — главная" onClick={() => setSearch('')}><BookOpenText size={20} strokeWidth={1.6} /><span>LectureVault</span></Link>
    <div className="space-picker"><span aria-hidden="true">{space === 'personal' ? <House size={15} /> : <Users size={15} />}</span><select aria-label="Пространство" value={space} onChange={e => switchSpace(e.target.value as Workspace)}><option value="personal">Личное пространство</option><option value="shared">Общее пространство</option></select><ChevronDown size={13} className="select-chevron" /></div>
    <label className="search-box"><Search size={15} /><input aria-label="Поиск по библиотеке" placeholder="Поиск" value={search} onChange={e => { setSearch(e.target.value); if (location.pathname !== (basePath || '/')) { navigate(basePath || '/') } }} />{search && <button className="clear-search" aria-label="Очистить поиск" onClick={() => setSearch('')}><X size={14} /></button>}</label>
    <nav className="main-nav mobile-primary-nav" aria-label="Мобильная навигация"><NavLink to={basePath || '/'} end><BookOpen size={16} />Библиотека</NavLink>{<button className="mobile-new" onClick={() => startNew()}><Plus size={16} />Новый</button>}{<NavLink to="/queue"><ListOrdered size={16} />Очередь заданий{activeJobs.length > 0 && <span className="nav-count">{activeJobs.length}</span>}</NavLink>}</nav>
    <div className="explorer-header"><span>Курсы</span>{<button className="icon-action" aria-label="Создать курс" title="Создать курс" onClick={() => openCourseDialog()}><Plus size={16} /></button>}</div>
    <nav className="explorer" aria-label="Курсы и конспекты">
      {!courses.length && <p className="tree-empty">Нет курсов</p>}
      {courses.filter(c => !search || c.title.toLowerCase().includes(search.toLowerCase()) || state.notes.some(n => n.courseId === c.id && n.title.toLowerCase().includes(search.toLowerCase()))).map(course => {
        const expanded = state.expandedCourses.includes(course.id) || Boolean(search)
        const notes = state.notes.filter(n => n.courseId === course.id && (!search || n.title.toLowerCase().includes(search.toLowerCase()) || course.title.toLowerCase().includes(search.toLowerCase())))
        return <div className="course-tree" key={course.id}><div className={`course-tree-row ${activeCourse?.id === course.id && !activeNote ? 'is-current' : ''}`}><button className="tree-toggle" aria-label={`${expanded ? 'Свернуть' : 'Развернуть'} курс ${course.title}`} aria-expanded={expanded} onClick={() => toggleCourse(course.id)}>{expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}</button><Link to={`${basePath}/courses/${course.id}`} className="course-tree-link" title={course.title}>{expanded ? <FolderOpen size={14} /> : <Folder size={14} />}<span>{course.title}</span></Link>{course.role !== 'viewer' && <button className="tree-add icon-action" aria-label={`Новый в курсе ${course.title}`} onClick={() => startNew(course.id)}><Plus size={14} /></button>}</div>{expanded && <div className="tree-children">{!notes.length ? <span className="tree-empty-note">Нет конспектов</span> : notes.map(note => <NavLink key={note.id} to={`${basePath}/notes/${note.id}`} title={note.title}><FileText size={13} /><span>{note.title}</span></NavLink>)}</div>}</div>
      })}
    </nav>
    <div className="sidebar-bottom">{<><ProviderConnections account />{library.hasLegacy() && !imported && <button className="help-link" onClick={() => { void library.importLocal().then(() => setImported(true)).catch(library.reportError) }}>Импортировать локальные курсы и заметки</button>}{imported && <p className="field-hint" role="status">Импортировано. Исходная копия в браузере сохранена.</p>}</>}<button className="help-link mobile-only" onClick={() => { setMobileNav(false); setHelp(true) }}><HelpCircle size={15} />Как это работает</button></div>
  </>

  return <>
    <a href="#main-content" className="skip-link">К содержимому</a>
    <div className="page app-layout"><div id="quartz-body" className={`workspace-layout ${hasContext ? 'with-context' : ''} ${navCollapsed ? 'nav-collapsed' : ''}`}>
      <ActivityRail basePath={basePath} queueCount={activeJobs.length} section={isNew ? 'create' : isQueue ? 'queue' : isSettings ? 'settings' : 'library'} onCreate={() => startNew()} onSearch={focusSearch} onHelp={() => setHelp(true)} />
      <aside className="sidebar left desktop-sidebar">{sidebar}</aside>
      <main className={`center main-content ${activeNote ? `reader-page reader-${state.readerTheme}` : ''}`} id="main-content">
        <WorkspaceHeader collapsed={navCollapsed} onToggleNavigation={() => setNavCollapsed(!navCollapsed)} label={activeNote ? `${activeNote.title}.md` : isNew ? 'Сгенерировать конспект' : isQueue ? 'Очередь заданий' : isSettings ? 'Настройки' : activeCourse?.title ?? 'Библиотека'} mode={activeNote ? isEditing ? 'Редактирование' : 'Чтение' : ''} onOpenNavigation={() => setMobileNav(true)} breadcrumbs={<><Link to={basePath || '/'}>{spaceLabel(space)}</Link>{activeCourse && <><ChevronRight size={12} />{activeNote ? <Link to={`${basePath}/courses/${activeCourse.id}`}>{activeCourse.title}</Link> : <span>{activeCourse.title}</span>}</>}{!activeCourse && (isNew || isQueue || isSettings) && <><ChevronRight size={12} /><span>{isNew ? 'Новый конспект' : isSettings ? 'Настройки' : 'Очередь заданий'}</span></>}</>} actions={activeNote && !isEditing ? <ReaderActions note={activeNote} contextAvailable={Boolean(activeNote.jobId || /^#{2,4}\s/m.test(activeNote.markdown))} onEdit={activeCourse?.role !== 'viewer' ? () => navigate(`/notes/${activeNote.id}/edit`) : undefined} onDelete={activeCourse?.role !== 'viewer' ? () => { if (window.confirm('Удалить эту заметку? Это действие нельзя отменить.')) void library.deleteNote(activeNote.id).then(() => navigate(`/courses/${activeNote.courseId}`)).catch(library.reportError) } : undefined} theme={state.readerTheme} onToggleTheme={toggleTheme} contextOpen={contextOpen} onToggleContext={() => setContextOpen(!contextOpen)} /> : undefined} />
        {library.getError() && <div className="storage-error" role="alert">{library.getError()}</div>}
        <div className="content-body">
          <Routes>
            <Route path="/" element={<LibraryPage state={state} courses={courses} search={search} createCourse={() => openCourseDialog()} startNew={startNew} />} />
            <Route path="/courses/:id" element={<CoursePage state={state} startNew={startNew} />} />
            <Route path="/new" element={<CreateNotePage state={state} createCourse={() => { setNewIntent('generate'); openCourseDialog(true) }} />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/queue" element={<QueuePage state={state} startNew={() => startNew()} />} />
            <Route path="/notes/:id/edit" element={activeNote && activeNote.contentLoaded === false ? <p role="status">Открываем заметку…</p> : activeNote ? <Suspense fallback={<p role="status">Открываем редактор…</p>}><NoteEditor key={activeNote.id} note={activeNote} canEdit={activeCourse?.role !== 'viewer'} /></Suspense> : <NotFound />} />
            <Route path="/notes/:id" element={<ReaderPage state={state} />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </div>
      </main>
      {hasContext && <aside className="sidebar right context-sidebar">{activeNote ? <ReaderInspector key={activeNote.id} markdown={activeNote.markdown} source={state.jobs.some(j => j.id === activeNote.jobId) ? <SourceDetails note={activeNote} state={state} /> : null} /> : <JobSteps job={selectedJob} />}</aside>}
    </div></div>
    <Dialog open={newDialog} onOpenChange={setNewDialog} title="Новый" description="Создать заметку вручную или получить конспект из видео"><div className="new-menu">{courses.some(c => c.role !== 'viewer') && <><label className="field-label" htmlFor="new-course">Курс</label><select id="new-course" value={newCourseId} onChange={e => setNewCourseId(e.target.value)}>{courses.filter(c => c.role !== 'viewer').map(c => <option key={c.id} value={c.id}>{c.title}</option>)}</select></>}<Button variant="outline" disabled={creating} onClick={() => void chooseNew('manual')}><FileText size={17} />Создать заметку</Button><Button variant="outline" disabled={creating} onClick={() => void chooseNew('generate')}><Upload size={17} />Сгенерировать конспект</Button>{!courses.some(c => c.role !== 'viewer') && <p className="field-hint">Сначала предложим создать курс.</p>}{newError && <p className="field-error" role="alert">{newError}</p>}</div></Dialog>
    <Dialog open={mobileNav} onOpenChange={setMobileNav} title="Навигация" description="Пространства, курсы и конспекты" className="mobile-sidebar-dialog"><div className="mobile-sidebar-content" onClick={event => { if ((event.target as HTMLElement).closest('a[href]')) setMobileNav(false) }}>{sidebar}</div></Dialog>
    <Dialog open={courseDialog} onOpenChange={setCourseDialog} title={courses.length ? 'Новый курс' : 'Первый курс'} description={spaceLabel(space)}><form onSubmit={submitCourse} className="course-form"><label className="field-label" htmlFor="course-title">Название курса</label><input id="course-title" autoFocus maxLength={80} placeholder="Например, Распределённые системы" value={courseTitle} onChange={e => { setCourseTitle(e.target.value); setCourseError('') }} aria-invalid={!!courseError} aria-describedby={courseError ? 'course-error' : undefined} />{courseError && <p id="course-error" className="field-error" role="alert">{courseError}</p>}<div className="dialog-actions"><Button type="button" variant="ghost" onClick={() => setCourseDialog(false)}>Отмена</Button><Button type="submit">Создать курс</Button></div></form></Dialog>
    <Dialog open={help} onOpenChange={setHelp} title="Как это работает" description="Заметки и конспекты"><div className="help-content"><ol><li>Создайте курс.</li><li>Выберите ручную заметку или генерацию из видео.</li><li>Дождитесь завершения задания в очереди.</li><li>Откройте конспект и скачайте Markdown.</li></ol><p>Для генерации подключите провайдера в настройках, затем загрузите видео и при необходимости PDF. Расшифровка и текст слайдов передаются выбранной модели. Ручные заметки не требуют подключения.</p><p>«Новый» также создаёт пустую заметку без генерации. Сохранённые документы хранятся на сервере; несохранённые черновики — в этом браузере. </p><Button variant="outline" onClick={() => setHelp(false)}>Понятно</Button></div></Dialog>
  </>
}

function LibraryPage({ state, courses, search, createCourse, startNew, basePath = '' }: { state: AppState; courses: Course[]; search: string; createCourse?: () => void; startNew?: (id?: string) => void; basePath?: string }) {
  const notes = state.notes.filter(n => courses.some(c => c.id === n.courseId))
  const hits = notes.filter(n => n.title.toLowerCase().includes(search.toLowerCase()) || courses.find(c => c.id === n.courseId)?.title.toLowerCase().includes(search.toLowerCase()))
  if (search) return <><PageHeading title="Результаты поиска" description={`«${search}» · ${plural(hits.length, ['конспект', 'конспекта', 'конспектов'])}`} /><NoteTable notes={hits} courses={courses} basePath={basePath} />{!hits.length && <div className="quiet-empty"><p>Ничего не найдено. Попробуйте другое название.</p></div>}</>
  return <>
    <PageHeading title="Библиотека" description={courses.length ? `${plural(courses.length, ['курс', 'курса', 'курсов'])} · ${plural(notes.length, ['конспект', 'конспекта', 'конспектов'])}${basePath ? ' · демонстрационные материалы' : ''}` : undefined} action={courses.length > 0 && createCourse && <Button variant="outline" size="sm" onClick={createCourse}><Plus size={14} />Создать курс</Button>} />
    {!courses.length ? <div className="library-empty"><Folder size={20} strokeWidth={1.5} /><div><h2>Нет курсов</h2><p>Создайте курс для заметок и конспектов.</p><Button size="sm" onClick={createCourse}><Plus size={15} />Создать курс</Button></div></div> : <><div className="section-heading"><h2>Курсы</h2></div><div className="course-list">{courses.map(course => <div className="course-list-row" key={course.id}><Link to={`${basePath}/courses/${course.id}`} className="course-list-main"><Folder size={17} strokeWidth={1.5} /><strong>{course.title}</strong><span className="course-note-count">{plural(notes.filter(n => n.courseId === course.id).length, ['конспект', 'конспекта', 'конспектов'])}</span><ChevronRight size={14} className="row-chevron" /></Link>{startNew && course.role !== 'viewer' && <button className="icon-action" aria-label={`Добавить конспект в ${course.title}`} onClick={() => startNew(course.id)}><Plus size={16} /></button>}</div>)}</div>{notes.length > 0 && <><div className="section-heading recent-heading"><h2>Конспекты</h2><span>По дате добавления</span></div><NoteTable notes={notes.slice().sort((a,b) => b.createdAt - a.createdAt)} courses={courses} basePath={basePath} /></>}</>}
  </>
}

function NoteTable({ notes, courses, basePath = '' }: { notes: Note[]; courses?: Course[]; basePath?: string }) {
  return <div className={`note-list ${courses ? '' : 'without-course'}`} role="table" aria-label="Конспекты"><div className="note-table-head" role="row"><span role="columnheader">Название</span>{courses && <span role="columnheader">Курс</span>}<span role="columnheader">Добавлен</span></div>{notes.map(note => <Link className="note-list-row" role="row" key={note.id} to={`${basePath}/notes/${note.id}`}><span className="note-name" role="cell"><FileText size={16} strokeWidth={1.5} /><strong title={note.title}>{note.title}</strong></span>{courses && <span className="note-course" role="cell">{courses.find(c => c.id === note.courseId)?.title}</span>}<time role="cell" dateTime={new Date(note.createdAt).toISOString()}>{formatDate(note.createdAt)}</time></Link>)}</div>
}

function CoursePage({ state, startNew, basePath = '' }: { state: AppState; startNew?: (id?: string) => void; basePath?: string }) {
  const { id } = useParams()
  const navigate = useNavigate()
  const course = state.courses.find(c => c.id === id)
  if (!course) return <NotFound />
  const notes = state.notes.filter(n => n.courseId === id)
  const pending = state.jobs.filter(j => j.courseId === id && (j.status === 'queued' || isActive(j)))
  return <><PageHeading title={course.title} description={`${plural(notes.length, ['конспект', 'конспекта', 'конспектов'])} · ${basePath ? 'Демонстрационные материалы' : spaceLabel(course.workspace)}`} action={<div className="page-actions">{course.role === 'owner' && <ActionMenu label="Действия курса"><Button size="sm" variant="ghost" className="danger-action" onClick={() => { if (window.confirm('Удалить курс со всеми заметками и материалами? Это действие нельзя отменить.')) void library.deleteCourse(course.id).then(() => navigate('/')).catch(library.reportError) }}>Удалить курс</Button></ActionMenu>}{!basePath && course.workspace === 'shared' && <CourseMembers course={course} />}{startNew && course.role !== 'viewer' && <Button size="sm" onClick={() => startNew(course.id)}><Plus size={15} />Новый</Button>}</div>} />{pending.length > 0 && <Link to="/queue" className="pending-notice"><LoaderCircle size={16} className="spin" />{plural(pending.length, ['задание', 'задания', 'заданий'])} в обработке<ArrowRight size={15} /></Link>}{notes.length ? <NoteTable notes={notes.slice().reverse()} basePath={basePath} /> : <div className="quiet-empty course-empty"><p>В курсе пока нет заметок.</p>{startNew && course.role !== 'viewer' && <Button variant="outline" size="sm" onClick={() => startNew(course.id)}>Новый<Plus size={15} /></Button>}</div>}</>
}

function Status({ job }: { job: Job }) {
  return <span className={`job-status status-${job.status}`}>{isActive(job) ? <LoaderCircle size={14} className="spin" /> : job.status === 'ready' ? <CheckCircle2 size={14} /> : job.status === 'failed' ? <XCircle size={14} /> : job.status === 'cancelled' ? <X size={13} /> : <Clock3 size={14} />}{STATUS_LABELS[job.status]}</span>
}

function QueuePage({ state, startNew }: { state: AppState; startNew: () => void }) {
  const [params, setParams] = useSearchParams()
  const session = useSession()
  const [filter, setFilter] = useState<'all' | 'active' | 'ready'>('all')
  const jobs = state.jobs.filter(j => filter === 'all' || (filter === 'ready' ? j.status === 'ready' : j.status === 'queued' || isActive(j))).slice().reverse()
  const selectedJob = state.jobs.find(j => j.id === params.get('job')) ?? state.jobs.find(isActive) ?? state.jobs.at(-1)
  return <><PageHeading title="Очередь заданий" /><div className="queue-toolbar"><TabBar label="Фильтр заданий" panelId="queue-results" className="queue-tabs" value={filter} onValueChange={setFilter} items={(['all', 'active', 'ready'] as const).map(value => ({ value, label: <>{{ all: 'Все', active: 'В работе', ready: 'Готовые' }[value]}<span>{state.jobs.filter(j => value === 'all' || (value === 'ready' ? j.status === 'ready' : isActive(j) || j.status === 'queued')).length}</span></> }))} /><span className="queue-mode"><span className="demo-dot" />Последовательно</span></div><div aria-live="polite" className="sr-only">{selectedJob ? `${selectedJob.title}: ${STATUS_LABELS[selectedJob.status]}` : 'Очередь пуста'}</div>
    <div id="queue-results" role="tabpanel" aria-labelledby={`queue-results-${filter}-tab`}>{!jobs.length ? <div className="quiet-empty"><ListOrdered size={20} strokeWidth={1.5} /><h2>{state.jobs.length ? 'Здесь пока нет заданий' : 'Очередь пуста'}</h2><p>{state.jobs.length ? 'Выберите другой фильтр или создайте новый конспект.' : 'Загрузите видео, чтобы создать задание.'}</p><Button variant="outline" onClick={startNew}>Новый конспект<Plus size={16} /></Button></div> : <div className="job-list">{jobs.map(job => <div className={`job-item ${job.id === selectedJob?.id ? 'selected' : ''}`} key={job.id}><div className={`job-file-icon ${job.status === 'ready' ? 'job-file-ready' : ''}`}>{job.status === 'ready' ? <FileText size={21} strokeWidth={1.4} /> : job.sourceType === 'url' ? <Link2 size={20} /> : <BookOpen size={21} strokeWidth={1.4} />}</div><div className="job-content"><button title={`${job.title} — источник: ${job.sourceName}`} className="job-title" onClick={() => setParams({ job: job.id })}>{job.title}</button><p>{state.courses.find(c => c.id === job.courseId)?.title} <span>·</span> {job.preset === 'lecture' ? 'Лекция' : 'Семинар'} <span>·</span> {LENGTH_LABELS[job.length]} <span>·</span> {job.mode === 'real' ? `Whisper + ${job.provider === 'claude' ? 'Claude' : 'Codex'}` : 'Демо'}</p><Status job={job} />{job.error && <p className="job-error">{job.error}</p>}</div><div className="job-actions">{job.status === 'ready' && job.noteId && state.notes.some(note => note.id === job.noteId) && <Button asChild size="sm" variant="outline"><Link to={`/notes/${job.noteId}`}>Открыть<ArrowRight size={14} /></Link></Button>}{job.status === 'queued' && job.createdBy === session.user?.id && <Button size="sm" variant="ghost" onClick={() => void library.cancel(job.id).catch(library.reportError)}>Отменить</Button>}{job.status === 'failed' && job.createdBy === session.user?.id && <Button size="sm" variant="outline" onClick={() => void library.retry(job.id).catch(library.reportError)}>Повторить</Button>}{job.status === 'ready' && !job.noteId && <span className="field-hint">Результат удалён</span>}</div></div>)}</div>}
    </div><div className="queue-footnote"><Clock3 size={15} /><p>Обработка продолжается, когда вы переходите на другой экран. Ожидающее задание можно отменить, ошибку — повторить.</p></div><div className="mobile-context"><JobSteps job={selectedJob} /></div>
  </>
}

function ReaderPage({ state, basePath = '' }: { state: AppState; basePath?: string }) {
  const { id } = useParams()
  const note = state.notes.find(n => n.id === id)
  const [mobileToc, setMobileToc] = useState(false)
  useEffect(() => { setMobileToc(false) }, [id])
  if (!note) return <NotFound />
  if (note.contentLoaded === false) return <p role="status">Открываем заметку…</p>
  const job = state.jobs.find(j => j.id === note.jobId)
  return <><div className="reader-heading"><h1>{note.title}</h1><div className="reader-info"><div className="reader-meta"><time dateTime={new Date(note.createdAt).toISOString()}>{formatDate(note.createdAt)}</time>{note.origin !== 'manual' && <span>{note.isMock ? 'Учебный пример' : `Из видео · ${job?.provider === 'claude' ? 'Claude' : 'Codex'}`}</span>}</div></div></div>{!basePath && note.isMock && <p className="reader-demo">Учебный пример. Содержимое источника не анализировалось.</p>}{/^#{2,4}\s/m.test(note.markdown) && <div className="mobile-toc"><button aria-expanded={mobileToc} onClick={() => setMobileToc(!mobileToc)}><ListOrdered size={16} />Оглавление<ChevronDown size={15} /></button>{mobileToc && <div onClick={event => { if ((event.target as HTMLElement).closest('a')) setMobileToc(false) }}><QuartzToc markdown={note.markdown} /></div>}</div>}<QuartzArticle markdown={note.markdown} /><div className="mobile-context"><SourceDetails note={note} state={state} /></div><Link className="back-link" to={`${basePath}/courses/${note.courseId}`}><ArrowLeft size={15} />К курсу</Link></>
}

function SourceDetails({ note, state }: { note: Note; state: AppState }) {
  const job = state.jobs.find(j => j.id === note.jobId)
  if (!job) return null
  return <details className="source-details" open><summary>Об источнике<ChevronDown size={13} /></summary><dl><dt>{job.sourceType === 'file' ? 'Видеофайл' : 'Ссылка'}</dt><dd title={job.sourceName}>{job.sourceAssetId ? <a href={`/api/assets/${job.sourceAssetId}`} download>{job.sourceName}</a> : job.sourceName}</dd><dt>Параметры</dt><dd>{job.preset === 'lecture' ? 'Лекция' : 'Семинар'} · {LENGTH_LABELS[job.length]}</dd>{job.pdfName && <><dt>Презентация</dt><dd title={job.pdfName}>{job.pdfAssetId ? <a href={`/api/assets/${job.pdfAssetId}`} download>{job.pdfName}</a> : job.pdfName}<small>{job.mode === 'real' ? 'Текст слайдов передан модели.' : 'Выбрана для демо, не анализировалась.'}</small></dd></>}{job.instructions && <><dt>Инструкции</dt><dd>{job.instructions}</dd></>}</dl></details>
}

function JobSteps({ job }: { job?: Job }) {
  const current = job ? STAGES.findIndex(stage => stage.status === job.status) : -1
  return <div className="context-block job-steps"><h2>Стадии задания</h2><p className="context-description">{job ? job.title : 'Нет выбранного задания'}</p><ol>{STAGES.map((stage, index) => {
    const done = job?.status === 'ready' || index < current
    const active = index === current
    return <li key={stage.status} className={`${done ? 'step-done' : ''} ${active ? 'step-active' : ''}`}><span className="step-icon">{done ? <Check size={12} /> : active ? <LoaderCircle className="spin" size={14} /> : <Circle size={8} />}</span><span>{stage.label}{active && <small>{job?.mode === 'real' ? 'Обработка' : 'Демо-стадия'}</small>}</span></li>
  })}<li className={job?.status === 'ready' ? 'step-done' : ''}><span className="step-icon">{job?.status === 'ready' ? <Check size={12} /> : <Circle size={8} />}</span><span>Готовый конспект</span></li></ol><p className="context-note">{job?.mode === 'real' ? `Whisper + ${job.provider === 'claude' ? 'Claude' : 'Codex'}` : 'Демо'} · по одному заданию</p></div>
}

function NotFound() { return <div className="quiet-empty"><BookOpen size={30} /><h1>Страница не найдена</h1><p>Проверьте выбранного пользователя и доступ к курсу.</p><Button asChild variant="outline"><Link to="/">В библиотеку<ArrowLeft size={15} /></Link></Button></div> }
