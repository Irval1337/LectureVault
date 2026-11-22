import { useEffect, useMemo, useRef, useState, type FormEvent, type RefObject } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ChevronDown, FileText, Folder, Paperclip, Upload, X } from 'lucide-react'
import { library, useSession } from '../data/store'
import { ApiError } from '../data/api'
import { slidePolicy, LENGTH_LABELS, type AppState, type JobInput } from '../data/model'
import { ProviderOptions, defaultGeneration, type GenerationOptions } from './providers'
import { CategoryOptions } from './organization'
import { Button } from './ui/button'
import { PageHeading } from './ui/page-heading'

type Draft = { category?: string; wordGoal?: string; slideLimit?: number | null; courseId: string; title: string; file?: { name: string; size: number }; pdfName?: string; preset: 'lecture' | 'seminar'; length: JobInput['length']; instructions: string; language: 'ru' | 'en' | 'auto' } & GenerationOptions

type UploadResult = { id: string } | { errors: Record<string, string> }
export type GenerationDraft = { video?: File; pdf?: File; settings?: Draft; upload?: Promise<UploadResult>; percent?: number; errors?: Record<string, string> }

export function CreateNotePage({ state, createCourse, draftRef }: { state: AppState; createCourse: () => void; draftRef: RefObject<GenerationDraft> }) {
  const writableCourses = useMemo(() => state.courses.filter(course => course.role !== 'viewer'), [state.courses])
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const session = useSession()
  const draftKey = `lecturevault.generation-draft.${session.user?.id}`
  const [draft, setDraft] = useState<Draft>(() => {
    let saved: Partial<Draft> = {}
    if (draftRef.current.settings) saved = draftRef.current.settings
    else {
      try { saved = JSON.parse(localStorage.getItem(draftKey) || '{}') ?? {} } catch { /* use empty draft */ }
    }
    const { video, pdf } = draftRef.current
    if (video) saved = { ...saved, file: { name: video.name, size: video.size } }
    if (pdf) saved = { ...saved, pdfName: pdf.name }
    return { title: '', preset: 'lecture', length: 'medium', instructions: '', language: 'ru', ...defaultGeneration, ...saved, courseId: [params.get('course'), saved.courseId, writableCourses[0]?.id].find(id => writableCourses.some(course => course.id === id)) ?? '' }
  })
  const [errors, setErrors] = useState<Record<string, string>>(draftRef.current.errors ?? {})
  const [dragging, setDragging] = useState(false)
  const [pdfDragging, setPdfDragging] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const pdfInput = useRef<HTMLInputElement>(null)
  const [upload, setUpload] = useState(draftRef.current.upload)
  useEffect(() => {
    if (!upload) return
    let mounted = true
    void upload.then(result => {
      if (!mounted) return
      if ('id' in result) navigate(`/queue?job=${result.id}`)
      else { setErrors(result.errors); setUpload(undefined) }
    })
    return () => { mounted = false }
  }, [upload, navigate])
  useEffect(() => { if (!draftRef.current.upload) draftRef.current.errors = errors }, [errors, draftRef])
  const [percent, setPercent] = useState(draftRef.current.percent ?? 0)
  useEffect(() => {
    if (!upload) return
    const timer = window.setInterval(() => setPercent(draftRef.current.percent ?? 0), 200)
    return () => window.clearInterval(timer)
  }, [upload, draftRef])
  const [canGenerate, setCanGenerate] = useState(false)
  useEffect(() => {
    if (draftRef.current.upload) return
    draftRef.current.settings = draft
    try { localStorage.setItem(draftKey, JSON.stringify(draft)) } catch { /* still available in this tab */ }
  }, [draft, draftKey, draftRef])
  useEffect(() => {
    const courseId = params.get('course')
    const category = params.get('folder') || ''
    if (courseId && writableCourses.some(course => course.id === courseId)) {
      setDraft(previous => previous.courseId === courseId && (previous.category || '') === category ? previous : { ...previous, courseId, category })
      setErrors(previous => previous.courseId ? { ...previous, courseId: '' } : previous)
    }
  }, [params, writableCourses])
  const change = <K extends keyof Draft>(key: K, value: Draft[K]) => { setDraft(previous => ({ ...previous, [key]: value })); setErrors(previous => ({ ...previous, [key]: '' })) }
  function chooseFile(file?: File) {
    if (!file) return
    if (!/\.(mp4|mkv|mov|webm)$/i.test(file.name)) { setErrors(previous => ({ ...previous, source: 'Выберите видео MP4, MKV, MOV или WebM.' })); return }
    if (!file.size || file.size > 512 * 1024 * 1024) { setErrors(previous => ({ ...previous, source: 'Выберите непустое видео размером до 512 МБ.' })); return }
    draftRef.current.video = file
    setDraft(previous => ({ ...previous, file: { name: file.name, size: file.size }, title: previous.title || file.name.replace(/\.[^.]+$/, '').replace(/_/g, ' ') }))
    setErrors(previous => ({ ...previous, source: '' }))
  }
  function choosePdf(file?: File) {
    if (!file) return
    if (!/\.pdf$/i.test(file.name) || !file.size || file.size > 50 * 1024 * 1024) { setErrors(previous => ({ ...previous, pdf: 'Выберите непустую PDF-презентацию размером до 50 МБ.' })); return }
    draftRef.current.pdf = file
    change('pdfName', file.name); setErrors(previous => ({ ...previous, pdf: '' }))
  }
  function submit(event: FormEvent) {
    event.preventDefault()
    if (draftRef.current.upload) return
    const next: Record<string, string> = {}
    if (!writableCourses.some(course => course.id === draft.courseId)) next.courseId = 'Выберите категорию для конспекта.'
    if (draft.wordGoal && (!/^\d+$/.test(draft.wordGoal) || +draft.wordGoal < 100 || +draft.wordGoal > 5000)) next.wordGoal = 'Введите от 100 до 5000 слов.'
    if (errors.pdf) next.pdf = errors.pdf
    if (!canGenerate) next.provider = 'Выберите подключённого провайдера и модель.'
    if (!draft.title.trim()) next.title = 'Введите название конспекта.'
    const { video, pdf } = draftRef.current
    if (!video) next.source = errors.source || 'Выберите видеофайл для загрузки. После обновления страницы файл нужно выбрать заново.'
    if (draft.pdfName && !draftRef.current.pdf) next.pdf = 'Повторно выберите PDF для загрузки или удалите вложение.'
    setErrors(next)
    if (Object.keys(next).length) { document.querySelector<HTMLElement>(next.courseId ? '#note-course' : next.title ? '#note-title' : next.source ? '.dropzone, .selected-file button' : next.provider ? '#generation-provider' : '.attach-button, .pdf-chip button')?.focus(); return }
    if (!video) return
    draftRef.current.percent = 0
    setPercent(0)
    const request = library.createJob({ courseId: draft.courseId, category: draft.category, title: draft.title.trim(), sourceType: 'file', sourceName: video.name, sourceSize: video.size, pdfName: pdf?.name, preset: draft.preset, length: draft.length, instructions: draft.instructions.trim(), targetWords: draft.wordGoal ? Number(draft.wordGoal) : undefined, slideLimit: draft.slideLimit === undefined ? 3 : draft.slideLimit, timestamps: false, mode: 'real', language: draft.language, provider: draft.provider, apiBase: draft.apiBase, model: draft.model, effort: draft.effort }, video, pdf, value => { draftRef.current.percent = value }).then(id => {
      draftRef.current = {}
      try { if (localStorage.getItem(draftKey) === JSON.stringify(draft)) localStorage.removeItem(draftKey) } catch { /* upload is already persisted */ }
      return { id }
    }, (error: Error) => {
      const field = error instanceof ApiError && ['pdf', 'provider'].includes(error.field ?? '') ? error.field! : 'source'
      const errors = { [field]: error.message }
      draftRef.current.upload = undefined
      draftRef.current.errors = errors
      return { errors }
    })
    draftRef.current.upload = request
    setUpload(request)
  }
  if (upload) return <><PageHeading title="Создание конспекта" /><section className="upload-status" aria-live="polite"><h2 role="status">{percent >= 100 ? 'Файлы загружены. Создаём задание…' : 'Загружаем материалы…'}</h2><p>{draftRef.current.video?.name}</p><progress aria-label="Передано данных" max={100} value={percent}/><span>{percent}%</span></section></>
  if (!writableCourses.length) return <><PageHeading title="Новый конспект" description="Сначала создайте категорию." /><Button onClick={createCourse}>Создать категорию</Button></>
  return <>
    <PageHeading title="Новый конспект" />
    <form className="note-form" onSubmit={submit} noValidate>
      <div className="generation-basics">
        <div className="field-group"><label className="field-label" htmlFor="note-title">Название</label><input id="note-title" maxLength={120} placeholder="Лекция 1. Введение" value={draft.title} onChange={event => change('title', event.target.value)} aria-invalid={!!errors.title} aria-describedby={errors.title ? 'note-title-error' : undefined} />{errors.title && <p id="note-title-error" className="field-error" role="alert">{errors.title}</p>}</div>
        <div className="field-group"><div className="field-label-row"><label className="field-label" htmlFor="note-course">Сохранить в категорию</label><button type="button" className="inline-action" onClick={createCourse}>Создать категорию</button></div><div className="course-select"><Folder size={16} /><select id="note-course" value={draft.courseId + (draft.category ? '/' + draft.category : '')} aria-invalid={!!errors.courseId} aria-describedby={errors.courseId ? 'note-course-error' : undefined} onChange={event => {
          const [course, ...parts] = event.target.value.split('/')
          setParams({course, folder: parts.join('/')}, {replace:true})
        }}><CategoryOptions courses={writableCourses}/></select><ChevronDown size={14} /></div>{errors.courseId && <p id="note-course-error" className="field-error" role="alert">{errors.courseId}</p>}</div>
      </div>
      <div className="generation-layout">
        <section className="form-section"><div className="form-section-title"><h2>Источник</h2></div>
          <input hidden type="file" aria-label="Видеофайл" id="video-file" aria-describedby={errors.source ? 'source-error' : undefined} ref={fileInput} accept=".mp4,.mkv,.mov,.webm" onChange={event => chooseFile(event.target.files?.[0])} />
          {draft.file ? <div className="selected-file"><FileText size={22} /><span><strong title={draft.file.name}>{draft.file.name}</strong><small>{draft.file.size < 1024 * 1024 ? `${Math.max(1, Math.ceil(draft.file.size / 1024))} КБ` : `${(draft.file.size / 1024 / 1024).toFixed(1)} МБ`}{!draftRef.current.video && ' · выберите файл повторно'}</small></span><button type="button" className="icon-action" aria-label="Выбрать видео заново" title="Выбрать видео заново" onClick={() => fileInput.current?.click()}><Upload size={16} /></button><button type="button" className="icon-action" aria-label="Убрать видео" onClick={() => { change('file', undefined); draftRef.current.video = undefined; setErrors(previous => ({ ...previous, source: '' })); if (fileInput.current) fileInput.current.value = '' }}><X size={16} /></button></div>
            : <button type="button" className={`dropzone ${dragging ? 'dragging' : ''}`} aria-invalid={!!errors.source} aria-describedby={errors.source ? 'source-error' : undefined} onClick={() => fileInput.current?.click()} onDragOver={event => { event.preventDefault(); setDragging(true) }} onDragLeave={() => setDragging(false)} onDrop={event => { event.preventDefault(); setDragging(false); for (const file of Array.from(event.dataTransfer.files)) { if (/\.pdf$/i.test(file.name)) choosePdf(file); else chooseFile(file) } }}><span className="upload-icon"><Upload size={22} strokeWidth={1.5} /></span><strong>Выберите видео или перетащите сюда</strong><small>MP4, MKV, MOV, WebM · до 512 МБ</small></button>}
          {errors.source && <p id="source-error" className="field-error" role="alert">{errors.source}</p>}
          <input hidden type="file" ref={pdfInput} accept=".pdf" aria-label="PDF-презентация" aria-invalid={!!errors.pdf} aria-describedby={errors.pdf ? 'pdf-error' : undefined} onChange={event => choosePdf(event.target.files?.[0])} />
          <div className={`pdf-drop ${pdfDragging ? 'dragging' : ''}`} onDragOver={event => { event.preventDefault(); setPdfDragging(true) }} onDragLeave={() => setPdfDragging(false)} onDrop={event => { event.preventDefault(); setPdfDragging(false); choosePdf(event.dataTransfer.files[0]) }}>
          {draft.pdfName ? <div className="pdf-chip"><Paperclip size={15} /><span title={draft.pdfName}>{draft.pdfName}{!draftRef.current.pdf && <button type="button" className="inline-action" onClick={() => pdfInput.current?.click()}>Выбрать PDF повторно</button>}</span><button type="button" className="icon-action" aria-label="Убрать PDF" onClick={() => { change('pdfName', undefined); draftRef.current.pdf = undefined; setErrors(previous => ({ ...previous, pdf: '' })); if (pdfInput.current) pdfInput.current.value = '' }}><X size={14} /></button></div>
            : <button type="button" className="attach-button" onClick={() => pdfInput.current?.click()}><Paperclip size={15} />Добавить презентацию<span>Перетащите PDF · до 50 МБ</span></button>}</div>
          {draft.pdfName && <label className="slide-limit">Иллюстрации в тексте<select value={draft.slideLimit === null ? 'unlimited' : draft.slideLimit ?? 3} onChange={e => change('slideLimit', e.target.value === 'unlimited' ? null : Number(e.target.value))}>
            {[3,6,...(draft.slideLimit === 12 || draft.slideLimit === 24 ? [draft.slideLimit] : [])].map(n => <option key={n} value={n}>{slidePolicy(n)}</option>)}
            <option value="unlimited">{slidePolicy(null)}</option><option value="0">{slidePolicy(0)}</option></select></label>}
          {errors.pdf && <div><p id="pdf-error" className="field-error" role="alert">{errors.pdf}</p>{!draft.pdfName && <button type="button" className="inline-action" onClick={() => { if (pdfInput.current) pdfInput.current.value = ''; setErrors(previous => ({ ...previous, pdf: '' })) }}>Продолжить без PDF</button>}</div>}
        </section>
        <section className="form-section"><div className="form-section-title"><h2>Конспект</h2></div>
          <fieldset className="preset-field"><legend className="field-label">Формат материала</legend><div className="length-options preset-options">{(['lecture', 'seminar'] as const).map(preset => <button type="button" key={preset} aria-pressed={draft.preset === preset} onClick={() => change('preset', preset)}>{preset === 'lecture' ? 'Лекция' : 'Семинар'}</button>)}</div></fieldset>
          <fieldset><legend className="field-label">Объём</legend><div className="length-options">{(['short', 'medium', 'detailed'] as const).map(length => <button key={length} type="button" aria-pressed={!draft.wordGoal && draft.length === length} onClick={() => { change('wordGoal', ''); change('length', length) }}>{LENGTH_LABELS[length]}</button>)}</div></fieldset>
          <label className="word-limit">Ориентир в словах<input type="number" min={100} max={5000} placeholder="По выбранному объёму" value={draft.wordGoal ?? ''} onChange={e => change('wordGoal', e.target.value)} aria-invalid={!!errors.wordGoal} /></label>{errors.wordGoal && <p className="field-error" role="alert">{errors.wordGoal}</p>}
          <div className="field-group"><label className="field-label" htmlFor="transcript-language">Язык речи</label><select id="transcript-language" value={draft.language} onChange={event => change('language', event.target.value as Draft['language'])}><option value="ru">Русский</option><option value="en">English</option><option value="auto">Определить автоматически</option></select></div>
        </section>
      </div>
      <div className="generation-layout generation-output"><section className="form-section generation-models"><div className="form-section-title"><h2>Генерация</h2></div><ProviderOptions onAvailabilityChange={setCanGenerate} value={draft} onChange={value => { setDraft(previous => ({ ...previous, ...value })); setErrors(previous => ({ ...previous, provider: '' })) }} />{errors.provider && <p className="field-error" role="alert">{errors.provider}</p>}</section>
      <div className="generation-instructions"><label className="field-label" htmlFor="instructions">Инструкции к конспекту <span>необязательно</span></label><textarea id="instructions" maxLength={4000} rows={3} placeholder="Какие темы и примеры нужно сохранить?" value={draft.instructions} onChange={event => change('instructions', event.target.value)} /></div>
      </div><div className="form-submit"><Button type="submit">Создать конспект</Button></div>
    </form>
  </>
}
