import { useEffect, useState } from 'react'
import { ChevronLeft, ChevronRight, Download } from 'lucide-react'
import { useLocation } from 'react-router-dom'
import { api } from '../data/api'

export function Presentation({ asset, name, embedded }: { asset: string; name: string; embedded: boolean }) {
  const { hash, key } = useLocation()
  const [open, setOpen] = useState(!embedded || hash === '#presentation')
  const [page, setPage] = useState(1)
  const [pages, setPages] = useState(0)
  const [error, setError] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const [loaded, setLoaded] = useState('')
  const imageKey = `${page}-${attempt}`
  useEffect(() => { if (hash === '#presentation') setOpen(true) }, [hash, key])
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
          : <>
            {loaded !== imageKey && <p role="status">Загружаем слайд…</p>}
            <img key={imageKey} data-loading={loaded !== imageKey ? '' : undefined} alt={`Слайд ${page}${pages ? ' из ' + pages : ''}`}
              src={`/api/assets/${asset}/slides/${page}.jpg`} onLoad={() => setLoaded(imageKey)} onError={() => setError(true)} />
          </>}
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
