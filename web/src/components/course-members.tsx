import { useEffect, useState, type FormEvent } from 'react'
import { Users } from 'lucide-react'
import { api } from '../data/api'
import { library, type User } from '../data/store'
import type { Course } from '../data/model'
import { Dialog } from './ui/dialog'
import { Button } from './ui/button'

export function CourseMembers({ course, inherited = false }: { course: Course; inherited?: boolean }) {
  const [open, setOpen] = useState(false)
  const [members, setMembers] = useState<(User & { role: string })[]>([])
  const [link, setLink] = useState('')
  const [linkRole, setLinkRole] = useState('viewer')
  const [active, setActive] = useState(false)
  const [copied, setCopied] = useState(false)
  const [email, setEmail] = useState('')
  const [role, setRole] = useState('viewer')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const load = () => api<(User & { role: string })[]>(`/courses/${course.id}/members`).then(setMembers)
  useEffect(() => { if (open) { setError(''); setLink(''); setCopied(false); if (course.role === 'owner') void api<{active:boolean;role:string|null}>(`/courses/${course.id}/invite`).then(r => {setActive(r.active); setLinkRole(r.role ?? 'viewer')}).catch(e => setError(e.message)); void load().catch(e => setError(e.message)) } }, [open, course.id])
  const change = async (id: string, value: string) => {
    if (value === 'none' && !window.confirm('Убрать участника из категории? Его доступ будет отозван.')) return
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
  const makeLink = async () => {
    setBusy(true); setError(''); setCopied(false)
    try {
      const {token} = await api<{token:string}>(`/courses/${course.id}/invite`, {method:'POST',body:JSON.stringify({role:linkRole})})
      setLink(window.location.origin + '/invite#' + token); setActive(true)
    } catch(e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  const revoke = async () => {
    setBusy(true); setError('')
    try { await api(`/courses/${course.id}/invite`, {method:'DELETE'}); setLink(''); setActive(false) }
    catch(e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  return <><Button variant="outline" size="sm" onClick={() => setOpen(true)}><Users size={15} />Доступ</Button><Dialog open={open} onOpenChange={setOpen} title="Доступ к категории" description={inherited ? `Права наследуются от «${course.title}». Изменения распространяются на всю эту категорию.` : course.title}>{members.map(member => <div className="member-row" key={member.id}><span className="member-identity">{member.name}<small>{member.email}</small></span>{member.role === 'owner' || course.role !== 'owner' ? <span>{{ owner: 'Владелец', editor: 'Редактор', viewer: 'Читатель' }[member.role]}</span> : <select aria-label={`Роль ${member.name}`} value={member.role} disabled={busy} onChange={event => void change(member.id, event.target.value)}><option value="editor">Редактор</option><option value="viewer">Читатель</option><option value="none">Убрать доступ</option></select>}</div>)}{course.role === 'owner' && <><section className="invite-links"><h3>Ссылка-приглашение</h3><p className="field-hint">Для одного участника, действует сутки.</p><label className="field-label" htmlFor="invite-role">Доступ по ссылке</label><select id="invite-role" value={linkRole} disabled={busy} onChange={e => {setLinkRole(e.target.value); setLink('')}}><option value="viewer">Чтение</option><option value="editor">Редактирование</option></select>
      {link && <input readOnly aria-label="Ссылка для участника" value={link} onFocus={e => e.target.select()} />}
      <div className="invite-actions">{link ? <Button size="sm" onClick={() => {void navigator.clipboard.writeText(link).then(() => setCopied(true)).catch(() => setError('Выделите ссылку и скопируйте её вручную.'))}}>{copied ? 'Скопировано' : 'Скопировать ссылку'}</Button> : <Button size="sm" disabled={busy} onClick={() => void makeLink()}>{active ? 'Заменить ссылку' : 'Создать ссылку'}</Button>}{active && <Button variant="ghost" size="sm" disabled={busy} onClick={() => void revoke()}>Отозвать ссылку</Button>}</div></section><details><summary>Добавить по email</summary><form className="member-invite" onSubmit={event => void invite(event)}><label className="field-label" htmlFor="member-email">Добавить участника по email</label><input id="member-email" type="email" value={email} onChange={event => setEmail(event.target.value)} autoComplete="off" maxLength={254} required /><p className="field-hint">Участник должен зарегистрироваться и подтвердить свой email.</p><label className="field-label" htmlFor="member-role">Роль</label><select id="member-role" value={role} onChange={event => setRole(event.target.value)}><option value="viewer">Читатель</option><option value="editor">Редактор</option></select><Button size="sm" variant="outline" type="submit" disabled={busy}>Добавить участника</Button></form></details></>}{error && <p className="field-error" role="alert">{error}</p>}</Dialog></>
}
