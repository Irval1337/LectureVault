import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../data/api'
import { library } from '../data/store'
import { Button } from './ui/button'
import { PageHeading } from './ui/page-heading'

export const pendingInvite = 'lecturevault.invitation'
export function Invitation() {
  const navigate = useNavigate()
  const [token] = useState(() => location.hash.slice(1) || sessionStorage.getItem(pendingInvite) || '')
  const [course, setCourse] = useState<{id: string; title: string; role: string}>()
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let mounted = true
    api<typeof course>('/invitations/preview', { method:'POST', body:JSON.stringify({token}) })
      .then(value => { if (mounted) setCourse(value) }).catch(e => { if (mounted) setError(e.message) })
    return () => { mounted = false }
  }, [token])
  async function accept() {
    setBusy(true)
    try {
      const result = await api<{id:string}>('/invitations/accept', {method:'POST',body:JSON.stringify({token})})
      sessionStorage.removeItem(pendingInvite)
      await library.tick()
      navigate('/courses/' + result.id, {replace:true})
    } catch(e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  return <><PageHeading title={course?.title ?? 'Приглашение в категорию'} />
    {course && <p>Ваша роль — {course.role === 'editor' ? 'редактор' : 'читатель'}.</p>}
    {error && <p className="field-error" role="alert">{error}</p>}
    <div className="dialog-actions"><Button variant="ghost" onClick={() => { sessionStorage.removeItem(pendingInvite); navigate('/', {replace:true}) }}>В библиотеку</Button>{course && <Button disabled={busy} onClick={() => void accept()}>Принять приглашение</Button>}</div></>
}
