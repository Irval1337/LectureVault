import { useState, type ReactNode } from 'react'
import { Link, NavLink } from 'react-router-dom'
import { ArrowDownToLine, ArrowLeft, ChevronRight, BookOpen, HelpCircle, ListOrdered, Menu, Moon, Plus, PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen, Search, Sun } from 'lucide-react'
import { Button } from './ui/button'
import { TabBar } from './ui/tab-bar'
import { QuartzToc } from './reader'
import { downloadText } from '../lib/utils'
import type { Note } from '../data/model'

export function ActivityRail({ basePath, isDemo, queueCount, section, onCreate, onSearch, onHelp }: {
  basePath: string; isDemo: boolean; queueCount: number; section: 'library' | 'create' | 'queue';
  onCreate: () => void; onSearch: () => void; onHelp: () => void
}) {
  return <nav className="activity-rail" aria-label="Основные разделы">
    <Link className="rail-brand" to={basePath || '/'} aria-label="LectureVault — библиотека">L<span>V</span></Link>
    <Link className={`rail-button ${section === 'library' ? 'active' : ''}`} to={basePath || '/'} title="Библиотека" aria-label="Библиотека" aria-current={section === 'library' ? 'location' : undefined}><BookOpen size={19} /></Link>
    {!isDemo && <button className={`rail-button ${section === 'create' ? 'active' : ''}`} title="Новый конспект" aria-label="Новый конспект" aria-pressed={section === 'create'} onClick={onCreate}><Plus size={19} /></button>}
    {!isDemo && <NavLink to="/queue" className="rail-button" title="Очередь заданий" aria-label={`Очередь заданий${queueCount ? `: ${queueCount} в работе` : ''}`}><ListOrdered size={19} /></NavLink>}
    <div className="rail-bottom"><button className="rail-button" title="Поиск (Ctrl/Cmd+K)" aria-label="Найти в библиотеке" onClick={onSearch}><Search size={19} /></button>{isDemo && <Link to="/" className="rail-button" aria-label="Вернуться в мою библиотеку" title="Моя библиотека"><ArrowLeft size={18} /></Link>}<button className="rail-button" aria-label="Справка" title="Как это работает" onClick={onHelp}><HelpCircle size={18} /></button></div>
  </nav>
}

export function WorkspaceHeader({ label, mode, breadcrumbs, actions, isDemo, collapsed, onToggleNavigation, onOpenNavigation, onHelp }: {
  label: string; mode: string; breadcrumbs: ReactNode; actions?: ReactNode; isDemo: boolean; collapsed: boolean; onToggleNavigation: () => void;
  onOpenNavigation: () => void; onHelp: () => void
}) {
  return <>
    <div className="document-tab"><BookOpen size={14} /><span className="document-tab-label" title={label}>{label}</span><span className="document-mode">{mode}</span></div>
    <header className="topbar"><div className="breadcrumbs"><button className="icon-action navigation-toggle" aria-label={collapsed ? 'Показать проводник' : 'Скрыть проводник'} title={collapsed ? 'Показать проводник' : 'Скрыть проводник'} aria-pressed={!collapsed} onClick={onToggleNavigation}>{collapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}</button><button className="mobile-menu icon-action" aria-label="Открыть навигацию" onClick={onOpenNavigation}><Menu size={20} /></button>{breadcrumbs}</div><div className="topbar-actions">{actions}{isDemo ? <Link className="exit-demo" to="/" title="Выйти из примера">Выйти из примера<ArrowLeft size={13} /></Link> : !actions && <button className="demo-badge" onClick={onHelp}>Mock-генерация</button>}</div></header>
  </>
}

export function ReaderActions({ note, theme, onToggleTheme, contextOpen, onToggleContext }: {
  note: Note; theme: 'dark' | 'light'; onToggleTheme: () => void; contextOpen: boolean; onToggleContext: () => void
}) {
  return <div className="reader-toolbar"><Button variant="ghost" size="sm" onClick={() => downloadText(note.markdown, `${note.title}.md`)}><ArrowDownToLine size={15} />Скачать .md</Button><button className="icon-action theme-toggle" aria-label={theme === 'dark' ? 'Светлая тема ридера' : 'Тёмная тема ридера'} title={theme === 'dark' ? 'Светлая тема ридера' : 'Тёмная тема ридера'} onClick={onToggleTheme}>{theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}</button><button className="icon-action context-toggle" aria-label={contextOpen ? 'Скрыть контекст' : 'Показать контекст'} title={contextOpen ? 'Скрыть контекст' : 'Показать контекст'} aria-pressed={contextOpen} onClick={onToggleContext}>{contextOpen ? <PanelRightClose size={17} /> : <PanelRightOpen size={17} />}</button></div>
}

export function ReaderInspector({ markdown, source }: { markdown: string; source: ReactNode }) {
  const [tab, setTab] = useState<'outline' | 'source'>('outline')
  return <><TabBar label="Контекст конспекта" panelId="reader-inspector-panel" className="inspector-tabs" value={tab} onValueChange={setTab} items={[{ value: 'outline', label: 'Оглавление' }, { value: 'source', label: 'Источник' }]} /><div className="inspector-panel" id="reader-inspector-panel" role="tabpanel" aria-labelledby={`reader-inspector-panel-${tab}-tab`}>{tab === 'outline' ? <><QuartzToc markdown={markdown} /><button className="source-shortcut" onClick={() => setTab('source')}>Видео и материалы<ChevronRight size={13} /></button></> : source}</div></>
}
