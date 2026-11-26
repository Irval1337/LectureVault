import { useState, type ReactNode } from 'react'
import { Link, NavLink } from 'react-router-dom'
import { BookOpen, Download, ListOrdered, Menu, Plus, PanelLeftOpen, PanelRightClose, PanelRightOpen, Settings, Search } from 'lucide-react'
import { Button } from './ui/button'
import { ActionMenu } from './ui/action-menu'
import { TabBar } from './ui/tab-bar'
import { NoteExportLinks } from './organization'
import type { TocEntry } from '../quartz/reader'
import { QuartzToc } from './reader'
import type { Note } from '../data/model'

export function ActivityRail({ basePath, queueCount, section, onCreate, onSearch, onExpand }: {
  basePath: string; queueCount: number; section: 'library' | 'create' | 'queue' | 'settings';
  onExpand: () => void; onCreate: () => void; onSearch: () => void
}) {
  return <nav className="activity-rail" aria-label="Основные разделы">
    <button className="rail-button expand-navigator" aria-controls="course-navigator" aria-label="Развернуть панель категорий" aria-expanded={false} title="Развернуть панель категорий" onClick={onExpand}><PanelLeftOpen size={19}/></button>
    <Link className={`rail-button ${section === 'library' ? 'active' : ''}`} to={basePath || '/'} title="Библиотека" aria-label="Библиотека" aria-current={section === 'library' ? 'location' : undefined}><BookOpen size={19} /></Link>
    {<button className={`rail-button ${section === 'create' ? 'active' : ''}`} title="Добавить заметку" aria-label="Добавить заметку" aria-pressed={section === 'create'} onClick={onCreate}><Plus size={19} /></button>}
    {<NavLink to="/queue" className="rail-button" title="Очередь заданий" aria-label={`Очередь заданий${queueCount ? `: ${queueCount} в работе` : ''}`}><ListOrdered size={19} /></NavLink>}
    <button className="rail-button" title="Поиск (Ctrl/Cmd+K)" aria-label="Найти в библиотеке" onClick={onSearch}><Search size={19} /></button>
    <NavLink to="/settings" className={() => 'rail-button rail-settings'} title="Настройки" aria-label="Настройки"><Settings size={18}/></NavLink>
  </nav>
}

export function WorkspaceHeader({ label, mode, breadcrumbs, actions, onOpenNavigation }: {
  label: string; mode: string; breadcrumbs: ReactNode; actions?: ReactNode;
  onOpenNavigation: () => void
}) {
  return <>
    {mode && <div className="document-tab"><BookOpen size={14} /><span className="document-tab-label" title={label}>{label}</span>{mode && <span className="document-mode">{mode}</span>}</div>}
    <header className="topbar"><div className="breadcrumbs"><button className="mobile-menu icon-action" aria-label="Открыть навигацию" onClick={onOpenNavigation}><Menu size={20} /></button>{breadcrumbs}</div><div className="topbar-actions">{actions}</div></header>
  </>
}

export function ReaderActions({ note, contextOpen, onToggleContext, onEdit, onDelete, contextAvailable = true }: {
  note: Note; onEdit?: () => void; onDelete?: () => void; contextAvailable?: boolean; contextOpen: boolean; onToggleContext: () => void
}) {
  return <div className="reader-toolbar">
    {onEdit && <Button variant="ghost" size="sm" onClick={onEdit}>Редактировать</Button>}
    <ActionMenu label="Скачать заметку" className="reader-download" trigger={<><Download size={14}/><span>Скачать</span></>}>
      <NoteExportLinks id={note.id}/>
    </ActionMenu>
    {contextAvailable && <button className="icon-action context-toggle" aria-label={contextOpen ? 'Скрыть контекст' : 'Показать контекст'} title={contextOpen ? 'Скрыть контекст' : 'Показать контекст'} aria-pressed={contextOpen} onClick={onToggleContext}>{contextOpen ? <PanelRightClose size={17} /> : <PanelRightOpen size={17} />}</button>}
    {onDelete && <ActionMenu label="Действия заметки"><Button variant="ghost" size="sm" className="danger-action" onClick={onDelete}>Удалить</Button></ActionMenu>}
  </div>
}

export function ReaderInspector({ entries, source }: { entries: TocEntry[]; source: ReactNode }) {
  const [tab, setTab] = useState<'outline' | 'source'>('outline')
  if (!entries.length) return <div className="context-block">{source}</div>
  return <><TabBar label="Контекст конспекта" panelId="reader-inspector-panel" className="inspector-tabs" value={tab} onValueChange={setTab} items={source ? [{ value: 'outline', label: 'Оглавление' }, { value: 'source', label: 'Материалы' }] : [{ value: 'outline', label: 'Оглавление' }]} /><div className="inspector-panel" id="reader-inspector-panel" role="tabpanel" aria-labelledby={`reader-inspector-panel-${tab}-tab`}>{tab === 'outline' ? <QuartzToc entries={entries}/> : source}</div></>
}
