import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { api, ApiError } from '../data/api'
import { Button } from './ui/button'
import { Dialog } from './ui/dialog'
import { useSession } from '../data/store'

export type Provider = { id: 'codex' | 'claude'; name: string; authType?: 'deviceCode' | 'apiKey'; connected: boolean; models: { id: string; name: string; efforts: string[] }[]; login: { status: string; url?: string; userCode?: string } }
export type GenerationOptions = { provider: 'codex' | 'claude'; model: string; effort: 'low' | 'medium' | 'high' | 'xhigh' }
export const defaultGeneration: GenerationOptions = { provider: 'codex', model: 'gpt-6.1-sol', effort: 'medium' }

export function ProviderConnections({ small = false, account = false }: { small?: boolean; connectProvider?: Provider['id']; account?: boolean }) {
  const session = useSession()
  return <Link className={small ? 'inline-action' : 'help-link account-trigger'} to="/settings#connections">{account ? session.user?.name : 'Настроить подключения'}{account && <span>Настройки</span>}</Link>
}

export function ConnectionSettings() {
  const [providers, setProviders] = useState<Provider[]>([])
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [copied, setCopied] = useState(false)
  const [password, setPassword] = useState('')
  const [reauth, setReauth] = useState(false)
  const pending = useRef<(() => Promise<void>) | null>(null)
  const load = () => api<{ providers: Provider[] }>('/providers').then(result => setProviders(result.providers)).catch(error => setError(error.message))
  useEffect(() => {
    void load()
    const timer = window.setInterval(load, 4000)
    return () => window.clearInterval(timer)
  }, [])
  const action = async (provider: string, command: string, key?: string) => {
    setBusy(provider); setError('')
    try {
      await api(`/providers/${provider}/${command}`, { method: 'POST', body: JSON.stringify(command === 'key' ? { apiKey: key } : {}) })
      if (command === 'key') setApiKey('')
      setCopied(false); await load()
    } catch (error) {
      if (error instanceof ApiError && error.status === 428) { pending.current = () => action(provider, command, key); setReauth(true) }
      else setError((error as Error).message)
    } finally { setBusy('') }
  }
  const confirmPassword = async (event: FormEvent) => {
    event.preventDefault(); setBusy('reauth'); setError('')
    try {
      await api('/auth/reauth', { method: 'POST', body: JSON.stringify({ password }) })
      setPassword(''); setReauth(false); await pending.current?.(); pending.current = null
    } catch (error) { setError((error as Error).message) }
    finally { setBusy('') }
  }
  return <section id="connections" className="settings-section"><h2>Подключения</h2><p className="field-hint">Для генерации нужен собственный аккаунт провайдера. Ручные заметки работают без подключения.</p><div className="provider-connections">{!providers.length && <p role="status">Проверяем подключения…</p>}{providers.map(provider => <section key={provider.id} className="provider-row"><div className="provider-heading"><div><strong>{provider.name}</strong><span role="status">{provider.connected ? provider.id === 'claude' ? 'Ключ сохранён' : 'Подключён' : 'Не подключён'}</span></div>{provider.connected && <Button size="sm" variant="outline" disabled={!!busy} onClick={() => { if (window.confirm(`Отключить ${provider.name}? Ваши заметки сохранятся.`)) void action(provider.id, 'disconnect') }}>Отключить</Button>}</div>{!provider.connected && (provider.id === 'codex' ? <><p className="field-hint">Вход с вашим аккаунтом ChatGPT на официальной странице OpenAI.</p><Button size="sm" variant="outline" disabled={!!busy} onClick={() => void action('codex', ['starting', 'waiting'].includes(provider.login.status) ? 'disconnect' : 'connect')}>{['starting', 'waiting'].includes(provider.login.status) ? 'Отменить вход' : 'Подключить Codex'}</Button>{provider.login.url && ['starting', 'waiting'].includes(provider.login.status) && <div className="provider-login"><span className="field-label">Одноразовый код</span><div className="device-code-row"><code className="device-code">{provider.login.userCode || 'Получаем код…'}</code><Button size="sm" variant="ghost" disabled={!provider.login.userCode} onClick={() => { void navigator.clipboard.writeText(provider.login.userCode!).then(() => setCopied(true)).catch(() => setError('Выделите код и скопируйте его вручную.')) }}>{copied ? 'Скопировано' : 'Скопировать'}</Button></div><a href={provider.login.url} target="_blank" rel="noopener noreferrer">Открыть вход OpenAI ↗</a><p className="field-hint">Введите код на странице OpenAI. Статус здесь обновится автоматически.</p></div>}{['failed', 'expired'].includes(provider.login.status) && <p className="field-error">Вход не завершён. Начните его заново.</p>}<details className="connection-help"><summary>Не получается войти?</summary><p>OpenAI может попросить разрешить вход по коду в настройках безопасности ChatGPT. Это настройка вашего аккаунта OpenAI.</p><a href="https://learn.chatgpt.com/docs/auth" target="_blank" rel="noopener noreferrer">Инструкция OpenAI ↗</a></details></> : <form className="provider-key-form" onSubmit={event => { event.preventDefault(); void action('claude', 'key', apiKey) }}><p className="field-hint">Собственный ключ Anthropic API. Использование оплачивается отдельно от подписки Claude.</p><label className="field-label" htmlFor="anthropic-key">Ключ Anthropic API</label><input id="anthropic-key" type="password" value={apiKey} autoComplete="off" spellCheck={false} maxLength={512} placeholder="sk-ant-…" onChange={event => setApiKey(event.target.value)} required /><Button size="sm" variant="outline" disabled={!apiKey.trim() || !!busy} type="submit">Сохранить ключ</Button></form>)}{provider.login.status === 'unavailable' && <p className="field-error">Подключения временно недоступны. Заметками можно пользоваться.</p>}</section>)}{error && <p role="alert" className="field-error">{error}</p>}</div><Dialog open={reauth} onOpenChange={value => { setReauth(value); if (!value) { setPassword(''); pending.current = null } }} title="Подтвердите пароль" description="Подключение провайдера изменяет доступ к вашему аккаунту."><form className="auth-form" onSubmit={confirmPassword}><label className="field-label" htmlFor="reauth-password">Пароль LectureVault</label><input id="reauth-password" type="password" value={password} onChange={event => setPassword(event.target.value)} autoComplete="current-password" required />{error && <p role="alert" className="field-error">{error}</p>}<Button disabled={!!busy} type="submit">Продолжить</Button></form></Dialog></section>
}

export function ProviderOptions({ value, onChange, onAvailabilityChange }: { value: GenerationOptions; onChange: (value: GenerationOptions) => void; onAvailabilityChange?: (available: boolean) => void }) {
  const [providers, setProviders] = useState<Provider[]>([])
  const [error, setError] = useState('')
  useEffect(() => { void api<{ providers: Provider[] }>('/providers').then(result => setProviders(result.providers)).catch(error => setError(error.message)) }, [])
  const provider = providers.find(p => p.id === value.provider)
  const model = provider?.models.find(m => m.id === value.model)
  useEffect(() => { onAvailabilityChange?.(!!provider?.connected) }, [provider?.connected, value.provider])
  return <div className="provider-options"><div className="field-label-row"><label className="field-label" htmlFor="generation-provider">Провайдер</label><ProviderConnections small /></div><select id="generation-provider" value={value.provider} onChange={event => {
    const next = providers.find(p => p.id === event.target.value)
    if (next) onChange({ provider: next.id, model: next.models[0].id, effort: 'medium' })
  }}>{providers.length ? providers.map(p => <option key={p.id} value={p.id}>{p.name}{p.connected ? '' : ' · не подключён'}</option>) : <option value={value.provider}>{value.provider === 'codex' ? 'Codex' : 'Claude'}</option>}</select>{provider && !provider.connected && <p className="field-hint">Сначала подключите {provider.name} в настройках. <Link to="/settings#connections">Открыть настройки</Link></p>}<div className="provider-model-effort"><div><label className="field-label" htmlFor="generation-model">Модель</label><select id="generation-model" value={value.model} onChange={event => {
    const next = provider?.models.find(m => m.id === event.target.value)
    if (next) onChange({ ...value, model: next.id, effort: next.efforts.includes(value.effort) ? value.effort : 'medium' })
  }}>{provider?.models.map(m => <option key={m.id} value={m.id}>{m.name}</option>) ?? <option value={value.model}>{value.model}</option>}</select></div><div><label className="field-label" htmlFor="generation-effort">Effort</label><select id="generation-effort" value={value.effort} onChange={event => onChange({ ...value, effort: event.target.value as GenerationOptions['effort'] })}>{(model?.efforts ?? ['low', 'medium', 'high']).map(e => <option key={e} value={e}>{{ low: 'Низкий', medium: 'Средний', high: 'Высокий', xhigh: 'Очень высокий' }[e]}</option>)}</select></div></div>{error && <p role="alert" className="field-error">{error}</p>}</div>
}
