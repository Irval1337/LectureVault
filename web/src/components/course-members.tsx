import { useEffect, useState, type FormEvent } from 'react'
import { Users } from 'lucide-react'
import { api } from '../data/api'
import { library, type User } from '../data/store'
import type { Course } from '../data/model'
import { Dialog } from './ui/dialog'
import { Button } from './ui/button'

export function CourseMembers({ course }: { course: Course }) {
  const [open, setOpen] = useState(false)
  const [members, setMembers] = useState<(User & { role: string })[]>([])
  const [email, setEmail] = useState('')
  const [role, setRole] = useState('viewer')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const load = () => api<(User & { role: string })[]>(`/courses/${course.id}/members`).then(setMembers)
  useEffect(() => { if (open) { setError(''); void load().catch(e => setError(e.message)) } }, [open, course.id])
  const change = async (id: string, value: string) => {
    if (value === 'none' && !window.confirm('Убрать участника из курса? Его доступ будет отозван.')) return
    setBusy(true); setError('')
    try {
      await api(`/courses/${course.id}/members/${id}`, { method: 'PUT', body: JSON.stringify({ role: value }) })
      await load(); await library.tick()
    } catch (error) { setError((error as Error).message) } finally { setBusy(false) }
  }
  const invite = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError('')
    try {
      await api(`/courses/${course.id}/members`, { method: 'POST', body: JSON.stringify({ email, role }) })
      setEmail(''); await load()
    } catch (error) { setError((error as Error).message) } finally { setBusy(false) }
  }
  return <><Button variant="outline" size="sm" onClick={() => setOpen(true)}><Users size={15} />Доступ</Button><Dialog open={open} onOpenChange={setOpen} title="Доступ к курсу" description={course.title}>{members.map(member => <div className="member-row" key={member.id}><span className="member-identity">{member.name}<small>{member.email}</small></span>{member.role === 'owner' || course.role !== 'owner' ? <span>{{ owner: 'Владелец', editor: 'Редактор', viewer: 'Читатель' }[member.role]}</span> : <select aria-label={`Роль ${member.name}`} value={member.role} disabled={busy} onChange={event => void change(member.id, event.target.value)}><option value="editor">Редактор</option><option value="viewer">Читатель</option><option value="none">Убрать доступ</option></select>}</div>)}{course.role === 'owner' && <form className="member-invite" onSubmit={event => void invite(event)}><label className="field-label" htmlFor="member-email">Добавить участника по email</label><input id="member-email" type="email" value={email} onChange={event => setEmail(event.target.value)} autoComplete="off" maxLength={254} required /><p className="field-hint">Участник должен зарегистрироваться и подтвердить свой email.</p><label className="field-label" htmlFor="member-role">Роль</label><select id="member-role" value={role} onChange={event => setRole(event.target.value)}><option value="viewer">Читатель</option><option value="editor">Редактор</option></select><Button size="sm" variant="outline" type="submit" disabled={busy}>Добавить участника</Button></form>}{error && <p className="field-error" role="alert">{error}</p>}</Dialog></>
}
