import { useSyncExternalStore } from 'react'
import { advanceQueue, initialState, parseState, STORAGE_KEY, type AppState, type JobInput } from './model'

let state: AppState
let storageError = ''
try { state = parseState(localStorage.getItem(STORAGE_KEY)) }
catch { state = initialState(); storageError = 'Сохранённую библиотеку не удалось прочитать. Исходная запись не удалена; новые изменения заменят её.' }
const listeners = new Set<() => void>()
const emit = () => listeners.forEach(fn => fn())
function update(next: AppState) {
  if (next === state) return
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(next)); storageError = '' }
  catch { storageError = 'Браузер не смог сохранить изменения. Не закрывайте вкладку: проверьте доступность localStorage и свободное место.' }
  state = next
  emit()
}

export const library = {
  subscribe: (fn: () => void) => { listeners.add(fn); return () => { listeners.delete(fn) } },
  getSnapshot: () => state,
  getError: () => storageError,
  createCourse(title: string, workspace: 'personal' | 'shared') {
    const id = crypto.randomUUID()
    update({ ...state, courses: [...state.courses, { id, title: title.trim(), workspace, createdAt: Date.now() }], expandedCourses: [...state.expandedCourses, id] })
    return id
  },
  createJob(input: JobInput) {
    const id = crypto.randomUUID()
    update(advanceQueue({ ...state, jobs: [...state.jobs, { ...input, id, status: 'queued', createdAt: Date.now(), attempt: 0 }], expandedCourses: [...new Set([...state.expandedCourses, input.courseId])] }, Date.now()))
    return id
  },
  cancel(id: string) { update({ ...state, jobs: state.jobs.map(j => j.id === id && j.status === 'queued' ? { ...j, status: 'cancelled', completedAt: Date.now() } : j) }) },
  retry(id: string) { update(advanceQueue({ ...state, jobs: state.jobs.map(j => j.id === id && j.status === 'failed' ? { ...j, status: 'queued', createdAt: Date.now(), startedAt: undefined, completedAt: undefined, error: undefined, attempt: j.attempt + 1 } : j) }, Date.now())) },
  toggleCourse(id: string) { update({ ...state, expandedCourses: state.expandedCourses.includes(id) ? state.expandedCourses.filter(v => v !== id) : [...state.expandedCourses, id] }) },
  setReaderTheme(theme: 'light' | 'dark') { update({ ...state, readerTheme: theme }) },
  tick: () => update(advanceQueue(state, Date.now())),
}

export function useLibrary() { return useSyncExternalStore(library.subscribe, library.getSnapshot) }

window.addEventListener('storage', event => {
  if (event.key !== STORAGE_KEY) return
  try { state = parseState(event.newValue); storageError = ''; emit() }
  catch { storageError = 'Изменения из другой вкладки имеют неверный формат. Текущая библиотека сохранена в памяти.'; emit() }
})
