import { useState, type FormEvent } from 'react'
import { useLocation, useNavigate, Link } from 'react-router-dom'
import { api } from '../data/api'
import { auth, useSession, type User } from '../data/store'
import { ConnectionSettings } from './providers'
import { StorageSettings } from './storage'
import { Button } from './ui/button'
import { PageHeading } from './ui/page-heading'
import { TabBar } from './ui/tab-bar'

type Section = 'profile' | 'connections' | 'storage' | 'security'
type Feedback = { area: 'profile' | 'security' | 'account'; error?: string; notice?: string }
const sections: { value: Section; label: string }[] = [
  { value: 'profile', label: 'Профиль' },
  { value: 'connections', label: 'Подключения' },
  { value: 'storage', label: 'Хранилище' },
  { value: 'security', label: 'Безопасность' },
]

export function SettingsPage() {
  const session = useSession()
  const navigate = useNavigate()
  const location = useLocation()
  const section = sections.find(item => '#' + item.value === location.hash)?.value ?? 'profile'
  const [name, setName] = useState(session.user?.name ?? '')
  const [password, setPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [deletionPassword, setDeletionPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [feedback, setFeedback] = useState<Feedback | null>(null)

  const run = async (area: Feedback['area'], action: () => Promise<void>) => {
    setBusy(true); setFeedback(null)
    try { await action() }
    catch (error) { setFeedback({ area, error: (error as Error).message }) }
    finally { setBusy(false) }
  }
  const message = (area: Feedback['area']) => feedback?.area === area && <>
    {feedback.error && <p role="alert" className="field-error">{feedback.error}</p>}
    {feedback.notice && <p role="status" className="auth-notice">{feedback.notice}</p>}
  </>
  const saveProfile = (event: FormEvent) => {
    event.preventDefault()
    void run('profile', async () => {
      const result = await api<{ user: User }>('/auth/profile', { method: 'PATCH', body: JSON.stringify({ name }) })
      auth.updateUser(result.user); setFeedback({ area: 'profile', notice: 'Имя сохранено.' })
    })
  }
  const changePassword = (event: FormEvent) => {
    event.preventDefault()
    void run('security', async () => {
      if (newPassword !== confirm) throw new Error('Новые пароли не совпадают.')
      await api('/auth/change-password', { method: 'POST', body: JSON.stringify({ password, newPassword }) })
      setPassword(''); setNewPassword(''); setConfirm('')
      setFeedback({ area: 'security', notice: 'Пароль изменён. Другие сеансы завершены.' })
    })
  }
  const removeAccount = (event: FormEvent) => {
    event.preventDefault()
    if (!window.confirm('Удалить аккаунт, личные материалы и принадлежащие вам общие категории для всех участников? Это действие нельзя отменить.')) return
    void run('account', async () => {
      await api('/account', { method: 'DELETE', body: JSON.stringify({ password: deletionPassword }) })
      setDeletionPassword('')
      if (session.user) { try { auth.eraseLocalUserData(session.user.id) } catch { /* server account is deleted */ } }
      auth.clear(); navigate('/login', { replace: true })
    })
  }

  return <div className="settings-page">
    <PageHeading title="Настройки" />
    <TabBar label="Разделы настроек" panelId="settings-panel" value={section} items={sections} className="settings-tabs" onValueChange={value => { navigate('/settings#' + value, { replace: true, state: location.state }); window.scrollTo(0, 0) }} />
    <div className="settings-panel" id="settings-panel" role="tabpanel" aria-labelledby={`settings-panel-${section}-tab`}>
      {section === 'profile' && <section className="settings-section">
        <h2>Личные данные</h2>
        <form className="settings-form" onSubmit={saveProfile}>
          <div className="settings-field"><label className="field-label" htmlFor="profile-name">Имя</label><input id="profile-name" value={name} onChange={event => setName(event.target.value)} required maxLength={80} autoComplete="name" /></div>
          <div className="settings-field"><span className="field-label">Email</span><p className="settings-email">{session.user?.email}</p><p className="field-hint">Адрес подтверждён.</p></div>
          <Button size="sm" type="submit" disabled={busy}>Сохранить имя</Button>
          {message('profile')}
        </form>
      </section>}
      {section === 'connections' && <ConnectionSettings />}
      {section === 'storage' && <StorageSettings />}
      {section === 'security' && <>
        <section className="settings-section">
          <h2>Пароль</h2>
          <form className="settings-form settings-password-form" onSubmit={changePassword}>
            <div className="settings-field"><label className="field-label" htmlFor="current-password">Текущий пароль</label><input id="current-password" type="password" value={password} onChange={event => setPassword(event.target.value)} autoComplete="current-password" required maxLength={128} /></div>
            <div className="settings-field"><label className="field-label" htmlFor="new-password">Новый пароль</label><input id="new-password" type="password" value={newPassword} onChange={event => setNewPassword(event.target.value)} autoComplete="new-password" required minLength={15} maxLength={128} /></div>
            <div className="settings-field"><label className="field-label" htmlFor="confirm-new-password">Повторите новый пароль</label><input id="confirm-new-password" type="password" value={confirm} onChange={event => setConfirm(event.target.value)} autoComplete="new-password" required minLength={15} maxLength={128} /></div>
            <Button size="sm" variant="outline" type="submit" disabled={busy}>Изменить пароль</Button>
            {message('security')}
          </form>
        </section>
        <section className="settings-section settings-sessions">
          <h2>Сеансы</h2>
          <div className="settings-session-actions">
            <Button size="sm" variant="outline" disabled={busy} onClick={() => void run('security', async () => { await api('/auth/logout-all', { method: 'POST' }); auth.clear(); navigate('/login') })}>Завершить все сеансы</Button>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => void run('security', async () => { await auth.logout(); navigate('/login') })}>Выйти</Button>
          </div>
        </section>
        <details className="account-removal">
          <summary>Удалить аккаунт</summary>
          <p>Будут удалены ваши подключения, личные материалы и общие категории, которыми вы владеете. Заметки в чужих категориях сохранятся.</p>
          <form className="settings-form" onSubmit={removeAccount}>
            <div className="settings-field"><label className="field-label" htmlFor="deletion-password">Пароль для подтверждения</label><input id="deletion-password" type="password" value={deletionPassword} onChange={event => setDeletionPassword(event.target.value)} autoComplete="current-password" maxLength={128} required /></div>
            <Button size="sm" variant="outline" type="submit" disabled={busy}>Удалить аккаунт и данные</Button>
            {message('account')}
          </form>
        </details>
      </>}
    </div>
    {location.state?.fromGeneration && <Link className="back-link" to={{ pathname: '/new', search: typeof location.state.generationSearch === 'string' ? location.state.generationSearch : '' }}>Вернуться к конспекту</Link>}
    {typeof location.state?.fromNote === 'string' && <Link className="back-link" to={'/notes/' + encodeURIComponent(location.state.fromNote) + '/edit'}>Вернуться к редактору</Link>}
    <Link className="privacy-link" to="/privacy">Конфиденциальность</Link>
  </div>
}
