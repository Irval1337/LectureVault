import { Fragment, lazy, Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { Link, NavLink, Route, Routes, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { ArrowLeft, ArrowRight, BookOpen, Check, CheckCircle2, ChevronDown, ChevronRight, Circle, Clock3, Download, FileInput, FileText, FileX, Folder, ListOrdered, LoaderCircle, PanelLeftClose, Plus, Search, Upload, X, XCircle } from 'lucide-react'
import { Button } from './components/ui/button'
import { Brand } from './components/brand'
import { openRowActions } from './components/ui/action-menu'
import { Dialog } from './components/ui/dialog'
import { TabBar } from './components/ui/tab-bar'
import { PageHeading } from './components/ui/page-heading'
import { ProgressMeter } from './components/ui/progress-meter'
import { UploadPopup, type UploadNotice } from './components/upload-popup'
import { ActivityRail, WorkspaceHeader, ReaderActions, ReaderInspector } from './components/workspace'
import { library, useLibrary, useSession } from './data/store'
import { CreateNotePage, type GenerationDraft } from './components/create-note'
import { CourseIcon, IconPicker, DirectoryRow, CategoryMenu, type CategoryAction, NoteActions, NoteBranches, CategoryOptions, childFolders, folderLink } from './components/organization'
import { CategoryDropArea, CategoryExplorer, NoteMoveProvider } from './components/note-move'
import { CourseMembers } from './components/course-members'
import { ProviderConnections } from './components/providers'
const NoteEditor = lazy(() => import('./components/editor'))
const MarkdownImportPage = lazy(() => import('./components/import-markdown'))
import { initializeTheme, ThemeButton } from './components/theme'
import { Invitation } from './components/invitation'
import { Presentation } from './components/presentation'
import { SettingsPage } from './components/settings'
import { canDeleteNote, slidePolicy, reuseMaterialsUrl, PROVIDER_NAMES, isActive, isQueued, LENGTH_LABELS, STAGES, STATUS_LABELS, type AppState, type Course, type Job, type Note } from './data/model'
import { formatDate, plural } from './lib/utils'
import { QuartzArticle, QuartzToc } from './components/reader'

import type { TocEntry } from './quartz/reader'

const emptyOutline: TocEntry[] = []

export default function App() {
  const savedState = useLibrary()
  const session = useSession()
  const generationDraft = useMemo<{ current: GenerationDraft }>(() => ({ current: {} }), [session.user?.id])
  const [uploadNotice, setUploadNotice] = useState<UploadNotice>()
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
  const [search, setSearch] = useState('')
  const [courseDialog, setCourseDialog] = useState(false)
  const [createAndStart, setCreateAndStart] = useState(false)
  const [mobileNav, setMobileNav] = useState(false)
  const [navCollapsed, setNavCollapsed] = useState(false)
  const [contextOpen, setContextOpen] = useState(true)
  const searchTimer = useRef<number | undefined>(undefined)
  const [courseTitle, setCourseTitle] = useState('')
  const [folderParent, setFolderParent] = useState<{root: Course; path: string} | null>(null)
  const [categoryChange, setCategoryChange] = useState<{root: Course; path: string; action: CategoryAction} | null>(null)
  const [categoryBusy, setCategoryBusy] = useState(false)
  const [courseError, setCourseError] = useState('')
  const activeNote = state.notes.find(n => location.pathname === `${basePath}/notes/${n.id}` || location.pathname === `${basePath}/notes/${n.id}/edit`)
  const isEditing = location.pathname.endsWith('/edit')
  const activeCourse = state.courses.find(c => location.pathname === `${basePath}/courses/${c.id}` || c.id === activeNote?.courseId)
  const activePath = activeNote?.category ?? new URLSearchParams(location.search).get('folder') ?? ''
  const isNew = location.pathname === '/new'
  const isImport = location.pathname === '/import'
  const isQueue = location.pathname === '/queue'
  const isSettings = location.pathname === '/settings'
  const courses = state.courses
  const activeJobs = state.jobs.filter(j => isActive(j) || isQueued(j))
  const noteId = activeNote?.id
  const noteMarkdown = activeNote?.markdown
  const [outline, setOutline] = useState<{ id: string; markdown: string; entries: TocEntry[] }>()
  const updateOutline = useCallback((entries: TocEntry[]) => {
    if (noteId && noteMarkdown !== undefined) setOutline({ id: noteId, markdown: noteMarkdown, entries })
  }, [noteId, noteMarkdown])
  const entries = outline && outline.id === noteId && outline.markdown === noteMarkdown ? outline.entries : emptyOutline
  const hasSource = Boolean(activeNote?.jobId && state.jobs.some(job => job.id === activeNote.jobId))
  const hasReaderContext = entries.length > 0 || hasSource
  const hasContext = activeNote && !isEditing && contextOpen && hasReaderContext


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
    if (!/^\/notes\/[^/]+$/.test(location.pathname)) window.scrollTo({ top: 0, behavior: 'instant' })
  }, [location.pathname])
  useLayoutEffect(() => {
    const previous = window.history.scrollRestoration
    window.history.scrollRestoration = 'manual'
    return () => { window.history.scrollRestoration = previous }
  }, [])
  useEffect(() => {
    const closeNavigation = () => setMobileNav(false)
    window.addEventListener('popstate', closeNavigation)
    return () => window.removeEventListener('popstate', closeNavigation)
  }, [])
  useEffect(() => {
    // Direct links to a document should reveal its location in the explorer.
    if (activeCourse && (activeNote || activePath) && !savedState.expandedCourses.includes(activeCourse.id)) library.toggleCourse(activeCourse.id)
  }, [activeNote?.id, activePath, activeCourse?.id])
  useEffect(() => { document.title = `${activeNote?.title ?? (isNew ? 'Сгенерировать конспект' : isImport ? 'Импорт Markdown' : isQueue ? 'Очередь заданий' : isSettings ? 'Настройки' : (activePath.split('/').at(-1) || activeCourse?.title) ?? 'Библиотека')} · LectureVault` }, [activeNote, activeCourse, activePath, isNew, isQueue, isSettings])
  useEffect(() => {
    if (!mobileNav) return
    const desktop = window.matchMedia('(min-width: 768px)')
    const closeOnDesktop = () => { if (desktop.matches) setMobileNav(false) }
    desktop.addEventListener('change', closeOnDesktop)
    closeOnDesktop()
    return () => desktop.removeEventListener('change', closeOnDesktop)
  }, [mobileNav])

  function toggleNavigator(collapsed: boolean) {
    setNavCollapsed(collapsed)
    requestAnimationFrame(() => document.querySelector<HTMLButtonElement>(collapsed ? '.expand-navigator' : '.desktop-sidebar .close-explorer')?.focus({ preventScroll: true }))
  }
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

  function openCourseDialog(start = false) { setFolderParent(null); setMobileNav(false); setCourseTitle(''); setCourseError(''); setCreateAndStart(start); setCourseDialog(true) }
  function addFolder(root: Course, path: string) {
    openCourseDialog()
    setFolderParent({ root, path })
  }
  function startNew(courseId?: string, path?: string) {
    setNewError('')
    const id = courseId ?? (activeCourse?.role !== 'viewer' ? activeCourse?.id : undefined) ?? courses.find(c => c.role !== 'viewer')?.id ?? ''
    const folder = path ?? (id === activeCourse?.id ? activePath : '')
    setNewCourseId(id + (folder ? '/' + folder : ''))
    setNewDialog(true)
  }
  async function chooseNew(intent: 'manual' | 'generate') {
    setNewIntent(intent); setNewError('')
    if (!newCourseId) { setNewDialog(false); openCourseDialog(true); return }
    const [root, ...parts] = newCourseId.split('/')
    const category = parts.join('/')
    setCreating(true)
    try {
      if (intent === 'manual') {
        const note = await library.createNote(root, category)
        setNewDialog(false); setMobileNav(false); navigate(`/notes/${note.id}/edit`)
      } else { setNewDialog(false); setMobileNav(false); navigate('/new?' + new URLSearchParams({course: root, folder: category})) }
    } catch (error) { setNewError((error as Error).message) }
    finally { setCreating(false) }
  }
  function openMarkdownImport() {
    const [course, ...folders] = newCourseId.split('/')
    setNewDialog(false)
    setMobileNav(false)
    navigate(course ? '/import?' + new URLSearchParams({ course, folder: folders.join('/') }) : '/import')
  }
  function toggleCourse(id: string) { library.toggleCourse(id) }
  useLayoutEffect(() => initializeTheme(state.readerTheme), [])
  async function submitCourse(event: FormEvent) {
    event.preventDefault()
    if (!courseTitle.trim()) { setCourseError('Введите название категории.'); return }
    if (categoryBusy) return
    if (!folderParent && courses.some(c => c.ownerId === session.user?.id && c.title.toLocaleLowerCase() === courseTitle.trim().toLocaleLowerCase())) {
      setCourseError('Категория с таким названием уже существует.')
      return
    }
    setCategoryBusy(true)
    try {
      const id = folderParent?.root.id ?? await library.createCourse(courseTitle, 'personal')
      const path = folderParent ? await library.createFolder(id, folderParent.path, courseTitle.trim()) : ''
      if (!library.getSnapshot().expandedCourses.includes(id)) library.toggleCourse(id)
      if (createAndStart && newIntent === 'manual') {
        try {
          const note = await library.createNote(id, path)
          navigate(`/notes/${note.id}/edit`)
        } catch (error) {
          setNewCourseId([id, path].filter(Boolean).join('/'))
          setNewError((error as Error).message)
          setCourseDialog(false)
          setNewDialog(true)
          return
        }
      } else if (createAndStart) {
        navigate('/new?' + new URLSearchParams({course: id, folder: path}))
      } else {
        navigate(folderLink(id,path))
      }
      setSearch('')
      setCourseDialog(false)
    } catch (error) { setCourseError((error as Error).message) }
    finally { setCategoryBusy(false) }
  }

  function manageCategory(root: Course, path: string, action: CategoryAction) {
    setMobileNav(false)
    setCourseError('')
    setCourseTitle(path.split('/').at(-1) || root.title)
    setCategoryChange({root,path,action})
  }
  async function submitCategoryChange(event: FormEvent) {
    event.preventDefault()
    if (!categoryChange || categoryBusy) return
    const {root,path,action} = categoryChange
    setCategoryBusy(true)
    try {
      if (action === 'delete') {
        await library.deleteCourse(root.id)
        if (activeCourse?.id === root.id) navigate('/', {replace:true})
      } else {
        const nextPath = await library.reorganizeCategory(root.id, path, action, courseTitle.trim())
        if (!activeNote && activeCourse?.id === root.id && (activePath === path || activePath.startsWith(path + '/'))) {
          navigate(folderLink(root.id, [nextPath, activePath.slice(path.length).replace(/^\//,'')].filter(Boolean).join('/')), {replace:true})
        }
      }
      setCategoryChange(null)
    } catch (error) { setCourseError((error as Error).message) }
    finally { setCategoryBusy(false) }
  }

  const sidebar = <>
    <div className="navigator-heading"><Link className="brand" to={basePath || '/'} aria-label="LectureVault — главная" onClick={() => setSearch('')}><Brand/></Link><button className="icon-action close-explorer" aria-label="Скрыть проводник" title="Скрыть проводник" aria-controls="course-navigator" aria-expanded="true" onClick={() => toggleNavigator(true)}><PanelLeftClose size={16}/></button></div>
    <nav className="main-nav library-nav" aria-label="Разделы библиотеки"><NavLink to={basePath || '/'} end><BookOpen size={16} />Библиотека</NavLink>{<button className="mobile-new" onClick={() => startNew()}><Plus size={16} />Добавить заметку</button>}{<NavLink to="/queue"><ListOrdered size={16} />Очередь заданий{activeJobs.length > 0 && <span className="nav-count">{activeJobs.length}</span>}</NavLink>}</nav>
    <label className="search-box"><Search size={15} /><input aria-label="Поиск по библиотеке" placeholder="Найти категорию или заметку" value={search} onChange={e => { setSearch(e.target.value); if (location.pathname !== (basePath || '/')) { navigate(basePath || '/') } }} />{search && <button className="clear-search" aria-label="Очистить поиск" onClick={() => setSearch('')}><X size={14} /></button>}</label>
    <div className="explorer-header"><span>Категории</span>{<button className="icon-action" aria-label="Создать категорию" title="Создать категорию" onClick={() => openCourseDialog()}><Plus size={16} /></button>}</div>
    <CategoryExplorer>
      {courses.filter(c => !search || c.title.toLowerCase().includes(search.toLowerCase()) || c.folders?.some(p => p.toLowerCase().includes(search.toLowerCase())) || state.notes.some(n => n.courseId === c.id && `${n.title} ${n.category || ''}`.toLowerCase().includes(search.toLowerCase()))).map(course => {
        const expanded = state.expandedCourses.includes(course.id) || Boolean(search)
        const notes = state.notes.filter(n => n.courseId === course.id && (!search || `${n.title} ${n.category || ''}`.toLowerCase().includes(search.toLowerCase()) || course.title.toLowerCase().includes(search.toLowerCase())))
        return <div className="course-tree" key={course.id}><CategoryDropArea course={course} onExpand={() => { if (!expanded) toggleCourse(course.id) }} onContextMenu={event => { event.preventDefault(); openRowActions(event.currentTarget, {x:event.clientX, y:event.clientY}) }} onKeyDown={event => { if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) { event.preventDefault(); openRowActions(event.currentTarget) } }} className={`course-tree-row ${activeCourse?.id === course.id && !activeNote && !activePath ? 'is-current' : ''}`}><button className="tree-toggle" aria-label={`${expanded ? 'Свернуть' : 'Развернуть'} категорию ${course.title}`} aria-expanded={expanded} onClick={() => toggleCourse(course.id)}>{expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}</button><Link draggable={false} to={`${basePath}/courses/${course.id}`} className="course-tree-link" title={course.title}><CourseIcon course={course} open={expanded}/><span>{course.title}</span></Link><CategoryMenu course={course} manage={manageCategory} label={`Действия: ${course.title}`}><Button size="sm" variant="ghost" onClick={() => { setMobileNav(false); navigate(`/courses/${course.id}`) }}>Открыть категорию</Button>{course.role !== 'viewer' && <Button size="sm" variant="ghost" onClick={() => startNew(course.id,'')}>Добавить заметку</Button>}{course.role !== 'viewer' && <Button size="sm" variant="ghost" onClick={() => addFolder(course,'')}>Создать подкатегорию</Button>}</CategoryMenu></CategoryDropArea>{expanded && <div className="tree-children"><NoteBranches notes={notes} course={course} search={search} selected={activeCourse?.id === course.id ? activePath : ''} manage={manageCategory} addFolder={addFolder} startNew={startNew}/></div>}</div>
      })}
    </CategoryExplorer>
    <div className="sidebar-bottom">{<><div className="account-tools"><ProviderConnections account /><ThemeButton /></div>{library.hasLegacy() && !imported && <button className="help-link" onClick={() => { void library.importLocal().then(() => setImported(true)).catch(library.reportError) }}>Импортировать локальные категории и заметки</button>}{imported && <p className="field-hint" role="status">Импортировано. Исходная копия в браузере сохранена.</p>}</>}</div>
  </>

  return <NoteMoveProvider courses={courses}>
    <a href="#main-content" className="skip-link">К содержимому</a>
    <div className="page app-layout"><div id="quartz-body" className={`workspace-layout ${hasContext ? 'with-context' : ''} ${navCollapsed ? 'nav-collapsed' : ''}`}>
      <ActivityRail basePath={basePath} queueCount={activeJobs.length} section={isNew || isImport ? 'create' : isQueue ? 'queue' : isSettings ? 'settings' : 'library'} onCreate={() => startNew()} onSearch={focusSearch} onExpand={() => toggleNavigator(false)} />
      <aside id="course-navigator" className="sidebar left desktop-sidebar">{sidebar}</aside>
      <main className={`center main-content ${activeNote ? 'reader-page' : activeCourse ? 'category-page' : 'section-page'}`} id="main-content">
        <WorkspaceHeader label={activeNote ? `${activeNote.title}.md` : isNew ? 'Сгенерировать конспект' : isImport ? 'Импорт Markdown' : isQueue ? 'Очередь заданий' : isSettings ? 'Настройки' : (activePath.split('/').at(-1) || activeCourse?.title) ?? 'Библиотека'} mode={activeNote ? isEditing ? 'Редактирование' : 'Чтение' : ''} onOpenNavigation={() => setMobileNav(true)} breadcrumbs={isQueue || isSettings ? <span className="global-location">LectureVault</span> : <><Link to={basePath || '/'}>Библиотека</Link>{activeCourse && <><ChevronRight size={12} />{activeNote || activePath ? <Link to={folderLink(activeCourse.id)}>{activeCourse.title}</Link> : <span aria-current="page" title={activeCourse.title}>{activeCourse.title}</span>}
          {activePath.split('/').filter(Boolean).map((name,i,parts) => <Fragment key={i}><ChevronRight size={12}/>{!activeNote && i === parts.length - 1 ? <span aria-current="page" title={name}>{name}</span> : <Link title={name} to={folderLink(activeCourse.id,parts.slice(0,i+1).join('/'))}>{name}</Link>}</Fragment>)}</>}{!activeCourse && (isNew || isImport || isQueue || isSettings) && <><ChevronRight size={12} /><span>{isNew ? 'Новый конспект' : isImport ? 'Импорт Markdown' : isSettings ? 'Настройки' : 'Очередь заданий'}</span></>}</>} actions={activeNote && !isEditing ? <ReaderActions note={activeNote} contextAvailable={hasReaderContext} onEdit={activeCourse?.role !== 'viewer' ? () => navigate(`/notes/${activeNote.id}/edit`) : undefined} onDelete={canDeleteNote(activeCourse, activeNote, session.user?.id) ? () => { if (window.confirm('Удалить эту заметку? Это действие нельзя отменить.')) void library.deleteNote(activeNote.id).then(() => navigate(folderLink(activeNote.courseId, activeNote.category))).catch(library.reportError) } : undefined} contextOpen={contextOpen} onToggleContext={() => setContextOpen(!contextOpen)} /> : undefined} />
        {library.getError() && <div className="storage-error" role="alert">{library.getError()}</div>}
        <div className="content-body">
          <Routes>
            <Route path="/invite" element={<Invitation />} />
            <Route path="/" element={<LibraryPage manage={manageCategory} addFolder={addFolder} state={state} courses={courses} search={search} createCourse={() => openCourseDialog()} startNew={startNew} />} />
            <Route path="/courses/:id" element={<CoursePage manage={manageCategory} state={state} startNew={startNew} addFolder={addFolder} />} />
            <Route path="/new" element={<CreateNotePage draftRef={generationDraft} notifyUpload={setUploadNotice} state={state} createCourse={() => { setNewIntent('generate'); openCourseDialog(true) }} />} />
            <Route path="/import" element={<Suspense fallback={<p role="status">Открываем импорт…</p>}><MarkdownImportPage state={state}/></Suspense>} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/queue" element={<QueuePage state={state} startNew={() => navigate('/new')} />} />
            <Route path="/notes/:id/edit" element={activeNote && activeNote.contentLoaded === false ? <p role="status">Открываем заметку…</p> : activeNote ? <Suspense fallback={<p role="status">Открываем редактор…</p>}><NoteEditor key={activeNote.id} note={activeNote} canEdit={activeCourse?.role !== 'viewer'} ownsCourse={activeCourse?.role === 'owner'} /></Suspense> : <NotFound />} />
            <Route path="/notes/:id" element={<ReaderPage state={state} entries={entries} onOutline={updateOutline} />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </div>
      </main>
      {hasContext && <aside className="sidebar right context-sidebar"><ReaderInspector key={activeNote.id} entries={entries} source={hasSource ? <SourceDetails note={activeNote} state={state} /> : null} /></aside>}
    </div></div>
    {uploadNotice && <UploadPopup notice={uploadNotice} dismiss={() => setUploadNotice(undefined)}/>}
    <Dialog open={mobileNav} onOpenChange={setMobileNav} title="Навигация" description="" className="mobile-sidebar-dialog"><div className="mobile-sidebar-content" onClick={event => { if ((event.target as HTMLElement).closest('a[href]')) setMobileNav(false) }}>{sidebar}</div></Dialog>
    <Dialog open={newDialog} onOpenChange={setNewDialog} title="Добавить заметку" description=""><div className="new-menu">{courses.some(c => c.role !== 'viewer') && <><label className="field-label" htmlFor="new-course">Категория</label><select id="new-course" value={newCourseId} onChange={e => setNewCourseId(e.target.value)}><CategoryOptions courses={courses.filter(c => c.role !== 'viewer')}/></select></>}<Button variant="outline" disabled={creating} onClick={() => void chooseNew('manual')}><FileText size={17} />Создать заметку</Button><Button variant="outline" disabled={creating} onClick={() => void chooseNew('generate')}><Upload size={17} />Сгенерировать конспект</Button><Button variant="outline" disabled={creating} onClick={openMarkdownImport}><FileInput size={17}/>Импортировать Markdown</Button>{!courses.some(c => c.role !== 'viewer') && <p className="field-hint">Для первой заметки понадобится категория.</p>}{newError && <p className="field-error" role="alert">{newError}</p>}</div></Dialog>
    <Dialog open={courseDialog} onOpenChange={open => { if (!categoryBusy) setCourseDialog(open) }} title={folderParent ? 'Новая подкатегория' : 'Новая категория'} description="">
      <form onSubmit={submitCourse} className="course-form">
        <label className="field-label" htmlFor="course-title">Название категории</label>
        <input id="course-title" autoFocus maxLength={folderParent ? 60 : 80} placeholder="Название" value={courseTitle} onChange={e => {setCourseTitle(e.target.value); setCourseError('')}} aria-invalid={!!courseError}/>
        {courses.some(c => c.role !== 'viewer') && <>
          <label className="field-label" htmlFor="category-parent">Родительская категория</label>
          <select id="category-parent" value={folderParent ? [folderParent.root.id,folderParent.path].filter(Boolean).join('/') : ''} onChange={e => {
            const [id,...parts] = e.target.value.split('/')
            const root = courses.find(c => c.id === id)
            setFolderParent(root ? {root,path:parts.join('/')} : null)
            setCourseError('')
          }}><option value="">Без родителя — в библиотеке</option><CategoryOptions courses={courses.filter(c => c.role !== 'viewer')} parentsOnly/></select>
        </>}
        <p className="field-hint">{folderParent ? 'Доступ наследуется от родительской категории.' : 'Категория доступна только вам. Пригласить участников можно после создания.'}</p>
        {courseError && <p className="field-error" role="alert">{courseError}</p>}
        <div className="dialog-actions"><Button type="button" variant="ghost" disabled={categoryBusy} onClick={() => setCourseDialog(false)}>Отмена</Button><Button type="submit" disabled={categoryBusy}>{folderParent ? 'Создать подкатегорию' : 'Создать категорию'}</Button></div>
      </form>
    </Dialog>
    <Dialog open={!!categoryChange} onOpenChange={open => { if (!open && !categoryBusy) setCategoryChange(null) }}
      className="category-change-dialog" title={categoryChange?.action === 'rename' ? 'Переименовать категорию' : categoryChange?.action === 'delete' ? 'Удалить категорию?' : 'Объединить с родителем?'} description="">
      {categoryChange && <form className="course-form" onSubmit={submitCategoryChange}>
        {categoryChange.action === 'rename' ? <>
          <label className="field-label" htmlFor="rename-folder">Название категории</label>
          <input id="rename-folder" autoFocus value={courseTitle} maxLength={categoryChange.path ? 60 : 80}
            onChange={e => {setCourseTitle(e.target.value); setCourseError('')}} required/>
        </> : <p>{categoryChange.action === 'delete'
          ? <>Категория <strong>«{categoryChange.root.title}»</strong>, её заметки, подкатегории и материалы будут удалены.</>
          : <>Заметки и подкатегории из <strong>«{categoryChange.path.split('/').at(-1)}»</strong> перейдут в <strong>«{categoryChange.path.split('/').at(-2) || categoryChange.root.title}»</strong>. Пустая папка исчезнет.</>}</p>}
        {courseError && <p className="field-error" role="alert">{courseError}</p>}
        <div className="dialog-actions"><Button variant="ghost" type="button" disabled={categoryBusy} onClick={() => setCategoryChange(null)}>Отмена</Button><Button disabled={categoryBusy || (categoryChange.action === 'rename' && !courseTitle.trim())} type="submit">{categoryChange.action === 'rename' ? 'Сохранить' : categoryChange.action === 'delete' ? 'Удалить категорию' : 'Объединить'}</Button></div>
      </form>}
    </Dialog>
  </NoteMoveProvider>
}

function LibraryPage({ state, courses, search, createCourse, startNew, addFolder, manage, basePath = '' }: { state: AppState; courses: Course[]; search: string; createCourse: () => void; startNew: (id?: string, path?: string) => void; addFolder: (root: Course,path: string) => void; manage: (root: Course,path: string,action: CategoryAction) => void; basePath?: string }) {
  const [params] = useSearchParams()
  const allNotes = params.get('view') === 'recent'
  const notes = state.notes
  const categoryCount = courses.reduce((count, root) => count + 1 + (root.folders?.length ?? 0), 0)
  if (search) {
    const query = search.toLocaleLowerCase()
    const categories = courses.flatMap(root => ['', ...(root.folders ?? [])]
      .filter(path => (path || root.title).toLocaleLowerCase().includes(query))
      .map(path => ({ root, path })))
    const hits = notes.filter(note => [note.title, note.category, courses.find(root => root.id === note.courseId)?.title]
      .join(' ').toLocaleLowerCase().includes(query))
    return <div className="library-sheet">
      <PageHeading title="Результаты поиска" description={`«${search}»`}/>
      <div className="library-index">
        {categories.length > 0 && <section><div className="section-heading"><h2>Категории</h2></div>
          {categories.map(({root, path}) => <CategoryDropArea asChild course={root} path={path} key={root.id + '/' + path}><Link className="category-result" to={folderLink(root.id,path)}>
            <span className="folder-symbol"><CourseIcon course={root} path={path}/></span>
            {[root.title, ...path.split('/').filter(Boolean)].join(' › ')}
          </Link></CategoryDropArea>)}
        </section>}
        {hits.length > 0 && <section><div className="section-heading"><h2>Заметки</h2></div><NoteTable notes={hits} courses={courses} basePath={basePath}/></section>}
        {!categories.length && !hits.length && <p className="field-hint">Ничего не найдено. Попробуйте другое название.</p>}
      </div>
    </div>
  }
  const recent = [...notes].sort((a,b) => (b.updatedAt ?? b.createdAt) - (a.updatedAt ?? a.createdAt))
  if (allNotes) return <div className="library-sheet">
    <Link className="library-back" to={basePath || '/'}><ArrowLeft size={15}/>Библиотека</Link>
    <PageHeading title="Все заметки"/>
    {recent.length ? <NoteTable notes={recent} courses={courses} basePath={basePath}/> : <p className="field-hint">Заметок пока нет.</p>}
  </div>
  return <div className="library-sheet">
    <PageHeading title="Библиотека" description={courses.length ? `${plural(categoryCount, ['категория', 'категории', 'категорий'])} · ${plural(notes.length, ['заметка', 'заметки', 'заметок'])}` : undefined} action={courses.length > 0 && <div className="page-actions"><Button variant="outline" size="sm" onClick={createCourse}>Создать категорию</Button>{courses.some(c => c.role !== 'viewer') && <Button size="sm" onClick={() => startNew()}><Plus size={14}/>Добавить заметку</Button>}</div>} />
    {!courses.length ? <div className="library-empty"><span className="folder-symbol"><Folder size={22}/></span><div><h2>Нет категорий</h2><div className="page-actions"><Button size="sm" onClick={createCourse}><Plus size={15} />Создать категорию</Button><Button asChild variant="outline" size="sm"><Link to="/import"><FileInput size={15}/>Импортировать Markdown</Link></Button></div></div></div> : <div className="library-index">
      <section className="library-courses" aria-labelledby="catalog-heading">
        <div className="section-heading"><h2 id="catalog-heading">Категории</h2></div>
        <div className="course-list">{courses.map(root => {
          const materials = notes.filter(n => n.courseId === root.id)
          const children = childFolders(root, '')
          return <DirectoryRow key={root.id} course={root} count={materials.length} manage={manage} addFolder={addFolder} startNew={startNew}>
            {children.length > 0 && <div className="directory-children">{children.slice(0,3).map(path =>
              <CategoryDropArea asChild course={root} path={path} key={path}><Link to={folderLink(root.id,path)}><CourseIcon course={root} path={path}/>{path}</Link></CategoryDropArea>)}
              {children.length > 3 && <Link to={folderLink(root.id)}>Ещё {children.length-3}</Link>}
            </div>}
          </DirectoryRow>
        })}</div>
      </section>
      {recent.length > 0 && <section className="library-recent" aria-labelledby="recent-heading">
        <div className="section-heading"><h2 id="recent-heading">Недавние заметки</h2><Link className="library-more" to="?view=recent">Все заметки<ArrowRight size={14}/></Link></div>
        <NoteTable notes={recent.slice(0,6)} courses={courses} basePath={basePath}/>
      </section>}
    </div>}
  </div>
}

function NoteTable({ notes, courses, course, basePath = '' }: { notes: Note[]; courses?: Course[]; course?: Course; basePath?: string }) {
  return <div className={`note-list ${courses ? '' : 'without-course'}`} role="table" aria-label="Конспекты">
    <div className="note-table-head" role="row"><span role="columnheader">Название</span>{courses && <span role="columnheader">Категория</span>}<span role="columnheader">Изменено</span><span className="sr-only" role="columnheader">Действия</span></div>
    {notes.map(note => {
      const parent = course ?? courses?.find(item => item.id === note.courseId)
      if (!parent) return null
      const changed = note.updatedAt ?? note.createdAt
      return <NoteActions key={note.id} tableRow note={note} course={parent}>
        <span role="cell"><Link draggable={false} className="note-name" aria-label={note.title} to={`${basePath}/notes/${note.id}`}>
          <FileText size={16}/><span><strong title={note.title}>{note.title}</strong>{courses && note.category && <small>{note.category.split('/').join(' / ')}</small>}</span>
        </Link></span>
        {courses && <span className="note-course" role="cell">{parent.title}</span>}
        <time role="cell" dateTime={new Date(changed).toISOString()}>{formatDate(changed)}</time>
      </NoteActions>
    })}
  </div>
}

function CoursePage({ state, startNew, addFolder, manage }: {
  state: AppState; manage: (root: Course,path: string,action: CategoryAction) => void; startNew: (id: string,path: string) => void; addFolder: (course: Course,path: string) => void
}) {
  const { id } = useParams()
  const [params] = useSearchParams()
  const course = state.courses.find(c => c.id === id)
  const path = params.get('folder') || ''
  if (!course || (path && !course.folders?.includes(path))) return <NotFound/>
  const folders = childFolders(course, path)
  const notes = state.notes.filter(n => n.courseId === id && (n.category || '') === path)
  const pending = state.jobs.filter(j => j.courseId === id && (j.category || '') === path && (isQueued(j) || isActive(j)))
  const writable = course.role !== 'viewer'
  const title = path.split('/').at(-1) || course.title
  return <div className="library-sheet">
    <CategoryDropArea asChild dropOnly course={course} path={path}><header className="category-heading">
      <div className="category-identity">
        <span className="folder-symbol"><CourseIcon course={course} path={path}/></span>
        <div><h1>{title}</h1>{course.role === 'owner' && <IconPicker course={course} path={path}/>}</div>
      </div>
      <div className="category-tools">
        {writable && <><Button size="sm" onClick={() => startNew(course.id,path)}><Plus size={15}/>Добавить заметку</Button>
          {path.split('/').length < 6 && <Button variant="outline" size="sm" onClick={() => addFolder(course,path)}>Создать подкатегорию</Button>}</>}
        <CourseMembers key={course.id} course={course} inherited={!!path}/>
        {course.role === 'owner' && !path && <Button asChild size="sm" variant="ghost"><Link to={'/settings?course=' + course.id + '#storage'}>Хранилище</Link></Button>}
        {course.role === 'owner' && <CategoryMenu label="Действия категории" course={course} path={path} manage={manage}/>}
      </div>
    </header></CategoryDropArea>
    {pending.length > 0 && <Link to="/queue" className="pending-notice"><LoaderCircle size={16} className="spin"/>{plural(pending.length,['задание','задания','заданий'])} в работе<ArrowRight size={15}/></Link>}
    <div className="library-index">
      {folders.length > 0 && <section><div className="section-heading"><h2>Подкатегории</h2></div>
        {folders.map(child => <DirectoryRow key={child} course={course} path={child} startNew={startNew} addFolder={addFolder} manage={manage}
          count={state.notes.filter(n => n.courseId === id && (n.category === child || n.category?.startsWith(child + '/'))).length}/>)}
      </section>}
      {notes.length > 0 && <section><div className="section-heading"><h2>Заметки</h2></div><NoteTable course={course} notes={notes.slice().reverse()}/></section>}
      {!folders.length && !notes.length && <p className="category-empty">Категория пока пуста.</p>}
    </div>
  </div>
}

function Status({ job }: { job: Job }) {
  if (job.status === 'ready' && !job.noteId) return <span className="job-status status-note-removed"><FileX size={14} />Конспект удалён</span>
  return <span className={`job-status status-${job.status}`}>{isActive(job) ? <LoaderCircle size={14} className="spin" /> : job.status === 'ready' ? <CheckCircle2 size={14} /> : job.status === 'failed' ? <XCircle size={14} /> : job.status === 'cancelled' ? <X size={13} /> : <Clock3 size={14} />}{STATUS_LABELS[job.status]}</span>
}

type QueueFilter = 'all' | 'active' | 'ready'
function queueView(all: Job[], params: URLSearchParams) {
  const raw = params.get('filter')
  const filter: QueueFilter = raw === 'active' || raw === 'ready' ? raw : 'all'
  const jobs = all.filter(j => filter === 'all' || (filter === 'ready' ? j.status === 'ready' && !!j.noteId : isQueued(j) || isActive(j))).slice().reverse()
  const selectedJob = jobs.find(j => j.id === params.get('job')) ?? jobs.find(isActive) ?? jobs[0]
  return { filter, jobs, selectedJob }
}

function QueuePage({ state, startNew }: { state: AppState; startNew: () => void }) {
  const [params, setParams] = useSearchParams()
  const [overrides, setOverrides] = useState<Record<string, boolean>>({})
  const session = useSession()
  const { filter, jobs, selectedJob } = queueView(state.jobs, params)
  const setFilter = (value: QueueFilter) => setParams(value === 'all' ? {} : { filter: value })
  return <><PageHeading title="Очередь заданий" action={<Button asChild variant="ghost" size="sm"><Link to="/settings#storage">Хранилище</Link></Button>} />
    <div className="queue-toolbar"><TabBar label="Фильтр заданий" panelId="queue-results" className="queue-tabs" value={filter} onValueChange={setFilter}
      items={(['all', 'active', 'ready'] as const).map(value => ({value, label: <>{{all:'Все',active:'В работе',ready:'Готовые'}[value]}<span>{queueView(state.jobs, new URLSearchParams({filter:value})).jobs.length}</span></>}))}/></div>
    <div id="queue-results" role="tabpanel" aria-labelledby={`queue-results-${filter}-tab`}>
      {!jobs.length && <div className="quiet-empty"><h2>{state.jobs.length ? 'Заданий с этим статусом пока нет' : 'Очередь пуста'}</h2><Button variant="outline" onClick={startNew}>Новый конспект<Plus size={16}/></Button></div>}
      <div className="job-list">{jobs.map(job => {
        const expanded = overrides[job.id] ?? (isActive(job) || job.id === selectedJob?.id)
        const toggle = () => setOverrides(current => ({...current, [job.id]: !expanded}))
        const ownsJob = job.createdBy === session.user?.id
        const canWrite = state.courses.some(course => course.id === job.courseId && course.role !== 'viewer')
        const canRetry = job.status === 'failed' && job.canRetry && ownsJob && canWrite
        const canReuse = canWrite && !job.noteId && job.transcriptAssetId && !isActive(job) && !isQueued(job) && !canRetry
        const blockingCliJob = job.status === 'waiting_generation' && state.jobs.find(other =>
          other.id !== job.id && other.createdBy === job.createdBy && other.mode === 'real'
          && (other.status === 'preparing' || other.status === 'generating')
          && (job.provider !== 'openai' || other.provider !== 'openai'))
        return <Fragment key={job.id}><div className={`job-item ${expanded ? 'selected' : ''}`}>
          <div className="job-content">
            <button className="job-title" aria-expanded={expanded} aria-controls={'stages-' + job.id} onClick={toggle}><ChevronRight size={15}/>{job.title}</button>
            <p>{[state.courses.find(c => c.id === job.courseId)?.title, job.category?.split('/').join(' › '), job.mode === 'real' ? [PROVIDER_NAMES[job.provider ?? 'codex'],job.model].filter(Boolean).join(' · ') : 'Демо'].filter(Boolean).join(' · ')}</p>
            <Status job={job}/>{job.error && <p className="job-error">{job.error}</p>}
            {blockingCliJob && <p>{PROVIDER_NAMES[blockingCliJob.provider ?? 'codex']} занят другим заданием этого аккаунта.</p>}
          </div>
          <div className="job-actions">
            {job.status === 'ready' && job.noteId && state.notes.some(n => n.id === job.noteId) && <Button asChild size="sm" variant="outline"><Link to={`/notes/${job.noteId}`}>Открыть<ArrowRight size={14}/></Link></Button>}
            {isQueued(job) && ownsJob && <Button size="sm" variant="ghost" onClick={() => void library.cancel(job.id).catch(library.reportError)}>Отменить</Button>}
            {canRetry && <Button size="sm" variant="outline" onClick={() => void library.retry(job.id).catch(library.reportError)}>Повторить</Button>}
            {canReuse && <Button asChild size="sm" variant="outline"><Link to={reuseMaterialsUrl(job)}>Создать конспект</Link></Button>}
            {job.status === 'failed' && !canRetry && !canReuse && job.mode === 'real' && ownsJob && canWrite && <Button asChild size="sm" variant="outline"><Link to={'/new?course=' + job.courseId + (job.category ? '&folder=' + encodeURIComponent(job.category) : '')}>Новое задание</Link></Button>}
            {job.transcriptAssetId && <Button asChild size="sm" variant="outline"><a href={'/api/assets/' + job.transcriptAssetId} download><Download size={14}/>Расшифровка</a></Button>}
          </div></div>
          <div className="inline-job-steps" id={'stages-' + job.id} hidden={!expanded}>{expanded && <JobSteps job={job}/>}</div>
        </Fragment>
      })}</div>
    </div>
  </>
}

function ReaderPage({ state, entries, onOutline, basePath = '' }: { state: AppState; entries: TocEntry[]; onOutline: (entries: TocEntry[]) => void; basePath?: string }) {
  const { id } = useParams()
  const note = state.notes.find(n => n.id === id)
  const [mobileToc, setMobileToc] = useState(false)
  useEffect(() => { setMobileToc(false) }, [id])
  if (!note) return <NotFound />
  if (note.contentLoaded === false) return <p role="status">Открываем заметку…</p>
  const job = state.jobs.find(j => j.id === note.jobId)
  return <><div className="reader-heading"><h1>{note.title}</h1><div className="reader-info"><div className="reader-meta"><time dateTime={new Date(note.createdAt).toISOString()}>{formatDate(note.createdAt)}</time>{job?.pdfAssetId && <Link to={{ hash: '#presentation' }}>Презентация</Link>}{note.origin !== 'manual' && <span>{note.isMock ? 'Учебный пример' : job?.provider ? `Из видео · ${PROVIDER_NAMES[job.provider]}` : 'Из видео'}</span>}</div></div></div>{!basePath && note.isMock && <p className="reader-demo">Учебный пример. Содержимое источника не анализировалось.</p>}{entries.length > 0 && <div className="mobile-toc"><button aria-expanded={mobileToc} onClick={() => setMobileToc(!mobileToc)}><ListOrdered size={16} />Оглавление<ChevronDown size={15} /></button>{mobileToc && <QuartzToc entries={entries} onNavigate={() => setMobileToc(false)} />}</div>}<QuartzArticle onOutline={onOutline} title={note.title} markdown={note.markdown} />{job?.pdfAssetId && <Presentation key={job.pdfAssetId} asset={job.pdfAssetId} embedded={note.markdown.includes(`/api/assets/${job.pdfAssetId}/slides/`)} name={job.pdfName ?? "Слайды.pdf"} />}{job && <details className="mobile-context mobile-source"><summary>Материалы и параметры</summary><SourceDetails note={note} state={state}/></details>}<Link className="back-link" to={folderLink(note.courseId, note.category)}><ArrowLeft size={15} />К категории</Link></>
}

function SourceDetails({ note, state }: { note: Note; state: AppState }) {
  const job = state.jobs.find(j => j.id === note.jobId)
  if (!job) return null
  const parameters = [
    ['Формат', job.preset === 'lecture' ? 'Лекция' : 'Семинар'],
    ['Объём', job.targetWords ? `До ${job.targetWords} слов` : LENGTH_LABELS[job.length]],
    ['Язык речи', { ru:'Русский', en:'Английский', auto:'Автоопределение' }[job.language ?? 'ru']],
    ['Провайдер', job.mode === 'mock' ? 'Демо' : PROVIDER_NAMES[job.provider ?? 'codex']],
    ['Модель', job.model], ['Глубина рассуждения', job.effort && (job.effortApplied ?? (job.mode === 'real' && job.provider === 'codex')) && {low:'Низкая',medium:'Средняя',high:'Высокая',xhigh:'Максимальная'}[job.effort]],
    ['Иллюстрации', job.pdfName && slidePolicy(job.slideLimit)], ['Инструкции', job.instructions],
  ]
  return <section className="source-details" aria-label="Материалы и параметры">
    <h3>Материалы</h3><dl><dt>{job.sourceType === 'file' ? 'Видеофайл' : 'Ссылка'}</dt><dd title={job.sourceName}>{job.sourceAssetId ? <a href={`/api/assets/${job.sourceAssetId}`} download>{job.sourceName}</a> : job.sourceName}</dd>
    {job.transcriptAssetId && <><dt>Расшифровка</dt><dd><a href={'/api/assets/' + job.transcriptAssetId} download>Скачать TXT</a></dd></>}
    {job.pdfName && <><dt>Презентация</dt><dd>{job.pdfAssetId ? <Link to={{ hash: '#presentation' }}>{job.pdfName}</Link> : job.pdfName}</dd></>}</dl>
    {job.transcriptAssetId && state.courses.some(course => course.id === job.courseId && course.role !== 'viewer') && <Link className="back-link" to={reuseMaterialsUrl(job)}>Создать новый конспект</Link>}
    <h3>Параметры генерации</h3><dl>{parameters.filter(([,value]) => value).map(([label,value]) => <Fragment key={String(label)}><dt>{label}</dt><dd>{value}</dd></Fragment>)}</dl>
    {job.slideTextCondensed ? <p>Для генерации использованы выдержки из презентации.</p> : null}
    {job.slidesPartial && <p>Часть слайдов не удалось прочитать автоматически.</p>}
  </section>
}

function JobSteps({ job }: { job: Job }) {
  const [clock, setClock] = useState(Date.now)
  useEffect(() => {
    if (job?.status !== 'generating' || !job.stageStartedAt) return
    setClock(Date.now())
    const timer = window.setInterval(() => setClock(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [job?.status, job?.stageStartedAt])
  const elapsed = Math.max(0, Math.floor((clock - (job.stageStartedAt ?? clock)) / 1000))
  const steps = STAGES.filter(step => step.status !== 'preparing' || job.pdfName)
  const current = steps.findIndex(step => step.status === (job.status === 'failed' ? job.failedStage : job.status))
  return <section className="job-steps context-block" aria-label="Ход задания">
    <h2 className="sr-only">Этапы задания «{job.title}»</h2>

    <ol>{steps.map((step, index) => {
      const done = job.status === 'ready' || (job.status === 'waiting_generation' && index < 2) || (current >= 0 && index < current)
      const active = step.status === job.status
      return <li key={step.status} aria-current={active ? 'step' : undefined} className={done ? 'step-done' : active ? 'step-active' : job.status === 'failed' && index === current ? 'step-failed' : ''}>
        <span className="step-icon">{done ? <Check size={13} /> : active ? <LoaderCircle className="spin" size={15}/> : job.status === 'failed' && index === current ? <X size={13}/> : <Circle size={7}/>}</span>
        <div>{step.status === 'preparing' ? 'Чтение презентации' : step.label}
          {step.status === 'transcribing' && job.transcriptReused && <small>Из сохранённой расшифровки</small>}
          {step.status === 'preparing' && job.slidesReused && <small>Из кэша</small>}
          {step.status === 'preparing' && job.slidesPartial && <small>Часть слайдов не удалось прочитать</small>}
          {active && step.status === 'transcribing' && <ProgressMeter className="job-step-progress" label="Распознано аудио" value={job.recognitionPercent} showPercent/>}
          {step.status === 'generating' && job.model && <small>{job.model}{active && job.stageStartedAt && <time aria-label="Время ожидания ответа модели"> · {Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, '0')}</time>}</small>}
          {active && step.status === 'generating' && <ProgressMeter className="job-step-progress" label="Создание конспекта"/>}
        </div>
      </li>
    })}</ol>

  </section>
}

function NotFound() { return <div className="quiet-empty"><BookOpen size={30} /><h1>Страница не найдена</h1><p>Проверьте ссылку и доступ к категории.</p><Button asChild variant="outline"><Link to="/">В библиотеку<ArrowLeft size={15} /></Link></Button></div> }
