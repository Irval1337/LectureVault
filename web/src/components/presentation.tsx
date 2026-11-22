import { useEffect, useState } from 'react'
import { ChevronLeft, ChevronRight, Download } from 'lucide-react'
import { api } from '../data/api'

export function Presentation({ asset, name, embedded }: { asset: string; name: string; embedded: boolean }) {
  const [open, setOpen] = useState(!embedded || window.location.hash === '#presentation')
  const [page, setPage] = useState(1)
  const [pages, setPages] = useState(0)
  const [error, setError] = useState(false)
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    if (!open || pages) return
    let current = true
    setError(false)
    api<{ pages: number }>(`/assets/${asset}/slides`).then(result => { if (current) setPages(result.pages) })
      .catch(() => { if (current) setError(true) })
    return () => { current = false }
  }, [asset, attempt, open, pages])
  return <section id="presentation" className="presentation" aria-label="Слайды презентации">
    <header><h2>Презентация</h2><a href={`/api/assets/${asset}`} download title={name}><Download size={15} />Скачать PDF</a></header>
    <p title={name}>{name}</p>
    <button type="button" className="inline-action slide-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? 'Свернуть слайды' : 'Показать все слайды'}</button>
    {open && <div className="slide-viewer">
      <div className="slide-frame">
        {error ? <p role="alert">Не удалось загрузить слайд. <button className="inline-action" onClick={() => { setError(false); setPages(0); setAttempt(v => v + 1) }}>Повторить</button></p>
          : pages > 0 ? <img key={`${page}-${attempt}`} alt={`Слайд ${page} из ${pages}`} src={`/api/assets/${asset}/slides/${page}.jpg`} onError={() => setError(true)} />
          : <p role="status">Загружаем слайды…</p>}
      </div>
      <div className="slide-pager" aria-label="Страницы презентации">
        <button className="icon-action" disabled={page <= 1 || error} aria-label="Предыдущий слайд" onClick={() => setPage(page - 1)}><ChevronLeft size={18}/></button>
        <select disabled={!pages || error} aria-label="Номер слайда" value={pages ? page : ''} onChange={e => setPage(Number(e.target.value))}>
          {!pages && <option value="">…</option>}
          {Array.from({length:pages}, (_,i) => <option key={i} value={i+1}>{i+1} из {pages}</option>)}
        </select>
        <button className="icon-action" disabled={page >= pages || error} aria-label="Следующий слайд" onClick={() => setPage(page + 1)}><ChevronRight size={18}/></button>
      </div>
    </div>}
  </section>
}
