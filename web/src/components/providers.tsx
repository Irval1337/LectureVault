import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, NavLink } from 'react-router-dom'
import { Settings2 } from 'lucide-react'
import { api, ApiError } from '../data/api'
import { Button } from './ui/button'
import { Dialog } from './ui/dialog'
import { useSession } from '../data/store'

export type Provider = { id: 'codex' | 'claude'; name: string; authType?: 'deviceCode' | 'apiKey'; connected: boolean; models: { id: string; name: string; efforts: string[] }[]; login: { status: string; url?: string; userCode?: string } }
export type GenerationOptions = { provider: 'codex' | 'claude'; model: string; effort: 'low' | 'medium' | 'high' | 'xhigh' }
export const defaultGeneration: GenerationOptions = { provider: 'codex', model: 'gpt-6.1-sol', effort: 'medium' }

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
    const timer = window.setInterval(load, 4000)
    return () => window.clearInterval(timer)
  }, [])
  const codex = providers.find(provider => provider.id === 'codex')
  useEffect(() => {
    if (activeDialog === 'codex' && codex?.connected) {
      setActiveDialog(null); setNotice('Codex подключён.')
    }
  }, [codex?.connected, activeDialog])

  const closeDialog = () => {
    setActiveDialog(null); setApiKey(''); setCopied(false); setError('')
  }
  const action = async (provider: string, command: string, key?: string) => {
    setBusy(provider); setError(''); setNotice('')
    try {
      await api(`/providers/${provider}/${command}`, { method: 'POST', body: JSON.stringify(command === 'key' ? { apiKey: key } : {}) })
      if (command === 'key') { setApiKey(''); setActiveDialog(null); setNotice('Ключ Claude сохранён.') }
      if (command === 'disconnect') { setActiveDialog(null); setNotice(provider === 'codex' ? 'Подключение Codex отключено.' : 'Ключ Claude удалён.') }
      setCopied(false); await load()
    } catch (error) {
      if (error instanceof ApiError && error.status === 428) { pending.current = () => action(provider, command, key); setReauth(true) }
      else setError((error as Error).message)
    } finally { setBusy('') }
  }
  const openConnection = (provider: Provider) => {
    setError(''); setNotice(''); setActiveDialog(provider.id)
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
    <p className="settings-intro">Подключите свой аккаунт для генерации из видео. Писать заметки можно без AI.</p>
    <div className="provider-connections">
      {!providers.length && <p role="status" className="field-hint">Проверяем подключения…</p>}
      {providers.map(provider => <div key={provider.id} className="provider-row">
        <div className="provider-heading">
          <strong>{provider.name}</strong>
          <span role="status" className={provider.connected ? 'provider-connected' : ''}>
            {provider.connected ? provider.id === 'claude' ? 'Ключ сохранён' : 'Подключён' : provider.id === 'codex' && waiting ? 'Ожидаем входа' : provider.login.status === 'unavailable' ? 'Недоступен' : 'Не подключён'}
          </span>
        </div>
        <p className="provider-description">{provider.id === 'codex' ? 'Аккаунт ChatGPT' : 'Anthropic API · отдельная оплата'}</p>
        {provider.connected
          ? <Button size="sm" variant="ghost" disabled={!!busy} onClick={() => { if (window.confirm(`Отключить ${provider.name}? Ваши заметки сохранятся.`)) void action(provider.id, 'disconnect') }}>Отключить</Button>
          : <Button size="sm" variant="outline" disabled={!!busy || provider.login.status === 'unavailable'} onClick={() => openConnection(provider)} aria-label={`Подключить ${provider.name}`}>
            {provider.id === 'codex' && waiting ? 'Продолжить вход' : 'Подключить'}
          </Button>}
      </div>)}
      {error && !activeDialog && !reauth && <p role="alert" className="field-error">{error}</p>}
      {notice && <p role="status" className="auth-notice">{notice}</p>}
    </div>
    <Dialog open={!!activeDialog} onOpenChange={open => { if (!open) closeDialog() }} title={activeDialog === 'claude' ? 'Подключить Claude' : 'Подключить Codex'} description={activeDialog === 'claude' ? 'Используйте собственный ключ Anthropic API.' : 'Подтвердите вход в ваш аккаунт на странице OpenAI.'} className="connection-dialog">
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
      {activeDialog === 'claude' && <form className="provider-key-form" onSubmit={event => { event.preventDefault(); void action('claude', 'key', apiKey) }}>
        <label className="field-label" htmlFor="anthropic-key">Ключ Anthropic API</label>
        <input id="anthropic-key" type="password" value={apiKey} autoComplete="off" spellCheck={false} maxLength={512} placeholder="sk-ant-…" onChange={event => setApiKey(event.target.value)} required />
        <p className="field-hint">API оплачивается отдельно от подписки Claude. Сохранённый ключ не отображается в браузере.</p>
        <div className="dialog-actions"><Button type="button" variant="ghost" onClick={closeDialog}>Отмена</Button><Button size="sm" disabled={!apiKey.trim() || !!busy} type="submit">Сохранить ключ</Button></div>
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
  useEffect(() => { onAvailabilityChange?.(!!provider?.connected) }, [provider?.connected, value.provider])
  return <div className="provider-options">
    <div className="provider-picker">
      <label className="field-label" htmlFor="generation-provider">Провайдер</label>
      <select id="generation-provider" value={value.provider} onChange={event => {
        const next = providers.find(item => item.id === event.target.value)
        if (next) onChange({ provider: next.id, model: next.models[0].id, effort: 'medium' })
      }}>{providers.length ? providers.map(item => <option key={item.id} value={item.id}>{item.name}</option>) : <option value={value.provider}>{value.provider === 'codex' ? 'Codex' : 'Claude'}</option>}</select>
      {provider && !provider.connected && <p className="field-hint">{provider.name} не подключён. <Link to="/settings#connections">Подключить в настройках</Link></p>}
    </div>
    <div className="provider-model-effort">
      <div><label className="field-label" htmlFor="generation-model">Модель</label><select id="generation-model" value={value.model} onChange={event => {
        const next = provider?.models.find(item => item.id === event.target.value)
        if (next) onChange({ ...value, model: next.id, effort: next.efforts.includes(value.effort) ? value.effort : 'medium' })
      }}>{provider?.models.map(item => <option key={item.id} value={item.id}>{item.name}</option>) ?? <option value={value.model}>{value.model}</option>}</select></div>
      <div><label className="field-label" htmlFor="generation-effort">Effort</label><select id="generation-effort" value={value.effort} onChange={event => onChange({ ...value, effort: event.target.value as GenerationOptions['effort'] })}>{(model?.efforts ?? ['low', 'medium', 'high']).map(effort => <option key={effort} value={effort}>{{ low: 'Низкий', medium: 'Средний', high: 'Высокий', xhigh: 'Очень высокий' }[effort]}</option>)}</select></div>
    </div>
    {error && <p role="alert" className="field-error">{error}</p>}
  </div>
}
