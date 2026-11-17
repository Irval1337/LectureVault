import { useEffect, useRef, useState } from 'react'
import { api } from '../data/api'
import { Button } from './ui/button'
import { Dialog } from './ui/dialog'
import { auth, library, useSession } from '../data/store'

export type Provider = { id: 'codex' | 'claude'; name: string; connected: boolean; models: { id: string; name: string; efforts: string[] }[]; login: { status: string; url?: string } }
export type GenerationOptions = { provider: 'codex' | 'claude'; model: string; effort: 'low' | 'medium' | 'high' | 'xhigh' }
export const defaultGeneration: GenerationOptions = { provider: 'codex', model: 'gpt-6.1-sol', effort: 'medium' }

export function ProviderConnections({ small = false, connectProvider, account = false }: { small?: boolean; connectProvider?: Provider['id']; account?: boolean }) {
  const session = useSession()
  const [open, setOpen] = useState(false)
  const [providers, setProviders] = useState<Provider[]>([])
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [code, setCode] = useState('')
  const popup = useRef<{ provider: string; window: Window; navigated: boolean } | null>(null)
  const load = () => api<{ providers: Provider[] }>('/providers').then(result => {
    setProviders(result.providers)
    const pending = popup.current
    const provider = result.providers.find(p => p.id === pending?.provider)
    if (pending && provider?.connected) { pending.window.close(); popup.current = null }
    else if (pending && provider?.login.url && !pending.navigated && !pending.window.closed) {
      pending.navigated = true; pending.window.location.href = provider.login.url
    }
  }).catch(error => setError(error.message))
  useEffect(() => {
    if (!open) return
    setError(''); void load()
    const timer = window.setInterval(load, 3000)
    return () => { window.clearInterval(timer); window.dispatchEvent(new Event('lecturevault:connections')) }
  }, [open, session.user?.id])
  const action = async (provider: string, command: string) => {
    if (command === 'connect') {
      const window = globalThis.window.open('about:blank', '_blank')
      if (window) { window.opener = null; window.document.title = 'Вход в LectureVault'; window.document.body.textContent = 'Открываем страницу входа…'; popup.current = { provider, window, navigated: false } }
    }
    setBusy(provider); setError('')
    try {
      await api(`/providers/${provider}/${command}`, { method: 'POST', body: JSON.stringify(command === 'code' ? { code: code.trim() } : {}) })
      if (command === 'code') setCode('')
      await load()
    } catch (error) { setError((error as Error).message); popup.current?.window.close(); popup.current = null }
    finally { setBusy('') }
  }
  return <><button type="button" className={small ? 'inline-action' : 'help-link account-trigger'} onClick={() => { setOpen(true); if (connectProvider) void action(connectProvider, 'connect') }}>{account ? session.user?.name : connectProvider ? `Войти в ${connectProvider === 'codex' ? 'Codex' : 'Claude'}` : 'Подключения'}{account && <span>Аккаунт</span>}</button><Dialog open={open} onOpenChange={setOpen} title="Подключения" description="Войдите в аккаунт для генерации конспектов."><div className="provider-connections">{!providers.length && <p role="status">Проверяем подключения…</p>}{providers.map(provider => <section key={provider.id} className="provider-row"><div className="provider-heading"><div><strong>{provider.name}</strong><span role="status">{provider.connected ? 'Подключён' : 'Не подключён'}</span></div>{provider.connected ? <button className="inline-action" disabled={!!busy} onClick={() => void action(provider.id, 'disconnect')}>Отключить</button> : <Button size="sm" variant="outline" disabled={!!busy} onClick={() => void action(provider.id, 'connect')}>{busy === provider.id || ['starting', 'waiting'].includes(provider.login.status) ? 'Продолжить вход' : 'Войти'}</Button>}</div>{!provider.connected && <>{provider.login.url && ['starting', 'waiting'].includes(provider.login.status) && <div className="provider-login"><a href={provider.login.url} target="_blank" rel="noopener noreferrer">Продолжить на сайте {provider.name} ↗</a>{provider.id === 'codex' ? <p className="field-hint">Подтвердите вход в открывшейся вкладке. Здесь статус обновится автоматически.</p> : <><label className="field-label" htmlFor="claude-auth-code">Код подтверждения со страницы Claude</label><input id="claude-auth-code" value={code} onChange={event => setCode(event.target.value)} autoComplete="off" maxLength={1000} /><Button size="sm" disabled={!code.trim() || !!busy} onClick={() => void action('claude', 'code')}>Подтвердить</Button></>}</div>}{['failed', 'expired'].includes(provider.login.status) && <p className="field-error">Вход не завершён. Попробуйте ещё раз.</p>}{provider.login.status === 'unavailable' && <p className="field-error">Сервис подключения недоступен. Проверьте запуск приложения.</p>}</>}</section>)}{error && <p role="alert" className="field-error">{error}</p>}<p className="field-hint">Каждый пользователь подключает свой аккаунт. Модель выбирается при создании конспекта.</p>{account && <div className="account-signout"><span>{session.user?.name} · демопрофиль</span><button className="inline-action" onClick={() => { setOpen(false); void auth.logout().catch(library.reportError) }}>Выйти из LectureVault</button></div>}</div></Dialog></>
}

export function ProviderOptions({ value, onChange }: { value: GenerationOptions; onChange: (value: GenerationOptions) => void }) {
  const [providers, setProviders] = useState<Provider[]>([])
  useEffect(() => {
    const load = () => { void api<{ providers: Provider[] }>('/providers').then(result => setProviders(result.providers)).catch(() => setProviders([])) }
    load(); window.addEventListener('lecturevault:connections', load)
    return () => window.removeEventListener('lecturevault:connections', load)
  }, [])
  const provider = providers.find(p => p.id === value.provider)
  const model = provider?.models.find(m => m.id === value.model)
  return <div className="provider-options"><div className="field-label-row"><label className="field-label" htmlFor="generation-provider">Провайдер</label><ProviderConnections small connectProvider={provider && !provider.connected ? provider.id : undefined} /></div><select id="generation-provider" value={value.provider} onChange={event => {
    const next = providers.find(p => p.id === event.target.value)
    if (next) onChange({ provider: next.id, model: next.models[0].id, effort: 'medium' })
  }}>{providers.length ? providers.map(p => <option key={p.id} value={p.id}>{p.name}{p.connected ? ' · подключён' : ' · нужен вход'}</option>) : <option value={value.provider}>{value.provider === 'codex' ? 'Codex' : 'Claude'}</option>}</select><div className="provider-model-effort"><div><label className="field-label" htmlFor="generation-model">Модель</label><select id="generation-model" value={value.model} onChange={event => {
    const next = provider?.models.find(m => m.id === event.target.value)
    if (next) onChange({ ...value, model: next.id, effort: next.efforts.includes(value.effort) ? value.effort : 'medium' })
  }}>{provider?.models.map(m => <option key={m.id} value={m.id}>{m.name}</option>) ?? <option value={value.model}>{value.model}</option>}</select></div><div><label className="field-label" htmlFor="generation-effort">Effort</label><select id="generation-effort" value={value.effort} onChange={event => onChange({ ...value, effort: event.target.value as GenerationOptions['effort'] })}>{(model?.efforts ?? ['low', 'medium', 'high']).map(e => <option key={e} value={e}>{{ low: 'Низкий', medium: 'Средний', high: 'Высокий', xhigh: 'Очень высокий' }[e]}</option>)}</select></div></div></div>
}
