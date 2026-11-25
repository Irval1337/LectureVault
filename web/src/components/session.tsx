import { Fragment, useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { Brand } from './brand'
import { api, ApiError } from '../data/api'
import { auth, useSession, type User } from '../data/store'
import { pendingInvite } from './invitation'
import { ThemeButton } from './theme'
import { Button } from './ui/button'

const authPaths = ['/login', '/register', '/forgot-password', '/reset-password', '/verify-email']

export function SessionGate({ children }: { children: ReactNode }) {
  const session = useSession()
  const location = useLocation()
  useEffect(() => { void auth.initialize() }, [])
  useEffect(() => {
    if (location.pathname === '/invite' && /^[A-Za-z0-9_-]{43}$/.test(location.hash.slice(1))) sessionStorage.setItem(pendingInvite, location.hash.slice(1))
  }, [location.pathname, location.hash])
  if (location.pathname === '/privacy') return <Privacy />
  if (session.loading) return <main className="session-page"><p role="status">Открываем LectureVault…</p></main>
  if (authPaths.includes(location.pathname)) {
    if (session.user && ['/login', '/register'].includes(location.pathname)) return <Navigate to={sessionStorage.getItem(pendingInvite) ? '/invite' : '/'} replace />
    return <AuthScreen key={location.pathname} />
  }
  if (session.user) return <Fragment key={session.user.id}>{children}</Fragment>
  return <AuthScreen />
}

function AuthScreen() {
  const location = useLocation()
  const navigate = useNavigate()
  const session = useSession()
  const path = authPaths.includes(location.pathname) ? location.pathname : '/login'
  const [email, setEmail] = useState((location.state as { email?: string })?.email ?? '')
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [field, setField] = useState('')
  const [notice, setNotice] = useState('')
  const [token, setToken] = useState(() => new URLSearchParams(location.hash.slice(1)).get('token') ?? '')
  useEffect(() => {
    if (location.hash && ['/verify-email', '/reset-password'].includes(location.pathname)) {
      setToken(new URLSearchParams(location.hash.slice(1)).get('token') ?? '')
      window.history.replaceState(window.history.state, '', location.pathname)
    }
  }, [location.hash, location.pathname])
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setError(''); setField('')
    if (['/register', '/reset-password'].includes(path) && password !== confirm) { setError('Пароли не совпадают.'); setField('confirm'); return }
    setBusy(true)
    try {
      if (path === '/register') {
        await api('/auth/register', { method: 'POST', body: JSON.stringify({ email, name, password }) })
        setPassword(''); setConfirm('')
        if (session.emailVerification) navigate('/verify-email', { state: { email } })
        else { await auth.initialize(true); navigate(sessionStorage.getItem(pendingInvite) ? '/invite' : '/') }
      } else if (path === '/login') {
        await auth.login(email, password)
        if (authPaths.includes(location.pathname)) navigate(sessionStorage.getItem(pendingInvite) ? '/invite' : '/', { replace: true })
      } else if (path === '/forgot-password') {
        const result = await api<{ message: string }>('/auth/forgot-password', { method: 'POST', body: JSON.stringify({ email }) })
        setNotice(result.message)
      } else if (path === '/verify-email') {
        if (token) {
          const result = await api<{ user: User }>('/auth/verify-email', { method: 'POST', body: JSON.stringify({ token }) })
          await auth.acceptUser(result.user); navigate(sessionStorage.getItem(pendingInvite) ? '/invite' : '/')
        } else {
          const result = await api<{ message: string }>('/auth/resend-verification', { method: 'POST', body: JSON.stringify({ email }) })
          setNotice(result.message)
        }
      } else if (path === '/reset-password') {
        if (!token) throw new Error('Откройте ссылку из письма для восстановления пароля.')
        await api('/auth/reset-password', { method: 'POST', body: JSON.stringify({ token, password }) })
        setPassword(''); setConfirm(''); setNotice('Пароль изменён. Войдите с новым паролем.')
      }
    } catch (error) {
      if (path === '/login' && error instanceof ApiError && error.code === 'EMAIL_UNVERIFIED') {
        navigate('/verify-email', { state: { email } })
        return
      }
      setError((error as Error).message); setField(error instanceof ApiError ? error.field ?? '' : '') }
    finally { setBusy(false) }
  }
  const title = { '/login': 'Вход', '/register': 'Создать аккаунт', '/forgot-password': 'Восстановить доступ', '/reset-password': 'Новый пароль', '/verify-email': 'Подтвердите email' }[path]
  const action = { '/login': 'Войти', '/register': 'Создать аккаунт', '/forgot-password': 'Отправить ссылку', '/reset-password': 'Сохранить пароль', '/verify-email': token ? 'Подтвердить email' : 'Отправить письмо повторно' }[path]
  const showEmail = path !== '/reset-password' && !(path === '/verify-email' && token)
  const showPassword = ['/register', '/login', '/reset-password'].includes(path)
  return <main className="session-page"><ThemeButton /><section className="session-panel"><Link to="/login" className="brand"><Brand/></Link><h1>{title}</h1>{path === '/verify-email' && <p>{token ? 'Подтвердите адрес, чтобы открыть свою библиотеку.' : 'Откройте ссылку из письма. Если письма нет, проверьте спам или запросите новое.'}</p>}{path === '/forgot-password' && <p>Пришлём ссылку для смены пароля.</p>}<form className="auth-form" onSubmit={submit}>{path === '/register' && <div><label className="field-label" htmlFor="auth-name">Имя</label><input id="auth-name" value={name} onChange={e => setName(e.target.value)} autoComplete="name" maxLength={80} required aria-invalid={field === 'name'} /></div>}{showEmail && <div><label className="field-label" htmlFor="auth-email">Email</label><input id="auth-email" type="email" value={email} onChange={e => setEmail(e.target.value)} autoComplete="email" maxLength={254} required aria-invalid={field === 'email'} /></div>}{showPassword && <div><label className="field-label" htmlFor="auth-password">Пароль</label><input id="auth-password" type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete={path === '/login' ? 'current-password' : 'new-password'} minLength={path === '/login' ? 1 : 15} maxLength={128} required aria-invalid={field === 'password'} />{path !== '/login' && <p className="field-hint">От 15 до 128 символов. Можно использовать длинную фразу.</p>}</div>}{['/register', '/reset-password'].includes(path) && <div><label className="field-label" htmlFor="auth-confirm">Повторите пароль</label><input id="auth-confirm" type="password" value={confirm} onChange={e => setConfirm(e.target.value)} autoComplete="new-password" maxLength={128} required aria-invalid={field === 'confirm'} /></div>}{(error || session.error) && <p role="alert" className="field-error" id="auth-error">{error || session.error}</p>}{notice && <p role="status" className="auth-notice">{notice}</p>}<Button type="submit" disabled={busy || (path === '/reset-password' && !token)}>{busy ? 'Подождите…' : action}</Button></form><nav className="auth-links" aria-label="Доступ к аккаунту">{path === '/login' ? <><Link to="/register">Создать аккаунт</Link><Link to="/forgot-password">Забыли пароль?</Link></> : <Link to="/login">Ко входу</Link>}</nav><Link className="privacy-link" to="/privacy">Конфиденциальность</Link>{session.error && <Button variant="outline" onClick={() => void auth.initialize(true)}>Повторить подключение</Button>}</section></main>
}

export function Privacy() {
  const session = useSession()
  return <main className="privacy-page"><ThemeButton /><Link className="brand" to="/"><Brand/></Link><h1>Конфиденциальность</h1><p>LectureVault хранит данные аккаунта, категории, Markdown-заметки, загруженные материалы и состояние заданий на сервере сервиса. Пароли хранятся в виде хешей Argon2id.</p><h2>Доступ к материалам</h2><p>Личные категории доступны владельцу. Общие категории видят только добавленные участники, с назначенными владельцем правами. Оператор сервера имеет технический доступ к хранилищу и резервным копиям.</p><h2>Генерация</h2><p>Подключение провайдера необязательно. При запуске генерации речь распознаётся на сервере. После распознавания видео удаляется, а SHA-256 и файл расшифровки остаются в категории. Расшифровка, текст PDF и ваши инструкции отправляются выбранному провайдеру. Модели с поддержкой изображений также получают страницы презентации. К материалам применяются также условия этого провайдера. Ручные заметки без генерации ему не отправляются.</p><h2>Подключения и браузер</h2><p>Codex использует собственный вход OpenAI по одноразовому коду. Его токены не передаются браузеру LectureVault. Ключи внешних API хранятся на сервере в зашифрованном виде. Для задания используется ключ выбранного вами провайдера. Подключения можно удалить в настройках. Браузер хранит сессионную cookie, ваши несохранённые черновики и настройки интерфейса; аналитические и рекламные cookies не используются.</p><h2>Удаление</h2><p>Заметки и категории можно удалить в библиотеке. Во вкладке «Хранилище» владельцы могут управлять файлами своих категорий. Удаление аккаунта в настройках удаляет его личные материалы, принадлежащие ему общие категории и подключения. Резервные копии могут сохраняться в пределах срока, установленного оператором; данные из них не возвращаются в рабочий аккаунт автоматически.</p>{session.supportEmail && <p>Вопросы о данных: <a href={`mailto:${session.supportEmail}`}>{session.supportEmail}</a>.</p>}<Link to="/">Вернуться в LectureVault</Link></main>
}
