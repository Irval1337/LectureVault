import demoMarkdown from '../../../fixtures/demo-note.md?raw'

export type Workspace = 'personal' | 'shared'
export const PROVIDER_NAMES = { codex: 'Codex', claude: 'Claude Code', openai: 'Свой API' } as const
export type ProviderId = keyof typeof PROVIDER_NAMES
export type Course = { folders?: string[]; folderIcons?: Record<string,string>; icon?: string; id: string; title: string; workspace: Workspace; createdAt: number; ownerId?: string; role?: 'owner' | 'editor' | 'viewer' }
export type Note = { category?: string; id: string; courseId: string; title: string; markdown: string; createdAt: number; jobId?: string | null; contentLoaded?: boolean; revision?: number; origin?: 'manual' | 'generated'; isMock?: boolean; updatedAt?: number; createdBy?: string | null }
export const canDeleteNote = (course: Course | undefined, note: Note, userId: string | undefined) =>
  course?.role === 'owner' || (course?.role === 'editor' && !!userId && note.createdBy === userId)
export type JobStatus = 'queued' | 'acquiring' | 'transcribing' | 'waiting_generation' | 'preparing' | 'generating' | 'ready' | 'failed' | 'cancelled'
export type JobInput = { category?: string;
  courseId: string; title: string; sourceType: 'file' | 'url'; sourceName: string; sourceSize?: number;
  reuseJobId?: string;
  preset: 'lecture' | 'seminar'; length: 'short' | 'medium' | 'detailed'; instructions: string;
  targetWords?: number; slideLimit?: number | null; apiBase?: string; pdfName?: string; timestamps: boolean; mode?: 'mock' | 'real'; language?: 'ru' | 'en' | 'auto'; sourceAssetId?: string; pdfAssetId?: string; provider?: ProviderId; model?: string; effort?: 'low' | 'medium' | 'high' | 'xhigh'
}
export type Job = JobInput & { canRetry?: boolean; transcriptAssetId?: string; effortApplied?: boolean; transcriptReused?: boolean; slidesReused?: boolean; slideTextCondensed?: number; recognitionPercent?: number; stageStartedAt?: number; failedStage?: JobStatus; id: string; status: JobStatus; createdAt: number; startedAt?: number; completedAt?: number; attempt: number; createdBy?: string; error?: string; noteId?: string }
export function reuseMaterialsUrl(job: Pick<Job, 'id' | 'courseId' | 'category'>) {
  return '/new?' + new URLSearchParams({ fromJob: job.id, course: job.courseId, folder: job.category ?? '' })
}
export type AppState = { version: 1; courses: Course[]; notes: Note[]; jobs: Job[]; expandedCourses: string[]; readerTheme: 'dark' | 'light' }

export const STORAGE_KEY = 'lecturevault.library.v1'
export const STAGES = [
  { status: 'acquiring', label: 'Подготовка источника', duration: 1800 },
  { status: 'transcribing', label: 'Распознавание речи', duration: 3400 },
  { status: 'preparing', label: 'Подготовка материалов', duration: 2200 },
  { status: 'generating', label: 'Создание конспекта', duration: 4200 },
] as const
export const TOTAL_DURATION = STAGES.reduce((sum, s) => sum + s.duration, 0)
export const STATUS_LABELS: Record<JobStatus, string> = {
  queued: 'В очереди', acquiring: 'Подготовка источника', transcribing: 'Распознавание речи', preparing: 'Подготовка материалов',
  waiting_generation: 'Расшифровка готова · ждёт генерации', generating: 'Создание конспекта', ready: 'Готово', failed: 'Ошибка', cancelled: 'Отменено',
}
export const LENGTH_LABELS = { short: 'Кратко', medium: 'Стандартно', detailed: 'Подробно' }
export const initialState = (): AppState => ({ version: 1, courses: [], notes: [], jobs: [], expandedCourses: [], readerTheme: 'dark' })
export const isActive = (job: Job) => STAGES.some(s => s.status === job.status)
export const isQueued = (job: Job) => job.status === 'queued' || job.status === 'waiting_generation'

export function validateUrl(value: string): string | null {
  try {
    const url = new URL(value)
    if (!['http:', 'https:'].includes(url.protocol)) return 'Используйте ссылку, начинающуюся с http:// или https://.'
    if (url.username || url.password) return 'Ссылки с логином или паролем не поддерживаются.'
    const host = url.hostname.toLowerCase()
    if (!host.includes('.') || host.endsWith('.local') || host === 'localhost' || host.startsWith('[') || /^\d+(\.\d+){3}$/.test(host)) return 'Укажите ссылку на внешний видеосервис.'
    return null
  } catch { return 'Проверьте ссылку. Например: https://www.youtube.com/watch?v=example' }
}

export function buildMockMarkdown(job: JobInput) {
  const title = job.title.replace(/[\r\n]/g, ' ').replace(/([\\`*_[\]<>#])/g, '\\$1')
  const content = demoMarkdown.replace(/^# .*\n/, '')
  return `# ${title}\n\n${content}\n\n## 4. Сравнение подходов\n\n| Подход | Чтение | Издержки |\n| --- | --- | --- |\n| Синхронная репликация | Актуальные данные | Ожидание подтверждения |\n| Асинхронная репликация | Возможна задержка | Быстрый ответ |\n\n## 5. Как читать этот конспект\n\nЭто учебный пример о репликации и консистентности. **Видео, ссылка и презентация не анализировались.** Выбранные параметры сохранены в задании и показаны в сведениях об источнике.\n\n${job.preset === 'seminar' ? 'Для семинара: объясните на примере, почему чтение с отстающей реплики может вернуть старое значение.\n\n' : ''}${job.length === 'detailed' ? '### Вопрос для самопроверки\n\nПочему добавление реплик само по себе не обеспечивает линеаризуемость? Подумайте о порядке подтверждения записи и распространения изменений.\n\n' : ''}${job.length !== 'short' ? '![Демонстрационная иллюстрация репликации](/demo-replication.svg)\n\n' : ''}---\n\n*Демонстрационный материал LectureVault. Не результат обработки источника.*\n`
}

export function advanceQueue(previous: AppState, now: number): AppState {
  let state = previous
  let cursor: number | undefined
  for (let i = 0; i <= previous.jobs.length; i++) {
    let active = state.jobs.find(isActive)
    if (!active) {
      const queued = state.jobs.filter(j => j.status === 'queued').sort((a, b) => a.createdAt - b.createdAt)[0]
      if (!queued) break
      const startedAt = Math.max(queued.createdAt, cursor ?? queued.createdAt)
      state = { ...state, jobs: state.jobs.map(j => j.id === queued.id ? { ...j, status: 'acquiring', startedAt } : j) }
      active = state.jobs.find(j => j.id === queued.id)!
    }
    const start = active.startedAt ?? active.createdAt
    const elapsed = now - start
    if (elapsed < TOTAL_DURATION) {
      let duration = 0
      const stage = STAGES.find(s => { duration += s.duration; return elapsed < duration }) ?? STAGES[STAGES.length - 1]
      if (active.status !== stage.status) state = { ...state, jobs: state.jobs.map(j => j.id === active!.id ? { ...j, status: stage.status } : j) }
      break
    }
    cursor = start + TOTAL_DURATION
    const course = state.courses.find(c => c.id === active!.courseId)
    const demoFailure = active.sourceType === 'url' && new URL(active.sourceName).hostname === 'error.example' && active.attempt === 0
    if (!course || demoFailure) {
      state = { ...state, jobs: state.jobs.map(j => j.id === active!.id ? { ...j, status: 'failed', completedAt: cursor, error: course ? 'Демонстрационная ошибка источника. Нажмите «Повторить», чтобы продолжить демо.' : 'Категория больше недоступна.' } : j) }
    } else {
      const noteId = `note-${active.id}`
      const note: Note = { id: noteId, courseId: active.courseId, title: active.title, markdown: buildMockMarkdown(active), createdAt: cursor, jobId: active.id }
      state = { ...state, jobs: state.jobs.map(j => j.id === active!.id ? { ...j, status: 'ready', completedAt: cursor, noteId } : j), notes: state.notes.some(n => n.id === noteId) ? state.notes : [...state.notes, note] }
    }
  }
  return state
}

export function parseState(raw: string | null): AppState {
  if (!raw) return initialState()
  const state = JSON.parse(raw) as AppState
  if (state.version !== 1 || !Array.isArray(state.courses) || !Array.isArray(state.notes) || !Array.isArray(state.jobs) || !Array.isArray(state.expandedCourses)) throw new Error('Неверный формат библиотеки')
  if (state.courses.some(c => typeof c.id !== 'string' || typeof c.title !== 'string' || !['personal', 'shared'].includes(c.workspace))) throw new Error('Неверный формат категорий')
  if (state.notes.some(n => typeof n.id !== 'string' || typeof n.title !== 'string' || typeof n.markdown !== 'string')) throw new Error('Неверный формат конспектов')
  if (state.jobs.some(j => typeof j.id !== 'string' || typeof j.title !== 'string' || typeof j.sourceName !== 'string' || !Object.hasOwn(STATUS_LABELS, j.status) || !['file', 'url'].includes(j.sourceType) || !Number.isFinite(j.createdAt) || (j.sourceType === 'url' && validateUrl(j.sourceName)))) throw new Error('Неверный формат очереди')
  return { ...state, readerTheme: state.readerTheme === 'light' ? 'light' : 'dark' }
}

export function slidePolicy(limit: number | null | undefined) {
  if (limit === null) return 'Без ограничения'
  if (limit === 0) return 'Только PDF'
  if (limit === 3) return 'Только ключевые · до 3'
  return `По теме · до ${limit ?? 6}`
}
