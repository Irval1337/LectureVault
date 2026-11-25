import { ArrowRight, Check, CircleAlert, Upload, X } from 'lucide-react'
import { Link, useLocation } from 'react-router-dom'
import { Button } from './ui/button'
import { ProgressMeter } from './ui/progress-meter'

export type UploadNotice = { name: string } & (
  { status: 'uploading'; percent: number } |
  { status: 'submitting'; uploaded: boolean } |
  { status: 'queued'; jobId: string } |
  { status: 'failed'; message: string; formUrl: string }
)

export function UploadPopup({ notice, dismiss }: { notice: UploadNotice; dismiss: () => void }) {
  const location = useLocation()
  const settled = notice.status === 'queued' || notice.status === 'failed'
  const Icon = notice.status === 'queued' ? Check : notice.status === 'failed' ? CircleAlert : Upload
  const title = notice.status === 'queued' ? 'Задание в очереди' : notice.status === 'failed' ? 'Не удалось добавить задание' :
    notice.status === 'uploading' ? 'Загружаем материалы' : notice.uploaded ? 'Файлы загружены. Создаём задание…' : 'Создаём задание…'

  return <aside className="upload-popup" aria-label="Загрузка в очередь" data-status={notice.status}>
    <span className="upload-popup-icon" aria-hidden="true"><Icon size={18}/></span>
    <div className="upload-popup-content">
      <div role="status">
        <strong>{title}</strong>
        <p className="upload-popup-name" title={notice.name}>{notice.name}</p>
        {notice.status === 'failed' && <p className="upload-popup-error">{notice.message}</p>}
      </div>
      {!settled && <ProgressMeter label={notice.status === 'uploading' ? 'Передано данных' : 'Добавление в очередь'}
        value={notice.status === 'uploading' ? notice.percent : undefined} showPercent/>}
      {notice.status === 'queued' && <Button asChild size="sm" variant="outline">
        <Link to={'/queue?job=' + notice.jobId} onClick={dismiss}>Открыть задание<ArrowRight size={14}/></Link>
      </Button>}
      {notice.status === 'failed' && location.pathname + location.search !== notice.formUrl && <Button asChild size="sm" variant="outline">
        <Link to={notice.formUrl} onClick={dismiss}>Вернуться к форме<ArrowRight size={14}/></Link>
      </Button>}
    </div>
    {settled && <button type="button" className="upload-popup-close icon-action" aria-label="Закрыть уведомление" onClick={dismiss}><X size={16}/></button>}
  </aside>
}
