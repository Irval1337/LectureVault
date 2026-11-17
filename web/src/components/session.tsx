import { useEffect, type ReactNode } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { BookOpenText } from 'lucide-react'
import { auth, useSession } from '../data/store'
import { Button } from './ui/button'

export function SessionGate({ children }: { children: ReactNode }) {
  const session = useSession()
  const location = useLocation()
  useEffect(() => { void auth.initialize() }, [])
  if (location.pathname === '/demo' || location.pathname.startsWith('/demo/')) return children
  if (session.user && !session.loading) return children
  return <main className="session-page"><div className="session-panel"><div className="brand"><BookOpenText size={22} /><span>LectureVault</span></div><h1>Вход</h1><p>Локальная демонстрация. Выберите пользователя.</p>{session.error && <p className="field-error" role="alert">{session.error}</p>}{session.loading ? <p role="status">Подключаем библиотеку…</p> : <><div className="session-users">{session.users.map(user => <Button key={user.id} variant="outline" onClick={() => void auth.login(user.id)}>{user.name}</Button>)}</div>{session.error && <Button variant="outline" onClick={() => void auth.initialize(true)}>Повторить подключение</Button>}<Link to="/demo">Открыть примеры без входа</Link><small>Демо-вход предназначен для запуска на localhost.</small></>}</div></main>
}
