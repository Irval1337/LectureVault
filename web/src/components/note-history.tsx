import { useEffect, useId, useState } from 'react'
import { api } from '../data/api'
import { QuartzArticle } from './reader'
import { Button } from './ui/button'
import { Dialog } from './ui/dialog'

type Revision = { revision: number; title: string; updatedAt: number; editor?: string | null }
type History = { revision: number; versions: Revision[] }
type Preview = { title: string; markdown: string }
const changedAt = new Intl.DateTimeFormat('ru-RU', { dateStyle: 'medium', timeStyle: 'short' })

export function NoteHistory({ noteId, onClose, onRestore }: {
  noteId: string; onClose: () => void; onRestore: (version: number, revision: number) => Promise<void>
}) {
  const inputId = useId()
  const [history, setHistory] = useState<History>()
  const [selected, setSelected] = useState<Revision>()
  const [preview, setPreview] = useState<Preview>()
  const [restoring, setRestoring] = useState(false)
  const [error, setError] = useState('')
  const endpoint = `/notes/${noteId}/revisions`

  useEffect(() => {
    let active = true
    void api<History>(endpoint).then(result => {
      if (active) {
        setHistory(result)
        setSelected(result.versions[0])
      }
    }).catch(error => { if (active) setError((error as Error).message) })
    return () => { active = false }
  }, [endpoint])

  useEffect(() => {
    if (!selected) return
    let active = true
    void api<Preview>(`${endpoint}/${selected.revision}`).then(result => {
      if (active) setPreview(result)
    }).catch(error => { if (active) setError((error as Error).message) })
    return () => { active = false }
  }, [endpoint, selected])

  async function restore() {
    if (!selected || !history || !preview || restoring) return
    setRestoring(true)
    setError('')
    try { await onRestore(selected.revision, history.revision) }
    catch (error) { setError((error as Error).message) }
    finally { setRestoring(false) }
  }

  return <Dialog open onOpenChange={open => { if (!open && !restoring) onClose() }} className="note-history"
    title="История версий" description="Восстановленная версия станет текущей. Предыдущая останется в истории.">
    {!history && !error && <p className="field-hint" role="status">Загружаем историю…</p>}
    {history?.versions.length === 0 && <p className="field-hint">У заметки пока нет предыдущих версий.</p>}
    {history && selected && <>
      <label className="field-label" htmlFor={inputId}>Версия заметки</label>
      <select id={inputId} value={selected.revision} disabled={restoring} onChange={event => {
        setPreview(undefined)
        setError('')
        setSelected(history.versions.find(item => item.revision === Number(event.target.value)))
      }}>
        {history.versions.map(item => <option key={item.revision} value={item.revision}>
          {item.revision} · {item.title} · {changedAt.format(item.updatedAt)}{item.editor ? ` · ${item.editor}` : ''}
        </option>)}
      </select>
      {preview ? <section className="note-history-preview" aria-label="Содержимое версии" tabIndex={0}>
        <h3>{preview.title}</h3>
        {preview.markdown ? <QuartzArticle preview title={preview.title} markdown={preview.markdown} /> : <p className="field-hint">Текст заметки пуст.</p>}
      </section> : !error && <p className="field-hint" role="status">Открываем версию…</p>}
    </>}
    {error && <p className="field-error" role="alert">{error}</p>}
    <div className="dialog-actions">
      <Button variant="outline" disabled={restoring} onClick={onClose}>Отмена</Button>
      {selected && <Button disabled={!preview || restoring} onClick={() => void restore()}>{restoring ? 'Восстанавливаем…' : 'Восстановить версию'}</Button>}
    </div>
  </Dialog>
}
