import { useEffect, useState } from 'react'
import { Users } from 'lucide-react'
import { api } from '../data/api'
import { library, type User } from '../data/store'
import type { Course } from '../data/model'
import { Dialog } from './ui/dialog'
import { Button } from './ui/button'

export function CourseMembers({ course }: { course: Course }) {
  const [open, setOpen] = useState(false)
  const [members, setMembers] = useState<(User & { role: string })[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (open) { setError(''); void api<(User & { role: string })[]>(`/courses/${course.id}/members`).then(setMembers).catch(e => setError(e.message)) } }, [open, course.id])
  const change = async (id: string, role: string) => {
    setBusy(true); setError('')
    try {
      await api(`/courses/${course.id}/members/${id}`, { method: 'PUT', body: JSON.stringify({ role }) })
      setMembers(previous => previous.map(m => m.id === id ? { ...m, role } : m)); await library.tick()
    } catch (error) { setError((error as Error).message) }
    finally { setBusy(false) }
  }
  return <><Button variant="outline" size="sm" onClick={() => setOpen(true)}><Users size={15} />Доступ</Button><Dialog open={open} onOpenChange={setOpen} title="Доступ к курсу" description={course.title}>{members.map(member => <div className="member-row" key={member.id}><span>{member.name}</span>{member.role === 'owner' || course.role !== 'owner' ? <span>{{ owner: 'Владелец', editor: 'Редактор', viewer: 'Читатель', none: 'Нет доступа' }[member.role]}</span> : <select aria-label={`Роль ${member.name}`} value={member.role} disabled={busy} onChange={event => void change(member.id, event.target.value)}><option value="editor">Редактор</option><option value="viewer">Читатель</option><option value="none">Нет доступа</option></select>}</div>)}{error && <p className="field-error" role="alert">{error}</p>}<p className="field-hint">Права проверяются сервером при каждом запросе. Демо-вход действует только локально.</p></Dialog></>
}
