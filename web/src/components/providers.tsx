import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, NavLink } from 'react-router-dom'
import { Settings2 } from 'lucide-react'
import { api, ApiError } from '../data/api'
import { PROVIDER_NAMES, type ProviderId } from '../data/model'
import { Button } from './ui/button'
import { Dialog } from './ui/dialog'
import { useSession } from '../data/store'

export type Provider = { id: ProviderId; name: string; connected: boolean; managedBy?: string; baseUrl?: string; catalogError?: string; models: { id: string; name: string; efforts: string[] }[]; login: { status: string; url?: string; userCode?: string } }
export type GenerationOptions = { apiBase?: string; provider: ProviderId; model: string; effort: 'low' | 'medium' | 'high' | 'xhigh' }
export const defaultGeneration: GenerationOptions = { provider: 'codex', model: '', effort: 'medium' }
const endpoints = ['https://api.openai.com/v1', 'https://api.anthropic.com/v1', 'https://generativelanguage.googleapis.com/v1beta/openai', 'http://host.docker.internal:11434/v1']

export function ProviderConnections({ small = false, account = false }: { small?: boolean; connectProvider?: Provider['id']; account?: boolean }) {
  const session = useSession()
  return <NavLink className={small ? 'inline-action' : 'help-link account-trigger'} to={account ? '/settings' : '/settings#connections'} title={account ? 'Настройки аккаунта' : undefined}>
    {account ? <><span className="account-name">{session.user?.name}</span><Settings2 size={15} aria-hidden="true" /><span className="sr-only">Настройки</span></> : 'Настроить подключения'}
  </NavLink>
}

export function ConnectionSettings() {
  const [providers, setProviders] = useState<Provider[]>([])
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [activeDialog, setActiveDialog] = useState<Provider['id'] | null>(null)
  const [baseUrl, setBaseUrl] = useState(endpoints[0])
  const [workspace, setWorkspace] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [copied, setCopied] = useState(false)
  const [password, setPassword] = useState('')
  const [reauth, setReauth] = useState(false)
  const pending = useRef<(() => Promise<void>) | null>(null)
  const load = () => api<{ providers: Provider[] }>('/providers')
    .then(result => setProviders(result.providers))
    .catch(error => setError(error.message))
  useEffect(() => {
    void load()
    if (activeDialog !== 'codex') return
    const timer = window.setInterval(load, 4000)
    return () => window.clearInterval(timer)
  }, [activeDialog])
  const codex = providers.find(provider => provider.id === 'codex')
  useEffect(() => {
    if (activeDialog === 'codex' && codex?.connected) {
      setActiveDialog(null); setNotice('Codex подключён.')
    }
  }, [codex?.connected, activeDialog])

  const closeDialog = () => {
    setActiveDialog(null); setApiKey(''); setCopied(false); setError('')
  }
  const action = async (provider: ProviderId, command: 'key' | 'connect' | 'disconnect', key?: string) => {
    setBusy(provider); setError(''); setNotice('')
    try {
      await api(`/providers/${provider}/${command}`, { method: 'POST', body: JSON.stringify(command === 'key' ? { apiKey: key ?? '', baseUrl: provider === 'claude' ? endpoints[1] : baseUrl.trim(), workspace: provider === 'claude' ? '' : workspace.trim() } : {}) })
      if (command === 'key') { setApiKey(''); setActiveDialog(null); setNotice('Подключение сохранено.') }
      if (command === 'disconnect') { setActiveDialog(null); setNotice(provider === 'openai' ? 'API отключён.' : `${PROVIDER_NAMES[provider]} отключён.`) }
      setCopied(false); await load()
    } catch (error) {
      if (error instanceof ApiError && error.status === 428) { pending.current = () => action(provider, command, key); setReauth(true) }
      else setError((error as Error).message)
    } finally { setBusy('') }
  }
  const openConnection = (provider: Provider) => {
    setError(''); setNotice(''); setApiKey(''); setBaseUrl(provider.baseUrl || endpoints[0]); setWorkspace(''); setActiveDialog(provider.id)
    if (provider.id === 'codex' && !['starting', 'waiting'].includes(provider.login.status)) void action('codex', 'connect')
  }
  const confirmPassword = async (event: FormEvent) => {
    event.preventDefault(); setBusy('reauth'); setError('')
    try {
      await api('/auth/reauth', { method: 'POST', body: JSON.stringify({ password }) })
      setPassword(''); setReauth(false); await pending.current?.(); pending.current = null
    } catch (error) { setError((error as Error).message) }
    finally { setBusy('') }
  }
  const waiting = codex && ['starting', 'waiting'].includes(codex.login.status)
  return <section id="connections" className="settings-section connection-settings">
    <h2 className="sr-only">Подключения</h2>
    <div className="provider-connections">
      {!providers.length && !error && <p role="status" className="field-hint">Проверяем подключения…</p>}
      {providers.map(provider => <div key={provider.id} className="provider-row">
        <div className="provider-heading">
          <strong>{provider.name}</strong>
          <span role="status" className={provider.connected ? 'provider-connected' : ''}>
            {provider.connected ? 'Подключён' : provider.id === 'codex' && waiting ? 'Ожидаем входа' : provider.login.status === 'unavailable' ? 'Недоступен' : 'Не подключён'}
          </span>
        </div>
        <p className="provider-description">{provider.id === 'codex' ? 'Аккаунт ChatGPT' : provider.id === 'claude' ? (provider.managedBy ? 'Использует ключ из «Свой API»' : 'Ключ Anthropic API') : provider.baseUrl || 'OpenAI, Claude, Gemini или локальная модель'}</p>
        {provider.managedBy ? <Button size="sm" variant="ghost" onClick={() => { const owner = providers.find(p => p.id === provider.managedBy); if (owner) openConnection(owner) }}>Настроить ключ</Button> : provider.connected
          ? <div className="provider-actions">{provider.id === 'openai' && <Button size="sm" variant="outline" disabled={!!busy} onClick={() => openConnection(provider)}>Изменить</Button>}<Button size="sm" variant="ghost" disabled={!!busy} onClick={() => { if (window.confirm(`Отключить ${provider.id === 'openai' ? 'API' : provider.name}?`)) void action(provider.id, 'disconnect') }}>Отключить</Button></div>
          : <Button size="sm" variant="outline" disabled={!!busy || provider.login.status === 'unavailable'} onClick={() => openConnection(provider)} aria-label={provider.id === 'openai' ? 'Подключить API' : `Подключить ${provider.name}`}>
            {provider.id === 'codex' && waiting ? 'Продолжить вход' : 'Подключить'}
          </Button>}
      </div>)}
      {error && !activeDialog && !reauth && <p role="alert" className="field-error">{error}</p>}
      {notice && <p role="status" className="auth-notice">{notice}</p>}
    </div>
    <Dialog open={!!activeDialog} onOpenChange={open => { if (!open) closeDialog() }} title={activeDialog === 'openai' ? 'Настроить API' : activeDialog === 'claude' ? 'Подключить Claude Code' : 'Подключить Codex'} description={activeDialog === 'openai' ? 'Сервер с поддержкой OpenAI Chat Completions.' : activeDialog === 'claude' ? 'Используется ключ из консоли Anthropic.' : 'Подтвердите вход в ваш аккаунт на странице OpenAI.'} className="connection-dialog">
      {activeDialog === 'codex' && <>
        {codex?.login.url && waiting ? <div className="provider-login">
          <div className="device-code-row"><code className="device-code" aria-label="Одноразовый код">{codex.login.userCode || 'Получаем код…'}</code><Button size="sm" variant="ghost" disabled={!codex.login.userCode} onClick={() => { void navigator.clipboard.writeText(codex.login.userCode!).then(() => setCopied(true)).catch(() => setError('Выделите код и скопируйте его вручную.')) }}>{copied ? 'Скопировано' : 'Скопировать'}</Button></div>
          <Button asChild><a href={codex.login.url} target="_blank" rel="noopener noreferrer">Открыть OpenAI</a></Button>
          <p className="field-hint">Введите этот код на странице OpenAI. После подтверждения окно закроется.</p>
        </div> : <p role="status" className="field-hint">{['failed', 'expired'].includes(codex?.login.status ?? '') ? 'Вход не завершён. Попробуйте ещё раз.' : 'Получаем код входа…'}</p>}
        {['failed', 'expired'].includes(codex?.login.status ?? '') && <Button size="sm" disabled={!!busy} onClick={() => void action('codex', 'connect')}>Повторить вход</Button>}
        <details className="connection-help"><summary>Не получается войти?</summary><p>OpenAI может попросить разрешить вход по коду в настройках безопасности ChatGPT.</p><a href="https://learn.chatgpt.com/docs/auth" target="_blank" rel="noopener noreferrer">Инструкция OpenAI</a></details>
        <div className="dialog-actions"><Button variant="ghost" disabled={!!busy} onClick={() => void action('codex', 'disconnect')}>Отменить вход</Button></div>
      </>}
      {(activeDialog === 'openai' || activeDialog === 'claude') && <form className="provider-key-form" onSubmit={event => { event.preventDefault(); void action(activeDialog, 'key', apiKey.trim()) }}>
        {activeDialog === 'openai' && <><label className="field-label" htmlFor="provider-base">Адрес API (Base URL)</label>
        <input id="provider-base" type="url" list="api-endpoints" value={baseUrl} onChange={event => { setBaseUrl(event.target.value); setApiKey('') }} maxLength={500} required />
        <datalist id="api-endpoints">{endpoints.map((url,index) => <option key={url} value={url}>{['OpenAI','Anthropic','Gemini','Ollama'][index]}</option>)}</datalist></>}
        <label className="field-label" htmlFor="provider-api-key">API-ключ</label>
        <input id="provider-api-key" type="password" value={apiKey} autoComplete="off" spellCheck={false} maxLength={512} onChange={event => setApiKey(event.target.value)} />
        {activeDialog === 'openai' && baseUrl.startsWith('http://') && <p className="field-hint">Для локального сервера без авторизации оставьте ключ пустым. Адрес должен быть разрешён владельцем LectureVault.</p>}
        {activeDialog === 'openai' && baseUrl.replace(/\/+$/, '') === endpoints[1] && <><label className="field-label" htmlFor="api-workspace">Anthropic Workspace ID · при необходимости</label><input id="api-workspace" value={workspace} maxLength={100} onChange={event => setWorkspace(event.target.value)}/></>}
        <div className="dialog-actions"><Button type="button" variant="ghost" onClick={closeDialog}>Отмена</Button><Button size="sm" disabled={(activeDialog === 'claude' ? !apiKey.trim() : !baseUrl.trim()) || !!busy} type="submit">Сохранить подключение</Button></div>
      </form>}
      {error && !reauth && <p role="alert" className="field-error">{error}</p>}
    </Dialog>
    <Dialog open={reauth} onOpenChange={value => { setReauth(value); if (!value) { setPassword(''); pending.current = null } }} title="Подтвердите пароль" description="Для изменения подключения введите пароль LectureVault.">
      <form className="auth-form" onSubmit={confirmPassword}><label className="field-label" htmlFor="reauth-password">Пароль LectureVault</label><input id="reauth-password" type="password" value={password} onChange={event => setPassword(event.target.value)} autoComplete="current-password" required />{error && <p role="alert" className="field-error">{error}</p>}<Button disabled={!!busy} type="submit">Продолжить</Button></form>
    </Dialog>
  </section>
}

export function ProviderOptions({ value, onChange, onAvailabilityChange }: { value: GenerationOptions; onChange: (value: GenerationOptions) => void; onAvailabilityChange?: (available: boolean) => void }) {
  const [providers, setProviders] = useState<Provider[]>([])
  const [error, setError] = useState('')
  useEffect(() => { void api<{ providers: Provider[] }>('/providers').then(result => setProviders(result.providers)).catch(error => setError(error.message)) }, [])
  const provider = providers.find(item => item.id === value.provider)
  const model = provider?.models.find(item => item.id === value.model)
  useEffect(() => {
    if (!providers.length) return
    const choice = provider || providers[0]
    const next = model || choice.models[0]
    if (next && (!model || !provider || choice.baseUrl !== value.apiBase || (next.efforts.length > 0 && !next.efforts.includes(value.effort)))) {
      onChange({ provider: choice.id, apiBase: choice.baseUrl, model: next.id, effort: next.efforts.includes('medium') ? 'medium' : (next.efforts[0] as GenerationOptions['effort']) || 'medium' })
    } else if (!provider) onChange({ provider: choice.id, model: '', effort: 'medium' })
  }, [providers, provider, model])
  const manual = provider?.connected && !provider.models.length && provider.id !== 'codex'
  useEffect(() => { onAvailabilityChange?.(!!provider?.connected && (!!model || (!!manual && !!value.model.trim()))) }, [provider, model, manual, value.model])
  return <div className="provider-options">
    <div className="provider-picker"><label className="field-label" htmlFor="generation-provider">Провайдер</label>
      <select id="generation-provider" value={value.provider} onChange={event => {
        const next = providers.find(item => item.id === event.target.value)
        if (next) onChange({ provider: next.id, apiBase: next.baseUrl, model: next.models[0]?.id ?? '', effort: 'medium' })
      }}>{providers.length ? providers.map(item => <option key={item.id} value={item.id}>{item.name}</option>) : <option>{PROVIDER_NAMES[value.provider]}</option>}</select>
      {provider && !provider.connected && <p className="field-hint"><Link to="/settings#connections" state={{ fromGeneration: true }}>Подключить в настройках</Link></p>}
      {provider?.baseUrl && <p className="field-hint">{provider.baseUrl}</p>}
    </div>
    {provider?.connected && <div className="provider-model-effort"><div><label className="field-label" htmlFor="generation-model">Модель</label>
      {manual ? <input id="generation-model" maxLength={180} value={value.model} placeholder="Идентификатор модели" onChange={event => onChange({ ...value, apiBase: provider.baseUrl, model: event.target.value })} /> :
      <select id="generation-model" value={value.model} onChange={event => {
        const next = provider.models.find(item => item.id === event.target.value)
        if (next) onChange({ ...value, model: next.id, effort: next.efforts.includes(value.effort) ? value.effort : (next.efforts[0] as GenerationOptions['effort']) || 'medium' })
      }}>{provider.models.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select>}
      {provider.catalogError && <p className="field-hint">{provider.catalogError} {manual && 'Введите идентификатор вручную.'}</p>}</div>
      {!!model?.efforts.length && <div><label className="field-label" htmlFor="generation-effort">Глубина рассуждения</label><select id="generation-effort" value={value.effort} onChange={event => onChange({ ...value, effort: event.target.value as GenerationOptions['effort'] })}>{model.efforts.map(effort => <option key={effort} value={effort}>{{low:'Низкая',medium:'Средняя',high:'Высокая',xhigh:'Максимальная'}[effort]}</option>)}</select></div>}</div>}
    {error && <p role="alert" className="field-error">{error}</p>}
  </div>
}
