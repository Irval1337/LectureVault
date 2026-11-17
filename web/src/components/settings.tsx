import { useState, type FormEvent } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { api } from '../data/api'
import { auth, useSession, type User } from '../data/store'
import { ConnectionSettings } from './providers'
import { Button } from './ui/button'
import { PageHeading } from './ui/page-heading'

export function SettingsPage() {
  const session = useSession()
  const navigate = useNavigate()
  const [name, setName] = useState(session.user?.name ?? '')
  const [password, setPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [deletionPassword, setDeletionPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const run = async (action: () => Promise<void>) => {
    setBusy(true); setError(''); setNotice('')
    try { await action() } catch (error) { setError((error as Error).message) } finally { setBusy(false) }
  }
  const saveProfile = (event: FormEvent) => {
    event.preventDefault(); void run(async () => {
      const result = await api<{ user: User }>('/auth/profile', { method: 'PATCH', body: JSON.stringify({ name }) })
      auth.updateUser(result.user); setNotice('Имя сохранено.')
    })
  }
  const changePassword = (event: FormEvent) => {
    event.preventDefault(); void run(async () => {
      if (newPassword !== confirm) throw new Error('Новые пароли не совпадают.')
      await api('/auth/change-password', { method: 'POST', body: JSON.stringify({ password, newPassword }) })
      setPassword(''); setNewPassword(''); setConfirm(''); setNotice('Пароль изменён. Другие сеансы завершены.')
    })
  }
  const removeAccount = (event: FormEvent) => {
    event.preventDefault()
    if (!window.confirm('Удалить аккаунт, личные материалы и принадлежащие вам общие курсы для всех участников? Это действие нельзя отменить.')) return
    void run(async () => {
      await api('/account', { method: 'DELETE', body: JSON.stringify({ password: deletionPassword }) })
      setDeletionPassword(''); if (session.user) { try { auth.eraseLocalUserData(session.user.id) } catch { /* server account is deleted */ } } auth.clear(); navigate('/login', { replace: true })
    })
  }
  return <><PageHeading title="Настройки" /><div className="settings-page"><section className="settings-section"><h2>Профиль</h2><p className="settings-email">{session.user?.email}</p><form className="settings-form" onSubmit={saveProfile}><label className="field-label" htmlFor="profile-name">Имя</label><input id="profile-name" value={name} onChange={event => setName(event.target.value)} required maxLength={80} autoComplete="name" /><Button size="sm" type="submit" disabled={busy}>Сохранить имя</Button></form></section><ConnectionSettings /><section className="settings-section"><h2>Безопасность</h2><form className="settings-form" onSubmit={changePassword}><label className="field-label" htmlFor="current-password">Текущий пароль</label><input id="current-password" type="password" value={password} onChange={event => setPassword(event.target.value)} autoComplete="current-password" required maxLength={128} /><label className="field-label" htmlFor="new-password">Новый пароль</label><input id="new-password" type="password" value={newPassword} onChange={event => setNewPassword(event.target.value)} autoComplete="new-password" required minLength={15} maxLength={128} /><label className="field-label" htmlFor="confirm-new-password">Повторите новый пароль</label><input id="confirm-new-password" type="password" value={confirm} onChange={event => setConfirm(event.target.value)} autoComplete="new-password" required minLength={15} maxLength={128} /><Button size="sm" variant="outline" type="submit" disabled={busy}>Изменить пароль</Button></form><div className="settings-session-actions"><Button size="sm" variant="outline" disabled={busy} onClick={() => void run(async () => { await api('/auth/logout-all', { method: 'POST' }); auth.clear(); navigate('/login') })}>Завершить все сеансы</Button><Button size="sm" variant="ghost" disabled={busy} onClick={() => void run(async () => { await auth.logout(); navigate('/login') })}>Выйти</Button></div></section>{error && <p role="alert" className="field-error">{error}</p>}{notice && <p role="status" className="auth-notice">{notice}</p>}<details className="settings-section account-removal"><summary>Удалить аккаунт</summary><p>Будут удалены ваши подключения, личные материалы и общие курсы, владельцем которых вы являетесь. Участники потеряют к ним доступ. Заметки в чужих общих курсах сохранятся.</p><form className="settings-form" onSubmit={removeAccount}><label className="field-label" htmlFor="deletion-password">Пароль для подтверждения</label><input id="deletion-password" type="password" value={deletionPassword} onChange={event => setDeletionPassword(event.target.value)} autoComplete="current-password" maxLength={128} required /><Button size="sm" variant="outline" type="submit" disabled={busy}>Удалить аккаунт и данные</Button></form></details><Link className="privacy-link" to="/privacy">Конфиденциальность</Link></div></>
}
