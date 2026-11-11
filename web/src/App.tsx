import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, NavLink, Route, Routes, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { ArrowLeft, ArrowRight, BookOpen, BookOpenText, Check, CheckCircle2, ChevronDown, ChevronRight, Circle, Clock3, FileText, Folder, FolderOpen, HelpCircle, House, Link2, ListOrdered, LoaderCircle, Paperclip, Plus, Search, Upload, Users, X, XCircle } from 'lucide-react'
import { Button } from './components/ui/button'
import { Dialog } from './components/ui/dialog'
import { TabBar } from './components/ui/tab-bar'
import { PageHeading } from './components/ui/page-heading'
import { ActivityRail, WorkspaceHeader, ReaderActions, ReaderInspector } from './components/workspace'
import { library, useLibrary } from './data/store'
import { demoState } from './data/demo'
import { isActive, LENGTH_LABELS, STAGES, STATUS_LABELS, validateUrl, type AppState, type Course, type Job, type JobInput, type Note, type Workspace } from './data/model'
import { formatDate, plural } from './lib/utils'
import { QuartzArticle, QuartzToc } from './components/reader'

const spaceLabel = (space: Workspace) => space === 'personal' ? 'Моя библиотека' : 'Общая библиотека'

export default function App() {
  const savedState = useLibrary()
  const location = useLocation()
  const navigate = useNavigate()
  const isDemo = location.pathname === '/demo' || location.pathname.startsWith('/demo/')
  const basePath = isDemo ? '/demo' : ''
  const [example, setExample] = useState(demoState)
  const state = isDemo ? example : savedState
  const [space, setSpace] = useState<Workspace>(() => localStorage.getItem('lecturevault.space') === 'shared' ? 'shared' : 'personal')
  const [search, setSearch] = useState('')
  const [courseDialog, setCourseDialog] = useState(false)
  const [createAndStart, setCreateAndStart] = useState(false)
  const [mobileNav, setMobileNav] = useState(false)
  const [help, setHelp] = useState(false)
  const [navCollapsed, setNavCollapsed] = useState(false)
  const [contextOpen, setContextOpen] = useState(true)
  const searchTimer = useRef<number | undefined>(undefined)
  const keepNavigationForSearch = useRef(false)
  const [courseTitle, setCourseTitle] = useState('')
  const [courseError, setCourseError] = useState('')
  const activeNote = state.notes.find(n => location.pathname === `${basePath}/notes/${n.id}`)
  const activeCourse = state.courses.find(c => location.pathname === `${basePath}/courses/${c.id}` || c.id === activeNote?.courseId)
  const isNew = location.pathname === '/new'
  const isQueue = location.pathname === '/queue'
  const courses = state.courses.filter(c => c.workspace === (isDemo ? 'personal' : space))
  const activeJobs = state.jobs.filter(j => isActive(j) || j.status === 'queued')
  const selectedJob = state.jobs.find(j => j.id === new URLSearchParams(location.search).get('job')) ?? state.jobs.find(isActive) ?? state.jobs.at(-1)
  const hasContext = Boolean((activeNote && contextOpen) || (isQueue && selectedJob))

  useEffect(() => {
    library.tick()
    const timer = window.setInterval(library.tick, 400)
    return () => window.clearInterval(timer)
  }, [])
  useEffect(() => {
    if (keepNavigationForSearch.current) keepNavigationForSearch.current = false
    else setMobileNav(false)
    window.scrollTo(0, 0)
  }, [location.pathname])
  useEffect(() => { setSearch('') }, [isDemo])
  useEffect(() => {
    if (activeCourse && !isDemo) { setSpace(activeCourse.workspace); localStorage.setItem('lecturevault.space', activeCourse.workspace) }
  }, [activeCourse, isDemo])
  useEffect(() => { document.title = `${activeNote?.title ?? (isNew ? 'Новый конспект' : isQueue ? 'Очередь заданий' : activeCourse?.title ?? 'Библиотека')} · LectureVault` }, [activeNote, activeCourse, isNew, isQueue])
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

  function openCourseDialog(start = false) { setCourseTitle(''); setCourseError(''); setCreateAndStart(start); setCourseDialog(true) }
  function startNew(courseId?: string) {
    setMobileNav(false)
    if (!courses.length && !courseId) { openCourseDialog(true); return }
    navigate(`/new?course=${courseId ?? activeCourse?.id ?? courses[0]?.id}`)
  }
  function switchSpace(value: Workspace) { setSpace(value); localStorage.setItem('lecturevault.space', value); setSearch(''); setMobileNav(false); navigate('/') }
  function toggleCourse(id: string) {
    if (isDemo) setExample(prev => ({ ...prev, expandedCourses: prev.expandedCourses.includes(id) ? prev.expandedCourses.filter(v => v !== id) : [...prev.expandedCourses, id] }))
    else library.toggleCourse(id)
  }
  function toggleTheme() {
    if (isDemo) setExample(prev => ({ ...prev, readerTheme: prev.readerTheme === 'dark' ? 'light' : 'dark' }))
    else library.setReaderTheme(state.readerTheme === 'dark' ? 'light' : 'dark')
  }
  function submitCourse(event: FormEvent) {
    event.preventDefault()
    if (!courseTitle.trim()) { setCourseError('Введите название курса.'); return }
    if (courses.some(c => c.title.toLocaleLowerCase() === courseTitle.trim().toLocaleLowerCase())) { setCourseError('Курс с таким названием уже есть в этом пространстве.'); return }
    const id = library.createCourse(courseTitle, space)
    setCourseDialog(false)
    navigate(createAndStart ? `/new?course=${id}` : `/courses/${id}`)
  }

  const sidebar = <>
    <Link className="brand" to={basePath || '/'} aria-label="LectureVault — главная" onClick={() => setSearch('')}><BookOpenText size={20} strokeWidth={1.6} /><span>LectureVault</span></Link>
    {isDemo ? <div className="example-space"><BookOpen size={15} />Пример библиотеки</div> : <div className="space-picker"><span aria-hidden="true">{space === 'personal' ? <House size={15} /> : <Users size={15} />}</span><select aria-label="Пространство" value={space} onChange={e => switchSpace(e.target.value as Workspace)}><option value="personal">Личное пространство</option><option value="shared">Общее пространство</option></select><ChevronDown size={13} className="select-chevron" /></div>}
    <label className="search-box"><Search size={15} /><input aria-label="Поиск по библиотеке" placeholder="Поиск" value={search} onChange={e => { setSearch(e.target.value); if (location.pathname !== (basePath || '/')) { keepNavigationForSearch.current = mobileNav; navigate(basePath || '/') } }} />{search && <button className="clear-search" aria-label="Очистить поиск" onClick={() => setSearch('')}><X size={14} /></button>}</label>
    <nav className="main-nav mobile-primary-nav" aria-label="Мобильная навигация"><NavLink to={basePath || '/'} end><BookOpen size={16} />Библиотека</NavLink>{!isDemo && <button className="mobile-new" onClick={() => startNew()}><Plus size={16} />Новый конспект</button>}{!isDemo && <NavLink to="/queue"><ListOrdered size={16} />Очередь заданий{activeJobs.length > 0 && <span className="nav-count">{activeJobs.length}</span>}</NavLink>}</nav>
    <div className="explorer-header"><span>Курсы</span>{!isDemo && <button className="icon-action" aria-label="Создать курс" title="Создать курс" onClick={() => openCourseDialog()}><Plus size={16} /></button>}</div>
    <nav className="explorer" aria-label="Курсы и конспекты">
      {!courses.length && <p className="tree-empty">Нет курсов</p>}
      {courses.filter(c => !search || c.title.toLowerCase().includes(search.toLowerCase()) || state.notes.some(n => n.courseId === c.id && n.title.toLowerCase().includes(search.toLowerCase()))).map(course => {
        const expanded = state.expandedCourses.includes(course.id) || Boolean(search)
        const notes = state.notes.filter(n => n.courseId === course.id && (!search || n.title.toLowerCase().includes(search.toLowerCase()) || course.title.toLowerCase().includes(search.toLowerCase())))
        return <div className="course-tree" key={course.id}><div className={`course-tree-row ${activeCourse?.id === course.id && !activeNote ? 'is-current' : ''}`}><button className="tree-toggle" aria-label={`${expanded ? 'Свернуть' : 'Развернуть'} курс ${course.title}`} aria-expanded={expanded} onClick={() => toggleCourse(course.id)}>{expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}</button><Link to={`${basePath}/courses/${course.id}`} className="course-tree-link" title={course.title}>{expanded ? <FolderOpen size={14} /> : <Folder size={14} />}<span>{course.title}</span></Link>{!isDemo && <button className="tree-add icon-action" aria-label={`Новый конспект в курсе ${course.title}`} onClick={() => startNew(course.id)}><Plus size={14} /></button>}</div>{expanded && <div className="tree-children">{!notes.length ? <span className="tree-empty-note">Нет конспектов</span> : notes.map(note => <NavLink key={note.id} to={`${basePath}/notes/${note.id}`} title={note.title}><FileText size={13} /><span>{note.title}</span></NavLink>)}</div>}</div>
      })}
    </nav>
    <div className="sidebar-bottom">{isDemo ? <Link className="help-link mobile-only" to="/"><ArrowLeft size={15} />Моя библиотека</Link> : <Link className="help-link" to="/demo"><BookOpenText size={15} />Демонстрационная библиотека</Link>}<button className="help-link mobile-only" onClick={() => { setMobileNav(false); setHelp(true) }}><HelpCircle size={15} />Как это работает</button><div className="local-status"><span className="status-dot" />{isDemo ? 'Примеры · без записи в библиотеку' : 'Данные в этом браузере'}</div></div>
  </>

  return <>
    <a href="#main-content" className="skip-link">К содержимому</a>
    <div className="page app-layout"><div id="quartz-body" className={`workspace-layout ${hasContext ? 'with-context' : ''} ${navCollapsed ? 'nav-collapsed' : ''}`}>
      <ActivityRail basePath={basePath} isDemo={isDemo} queueCount={activeJobs.length} section={isNew ? 'create' : isQueue ? 'queue' : 'library'} onCreate={() => startNew()} onSearch={focusSearch} onHelp={() => setHelp(true)} />
      <aside className="sidebar left desktop-sidebar">{sidebar}</aside>
      <main className={`center main-content ${activeNote ? `reader-page reader-${state.readerTheme}` : ''}`} id="main-content">
        <WorkspaceHeader collapsed={navCollapsed} onToggleNavigation={() => setNavCollapsed(!navCollapsed)} label={activeNote ? `${activeNote.title}.md` : isNew ? 'Новый конспект' : isQueue ? 'Очередь заданий' : activeCourse?.title ?? 'Библиотека'} mode={activeNote ? 'Чтение' : isDemo ? 'Примеры' : 'Локальная библиотека'} isDemo={isDemo} onOpenNavigation={() => setMobileNav(true)} onHelp={() => setHelp(true)} breadcrumbs={<><Link to={basePath || '/'}>{isDemo ? 'Пример библиотеки' : spaceLabel(space)}</Link>{activeCourse && <><ChevronRight size={12} />{activeNote ? <Link to={`${basePath}/courses/${activeCourse.id}`}>{activeCourse.title}</Link> : <span>{activeCourse.title}</span>}</>}{!activeCourse && (isNew || isQueue) && <><ChevronRight size={12} /><span>{isNew ? 'Новый конспект' : 'Очередь заданий'}</span></>}</>} actions={activeNote ? <ReaderActions note={activeNote} theme={state.readerTheme} onToggleTheme={toggleTheme} contextOpen={contextOpen} onToggleContext={() => setContextOpen(!contextOpen)} /> : undefined} />
        {!isDemo && library.getError() && <div className="storage-error" role="alert">{library.getError()}</div>}
        <div className="content-body">
          <Routes>
            <Route path="/" element={<LibraryPage state={state} courses={courses} search={search} createCourse={() => openCourseDialog()} startNew={startNew} />} />
            <Route path="/courses/:id" element={<CoursePage state={state} startNew={startNew} />} />
            <Route path="/new" element={<NewNotePage state={state} createCourse={() => openCourseDialog(true)} />} />
            <Route path="/queue" element={<QueuePage state={state} startNew={() => startNew()} />} />
            <Route path="/notes/:id" element={<ReaderPage state={state} />} />
            <Route path="/demo" element={<LibraryPage state={state} courses={courses} search={search} basePath="/demo" />} />
            <Route path="/demo/courses/:id" element={<CoursePage state={state} basePath="/demo" />} />
            <Route path="/demo/notes/:id" element={<ReaderPage state={state} basePath="/demo" />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </div>
      </main>
      {hasContext && <aside className="sidebar right context-sidebar">{activeNote ? <ReaderInspector key={activeNote.id} markdown={activeNote.markdown} source={<SourceDetails note={activeNote} state={state} />} /> : <JobSteps job={selectedJob} />}</aside>}
    </div></div>
    <Dialog open={mobileNav} onOpenChange={setMobileNav} title="Навигация" description="Пространства, курсы и конспекты" className="mobile-sidebar-dialog"><div className="mobile-sidebar-content">{sidebar}</div></Dialog>
    <Dialog open={courseDialog} onOpenChange={setCourseDialog} title={courses.length ? 'Новый курс' : 'Первый курс'} description={spaceLabel(space)}><form onSubmit={submitCourse} className="course-form"><label className="field-label" htmlFor="course-title">Название курса</label><input id="course-title" autoFocus maxLength={80} placeholder="Например, Распределённые системы" value={courseTitle} onChange={e => { setCourseTitle(e.target.value); setCourseError('') }} aria-invalid={!!courseError} aria-describedby={courseError ? 'course-error' : undefined} />{courseError && <p id="course-error" className="field-error" role="alert">{courseError}</p>}<div className="dialog-actions"><Button type="button" variant="ghost" onClick={() => setCourseDialog(false)}>Отмена</Button><Button type="submit">Создать курс</Button></div></form></Dialog>
    <Dialog open={help} onOpenChange={setHelp} title="Как это работает" description="Локальный прототип"><div className="help-content"><ol><li>Создайте курс.</li><li>Добавьте видео или ссылку и выберите настройки.</li><li>Дождитесь mock-задания в очереди.</li><li>Откройте конспект и скачайте Markdown.</li></ol><div className="demo-notice"><strong>Генерация демонстрационная</strong><p>Вместо анализа источника открывается учебный пример о репликации. Видео и PDF не читаются, ссылки не загружаются. Данные сохраняются только в этом браузере.</p></div><p>Демонстрационная библиотека открывается отдельно и не меняет ваши курсы. Удаление данных сайта в браузере удалит локальную библиотеку.</p><Button variant="outline" onClick={() => setHelp(false)}>Понятно</Button></div></Dialog>
  </>
}

function LibraryPage({ state, courses, search, createCourse, startNew, basePath = '' }: { state: AppState; courses: Course[]; search: string; createCourse?: () => void; startNew?: (id?: string) => void; basePath?: string }) {
  const notes = state.notes.filter(n => courses.some(c => c.id === n.courseId))
  const hits = notes.filter(n => n.title.toLowerCase().includes(search.toLowerCase()) || courses.find(c => c.id === n.courseId)?.title.toLowerCase().includes(search.toLowerCase()))
  if (search) return <><PageHeading title="Результаты поиска" description={`«${search}» · ${plural(hits.length, ['конспект', 'конспекта', 'конспектов'])}`} /><NoteTable notes={hits} courses={courses} basePath={basePath} />{!hits.length && <div className="quiet-empty"><p>Ничего не найдено. Попробуйте другое название.</p></div>}</>
  return <>
    <PageHeading title="Библиотека" description={courses.length ? `${plural(courses.length, ['курс', 'курса', 'курсов'])} · ${plural(notes.length, ['конспект', 'конспекта', 'конспектов'])}${basePath ? ' · демонстрационные материалы' : ''}` : undefined} action={courses.length > 0 && createCourse && <Button variant="outline" size="sm" onClick={createCourse}><Plus size={14} />Создать курс</Button>} />
    {!courses.length ? <div className="library-empty"><Folder size={20} strokeWidth={1.5} /><div><h2>Нет курсов</h2><p>Создайте курс, чтобы добавить видео или ссылку.</p><Button size="sm" onClick={createCourse}><Plus size={15} />Создать курс</Button></div></div> : <><div className="section-heading"><h2>Курсы</h2></div><div className="course-list">{courses.map(course => <div className="course-list-row" key={course.id}><Link to={`${basePath}/courses/${course.id}`} className="course-list-main"><Folder size={17} strokeWidth={1.5} /><strong>{course.title}</strong><span className="course-note-count">{plural(notes.filter(n => n.courseId === course.id).length, ['конспект', 'конспекта', 'конспектов'])}</span><ChevronRight size={14} className="row-chevron" /></Link>{startNew && <button className="icon-action" aria-label={`Добавить конспект в ${course.title}`} onClick={() => startNew(course.id)}><Plus size={16} /></button>}</div>)}</div>{notes.length > 0 && <><div className="section-heading recent-heading"><h2>Конспекты</h2><span>По дате добавления</span></div><NoteTable notes={notes.slice().sort((a,b) => b.createdAt - a.createdAt)} courses={courses} basePath={basePath} /></>}</>}
  </>
}

function NoteTable({ notes, courses, basePath = '' }: { notes: Note[]; courses?: Course[]; basePath?: string }) {
  return <div className={`note-list ${courses ? '' : 'without-course'}`} role="table" aria-label="Конспекты"><div className="note-table-head" role="row"><span role="columnheader">Название</span>{courses && <span role="columnheader">Курс</span>}<span role="columnheader">Добавлен</span></div>{notes.map(note => <Link className="note-list-row" role="row" key={note.id} to={`${basePath}/notes/${note.id}`}><span className="note-name" role="cell"><FileText size={16} strokeWidth={1.5} /><strong title={note.title}>{note.title}</strong></span>{courses && <span className="note-course" role="cell">{courses.find(c => c.id === note.courseId)?.title}</span>}<time role="cell" dateTime={new Date(note.createdAt).toISOString()}>{formatDate(note.createdAt)}</time></Link>)}</div>
}

function CoursePage({ state, startNew, basePath = '' }: { state: AppState; startNew?: (id?: string) => void; basePath?: string }) {
  const { id } = useParams()
  const course = state.courses.find(c => c.id === id)
  if (!course) return <NotFound />
  const notes = state.notes.filter(n => n.courseId === id)
  const pending = state.jobs.filter(j => j.courseId === id && (j.status === 'queued' || isActive(j)))
  return <><PageHeading title={course.title} description={`${plural(notes.length, ['конспект', 'конспекта', 'конспектов'])} · ${basePath ? 'Демонстрационные материалы' : spaceLabel(course.workspace)}`} action={startNew && <Button size="sm" onClick={() => startNew(course.id)}><Plus size={15} />Новый конспект</Button>} />{pending.length > 0 && <Link to="/queue" className="pending-notice"><LoaderCircle size={16} className="spin" />{plural(pending.length, ['задание', 'задания', 'заданий'])} в обработке<ArrowRight size={15} /></Link>}{notes.length ? <NoteTable notes={notes.slice().reverse()} basePath={basePath} /> : <div className="quiet-empty course-empty"><p>Нет конспектов. Добавьте видеофайл или ссылку.</p><Button variant="outline" size="sm" onClick={() => startNew?.(course.id)}>Добавить источник<Plus size={15} /></Button></div>}</>
}

type Draft = { courseId: string; title: string; sourceType: 'file' | 'url'; url: string; file?: { name: string; size: number }; pdfName?: string; preset: 'lecture' | 'seminar'; length: JobInput['length']; instructions: string }
const DRAFT_KEY = 'lecturevault.draft.v1'
function NewNotePage({ state, createCourse }: { state: AppState; createCourse: () => void }) {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const [draft, setDraft] = useState<Draft>(() => {
    let saved: Partial<Draft> = {}
    try { saved = JSON.parse(localStorage.getItem(DRAFT_KEY) || '{}') } catch { /* use empty draft */ }
    return { title: '', sourceType: 'file', url: '', preset: 'lecture', length: 'medium', instructions: '', ...saved, courseId: params.get('course') ?? saved.courseId ?? state.courses[0]?.id ?? '' }
  })
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [dragging, setDragging] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const pdfInput = useRef<HTMLInputElement>(null)
  const submitLock = useRef(false)
  useEffect(() => { try { localStorage.setItem(DRAFT_KEY, JSON.stringify(draft)) } catch { /* metadata can still be submitted */ } }, [draft])
  useEffect(() => {
    const courseId = params.get('course')
    if (courseId && state.courses.some(course => course.id === courseId)) {
      setDraft(previous => previous.courseId === courseId ? previous : { ...previous, courseId })
      setErrors(previous => previous.courseId ? { ...previous, courseId: '' } : previous)
    }
  }, [params, state.courses])
  const change = <K extends keyof Draft>(key: K, value: Draft[K]) => { setDraft(prev => ({ ...prev, [key]: value })); setErrors(prev => ({ ...prev, [key]: '' })) }
  function chooseFile(file?: File) {
    if (!file) return
    if (!/\.(mp4|mkv|mov|webm)$/i.test(file.name)) { setErrors(prev => ({ ...prev, source: 'Выберите видео MP4, MKV, MOV или WebM.' })); return }
    if (!file.size || file.size > 512 * 1024 * 1024) { setErrors(prev => ({ ...prev, source: 'Выберите непустое видео размером до 512 МБ.' })); return }
    setDraft(prev => ({ ...prev, file: { name: file.name, size: file.size }, title: prev.title || file.name.replace(/\.[^.]+$/, '').replace(/[_]/g, ' ') }))
    setErrors(prev => ({ ...prev, source: '' }))
  }
  function choosePdf(file?: File) {
    if (!file) return
    if (!/\.pdf$/i.test(file.name) || !file.size || file.size > 50 * 1024 * 1024) { setErrors(prev => ({ ...prev, pdf: 'Выберите непустую PDF-презентацию размером до 50 МБ.' })); return }
    change('pdfName', file.name); setErrors(prev => ({ ...prev, pdf: '' }))
  }
  function submit(e: FormEvent) {
    e.preventDefault()
    if (submitLock.current) return
    const next: Record<string, string> = {}
    if (!state.courses.some(c => c.id === draft.courseId)) next.courseId = 'Выберите курс для конспекта.'
    if (errors.pdf) next.pdf = errors.pdf
    if (!draft.title.trim()) next.title = 'Введите название конспекта.'
    if (draft.sourceType === 'file' && !draft.file) next.source = 'Добавьте видеофайл.'
    if (draft.sourceType === 'url') { const error = validateUrl(draft.url.trim()); if (error) next.source = error }
    setErrors(next)
    if (Object.keys(next).length) { document.querySelector<HTMLElement>(next.courseId ? '#note-course' : next.title ? '#note-title' : next.source ? draft.sourceType === 'url' ? '#source-url' : '.dropzone' : '.attach-button, .pdf-chip button')?.focus(); return }
    submitLock.current = true
    const id = library.createJob({ courseId: draft.courseId, title: draft.title.trim(), sourceType: draft.sourceType, sourceName: draft.sourceType === 'file' ? draft.file!.name : draft.url.trim(), sourceSize: draft.file?.size, pdfName: draft.pdfName, preset: draft.preset, length: draft.length, instructions: draft.instructions.trim(), timestamps: false })
    localStorage.removeItem(DRAFT_KEY)
    navigate(`/queue?job=${id}`)
  }
  if (!state.courses.length) return <><PageHeading title="Новый конспект" description="Сначала создайте курс." /><Button onClick={createCourse}><Plus size={16} />Создать курс</Button></>
  return <><PageHeading title="Новый конспект" /><form className="note-form" onSubmit={submit} noValidate>
    <div className="form-section"><div className="form-section-title"><span>01</span><h2>Источник</h2></div><TabBar label="Тип источника" panelId="source-panel" className="source-tabs" value={draft.sourceType} onValueChange={value => { change('sourceType', value); setErrors(previous => ({ ...previous, source: '' })) }} items={[{ value: 'file', label: <><Upload size={16} />Видеофайл</> }, { value: 'url', label: <><Link2 size={16} />Ссылка</> }]} />
      <div id="source-panel" role="tabpanel" aria-labelledby={`source-panel-${draft.sourceType}-tab`}>{draft.sourceType === 'file' ? <><input className="sr-only" type="file" id="video-file" aria-describedby={errors.source ? 'source-error' : undefined} ref={fileInput} accept=".mp4,.mkv,.mov,.webm" onChange={e => chooseFile(e.target.files?.[0])} />{draft.file ? <div className="selected-file"><div className="file-icon"><FileText size={23} /></div><span><strong title={draft.file.name}>{draft.file.name}</strong><small>{draft.file.size < 1024 * 1024 ? `${Math.max(1, Math.ceil(draft.file.size / 1024))} КБ` : `${(draft.file.size / 1024 / 1024).toFixed(1)} МБ`} · видео выбрано</small></span><button type="button" className="icon-action" aria-label="Убрать видео" onClick={() => { change('file', undefined); if (fileInput.current) fileInput.current.value = '' }}><X size={17} /></button></div> : <button type="button" className={`dropzone ${dragging ? 'dragging' : ''}`} aria-invalid={!!errors.source} aria-describedby={errors.source ? 'source-error' : undefined} onClick={() => fileInput.current?.click()} onDragOver={e => { e.preventDefault(); setDragging(true) }} onDragLeave={() => setDragging(false)} onDrop={e => { e.preventDefault(); setDragging(false); chooseFile(e.dataTransfer.files[0]) }}><span className="upload-icon"><Upload size={23} strokeWidth={1.4} /></span><strong>Перетащите видео сюда</strong><span>или <em>выберите файл</em> на устройстве</span><small>MP4, MKV, MOV, WebM · до 512 МБ</small></button>}</> : <div><label className="field-label" htmlFor="source-url">Ссылка на видео</label><div className="url-input"><Link2 size={17} /><input id="source-url" type="url" maxLength={2048} placeholder="https://www.youtube.com/watch?v=…" value={draft.url} onChange={e => { change('url', e.target.value); setErrors(prev => ({ ...prev, source: '' })) }} aria-invalid={!!errors.source} aria-describedby={errors.source ? 'source-error source-hint' : 'source-hint'} /></div><p className="field-hint" id="source-hint">В демо ссылка сохраняется как источник, видео не загружается.</p></div>}
      {errors.source && <p id="source-error" className="field-error" role="alert">{errors.source}</p>}</div>
      <input className="sr-only" type="file" ref={pdfInput} accept=".pdf" aria-label="PDF-презентация" aria-invalid={!!errors.pdf} aria-describedby={errors.pdf ? 'pdf-error' : undefined} onChange={e => choosePdf(e.target.files?.[0])} />{draft.pdfName ? <div className="pdf-chip"><Paperclip size={15} /><span title={draft.pdfName}>{draft.pdfName}</span><button type="button" className="icon-action" aria-label="Убрать PDF" onClick={() => { change('pdfName', undefined); setErrors(previous => ({ ...previous, pdf: '' })); if (pdfInput.current) pdfInput.current.value = '' }}><X size={14} /></button></div> : <button type="button" className="attach-button" onClick={() => pdfInput.current?.click()}><Paperclip size={15} />Добавить PDF-презентацию<span>необязательно</span></button>}{errors.pdf && <p id="pdf-error" className="field-error" role="alert">{errors.pdf}</p>}
    </div>
    <div className="form-section"><div className="form-section-title"><span>02</span><h2>Настройки конспекта</h2></div><div className="field-group"><label className="field-label" htmlFor="note-title">Название</label><input id="note-title" maxLength={120} placeholder="Например, Лекция 1. Введение в курс" value={draft.title} onChange={e => change('title', e.target.value)} aria-invalid={!!errors.title} aria-describedby={errors.title ? 'note-title-error' : undefined} />{errors.title && <p id="note-title-error" className="field-error" role="alert">{errors.title}</p>}</div><div className="field-group"><div className="field-label-row"><label className="field-label" htmlFor="note-course">Сохранить в курс</label><button type="button" className="inline-action" onClick={createCourse}>Создать курс</button></div><div className="course-select"><Folder size={16} /><select id="note-course" value={draft.courseId} aria-invalid={!!errors.courseId} aria-describedby={errors.courseId ? 'note-course-error' : undefined} onChange={e => { change('courseId', e.target.value); setParams({ course: e.target.value }, { replace: true }) }}>{state.courses.map(c => <option value={c.id} key={c.id}>{c.title}{c.workspace === 'shared' ? ' · общий' : ''}</option>)}</select><ChevronDown size={14} /></div>{errors.courseId && <p id="note-course-error" className="field-error" role="alert">{errors.courseId}</p>}</div>
      <fieldset><legend className="field-label">Формат материала</legend><div className="preset-grid"><button type="button" aria-pressed={draft.preset === 'lecture'} onClick={() => change('preset', 'lecture')}><BookOpen size={19} /><span><strong>Лекция</strong><small>Теория, понятия и выводы</small></span>{draft.preset === 'lecture' && <CheckCircle2 size={16} className="preset-check" />}</button><button type="button" aria-pressed={draft.preset === 'seminar'} onClick={() => change('preset', 'seminar')}><FileText size={19} /><span><strong>Семинар</strong><small>Задачи, примеры и решения</small></span>{draft.preset === 'seminar' && <CheckCircle2 size={16} className="preset-check" />}</button></div></fieldset>
      <fieldset><legend className="field-label">Объём</legend><div className="length-options">{(['short', 'medium', 'detailed'] as const).map(length => <button key={length} type="button" aria-pressed={draft.length === length} onClick={() => change('length', length)}>{LENGTH_LABELS[length]}</button>)}</div></fieldset>
      <div className="field-group"><label className="field-label" htmlFor="instructions">Инструкции к конспекту <span>необязательно</span></label><textarea id="instructions" maxLength={4000} rows={3} placeholder="На что обратить внимание? Какие термины и примеры сохранить?" value={draft.instructions} onChange={e => change('instructions', e.target.value)} /></div>
    </div><div className="form-submit"><p><span className="demo-dot" />Демо: создадим учебный пример.<br />Файлы останутся на вашем устройстве.</p><Button type="submit">Создать конспект<ArrowRight size={17} /></Button></div>
  </form></>
}

function Status({ job }: { job: Job }) {
  return <span className={`job-status status-${job.status}`}>{isActive(job) ? <LoaderCircle size={14} className="spin" /> : job.status === 'ready' ? <CheckCircle2 size={14} /> : job.status === 'failed' ? <XCircle size={14} /> : job.status === 'cancelled' ? <X size={13} /> : <Clock3 size={14} />}{STATUS_LABELS[job.status]}</span>
}

function QueuePage({ state, startNew }: { state: AppState; startNew: () => void }) {
  const [params, setParams] = useSearchParams()
  const [filter, setFilter] = useState<'all' | 'active' | 'ready'>('all')
  const jobs = state.jobs.filter(j => filter === 'all' || (filter === 'ready' ? j.status === 'ready' : j.status === 'queued' || isActive(j))).slice().reverse()
  const selectedJob = state.jobs.find(j => j.id === params.get('job')) ?? state.jobs.find(isActive) ?? state.jobs.at(-1)
  return <><PageHeading title="Очередь заданий" /><div className="queue-toolbar"><TabBar label="Фильтр заданий" panelId="queue-results" className="queue-tabs" value={filter} onValueChange={setFilter} items={(['all', 'active', 'ready'] as const).map(value => ({ value, label: <>{{ all: 'Все', active: 'В работе', ready: 'Готовые' }[value]}<span>{state.jobs.filter(j => value === 'all' || (value === 'ready' ? j.status === 'ready' : isActive(j) || j.status === 'queued')).length}</span></> }))} /><span className="queue-mode"><span className="demo-dot" />Демо</span></div><div aria-live="polite" className="sr-only">{selectedJob ? `${selectedJob.title}: ${STATUS_LABELS[selectedJob.status]}` : 'Очередь пуста'}</div>
    <div id="queue-results" role="tabpanel" aria-labelledby={`queue-results-${filter}-tab`}>{!jobs.length ? <div className="quiet-empty"><ListOrdered size={34} strokeWidth={1.3} /><h2>{state.jobs.length ? 'Здесь пока нет заданий' : 'Очередь пуста'}</h2><p>{state.jobs.length ? 'Выберите другой фильтр или создайте новый конспект.' : 'Добавьте видео или ссылку, чтобы создать задание.'}</p><Button variant="outline" onClick={startNew}>Новый конспект<Plus size={16} /></Button></div> : <div className="job-list">{jobs.map(job => <div className={`job-item ${job.id === selectedJob?.id ? 'selected' : ''}`} key={job.id}><div className={`job-file-icon ${job.status === 'ready' ? 'job-file-ready' : ''}`}>{job.status === 'ready' ? <FileText size={21} strokeWidth={1.4} /> : job.sourceType === 'url' ? <Link2 size={20} /> : <BookOpen size={21} strokeWidth={1.4} />}</div><div className="job-content"><button title={`${job.title} — источник: ${job.sourceName}`} className="job-title" onClick={() => setParams({ job: job.id })}>{job.title}</button><p>{state.courses.find(c => c.id === job.courseId)?.title} <span>·</span> {job.preset === 'lecture' ? 'Лекция' : 'Семинар'} <span>·</span> {LENGTH_LABELS[job.length]}</p><Status job={job} />{job.error && <p className="job-error">{job.error}</p>}</div><div className="job-actions">{job.status === 'ready' && <Button asChild size="sm" variant="outline"><Link to={`/notes/${job.noteId}`}>Открыть<ArrowRight size={14} /></Link></Button>}{job.status === 'queued' && <Button size="sm" variant="ghost" onClick={() => library.cancel(job.id)}>Отменить</Button>}{job.status === 'failed' && <Button size="sm" variant="outline" onClick={() => library.retry(job.id)}>Повторить</Button>}</div></div>)}</div>}
    </div><div className="queue-footnote"><Clock3 size={15} /><p>Задания выполняются по одному. Стадии демонстрационные — видео и презентации не анализируются.</p></div><div className="mobile-context"><JobSteps job={selectedJob} /></div>
  </>
}

function ReaderPage({ state, basePath = '' }: { state: AppState; basePath?: string }) {
  const { id } = useParams()
  const note = state.notes.find(n => n.id === id)
  const [mobileToc, setMobileToc] = useState(false)
  useEffect(() => { setMobileToc(false) }, [id])
  if (!note) return <NotFound />
  const job = state.jobs.find(j => j.id === note.jobId)
  return <><div className="reader-heading"><div className="reader-kicker">{job?.preset === 'seminar' ? 'СЕМИНАР' : 'ЛЕКЦИЯ'}</div><h1>{note.title}</h1><div className="reader-info"><div className="reader-meta"><span>{job?.preset === 'seminar' ? 'Семинар' : 'Лекция'}</span><span>·</span><time dateTime={new Date(note.createdAt).toISOString()}>{formatDate(note.createdAt)}</time><span>·</span><span>{basePath ? 'Демонстрационный материал' : 'Mock-результат'}</span></div></div></div>{!basePath && <p className="reader-demo">Учебный пример. Содержимое источника не анализировалось.</p>}<div className="mobile-toc"><button aria-expanded={mobileToc} onClick={() => setMobileToc(!mobileToc)}><ListOrdered size={16} />Оглавление<ChevronDown size={15} /></button>{mobileToc && <div onClick={event => { if ((event.target as HTMLElement).closest('a')) setMobileToc(false) }}><QuartzToc markdown={note.markdown} /></div>}</div><QuartzArticle markdown={note.markdown} /><div className="mobile-context"><SourceDetails note={note} state={state} /></div><Link className="back-link" to={`${basePath}/courses/${note.courseId}`}><ArrowLeft size={15} />К курсу</Link></>
}

function SourceDetails({ note, state }: { note: Note; state: AppState }) {
  const job = state.jobs.find(j => j.id === note.jobId)
  if (!job) return null
  return <details className="source-details" open><summary>Об источнике<ChevronDown size={13} /></summary><dl><dt>{job.sourceType === 'file' ? 'Видеофайл' : 'Ссылка'}</dt><dd title={job.sourceName}>{job.sourceName}</dd><dt>Параметры</dt><dd>{job.preset === 'lecture' ? 'Лекция' : 'Семинар'} · {LENGTH_LABELS[job.length]}</dd>{job.pdfName && <><dt>Презентация</dt><dd title={job.pdfName}>{job.pdfName}<small>Выбрана для демо, не анализировалась.</small></dd></>}{job.instructions && <><dt>Инструкции</dt><dd>{job.instructions}</dd></>}</dl></details>
}

function JobSteps({ job }: { job?: Job }) {
  const current = job ? STAGES.findIndex(stage => stage.status === job.status) : -1
  return <div className="context-block job-steps"><h2>Стадии задания</h2><p className="context-description">{job ? job.title : 'Нет выбранного задания'}</p><ol>{STAGES.map((stage, index) => {
    const done = job?.status === 'ready' || index < current
    const active = index === current
    return <li key={stage.status} className={`${done ? 'step-done' : ''} ${active ? 'step-active' : ''}`}><span className="step-icon">{done ? <Check size={12} /> : active ? <LoaderCircle className="spin" size={14} /> : <Circle size={8} />}</span><span>{stage.label}{active && <small>Демо-стадия</small>}</span></li>
  })}<li className={job?.status === 'ready' ? 'step-done' : ''}><span className="step-icon">{job?.status === 'ready' ? <Check size={12} /> : <Circle size={8} />}</span><span>Готовый конспект</span></li></ol><p className="context-note">Mock-обработка · по одному заданию</p></div>
}

function NotFound() { return <div className="quiet-empty"><BookOpen size={30} /><h1>Страница не найдена</h1><p>Возможно, она сохранена в другом браузере.</p><Button asChild variant="outline"><Link to="/">В библиотеку<ArrowLeft size={15} /></Link></Button></div> }
