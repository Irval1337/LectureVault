import { useSyncExternalStore } from 'react'
import { initialState, STORAGE_KEY, type AppState, type JobInput, type Note } from './model'
import { api, ApiError } from './api'

export type User = { id: string; name: string }
type Session = { loading: boolean; user: User | null; users: User[]; error: string }
let session: Session = { loading: true, user: null, users: [], error: '' }
let state = initialState()
let storageError = ''
let initialized = false
let refreshing: Promise<void> | undefined
let lastPayload = ''
const listeners = new Set<() => void>()
const emit = () => listeners.forEach(fn => fn())
const subscribe = (fn: () => void) => { listeners.add(fn); return () => { listeners.delete(fn) } }
const preferenceKey = () => `lecturevault.ui.${session.user?.id ?? 'demo'}`

function preferences() {
  try { return JSON.parse(localStorage.getItem(preferenceKey()) || '{}') as Pick<AppState, 'expandedCourses' | 'readerTheme'> }
  catch { return {} }
}
function savePreferences() {
  try { localStorage.setItem(preferenceKey(), JSON.stringify({ expandedCourses: state.expandedCourses, readerTheme: state.readerTheme })) }
  catch { storageError = 'Не удалось сохранить настройки интерфейса в браузере.' }
  emit()
}

async function refresh(force = false) {
  if (!session.user) return
  if (refreshing) { await refreshing; if (force) return refresh(); return }
  const requestingUser = session.user.id
  refreshing = (async () => {
    try {
      const payload = await api<Pick<AppState, 'version' | 'courses' | 'notes' | 'jobs'>>('/state')
      if (session.user?.id !== requestingUser) return
      const serialized = JSON.stringify(payload)
      if (serialized !== lastPayload) { lastPayload = serialized; state = { ...state, ...payload }; emit() }
      if (storageError) { storageError = ''; emit() }
    } catch (error) {
      if (session.user?.id !== requestingUser) return
      storageError = (error as Error).message
      if (error instanceof ApiError && error.status === 401) { session = { ...session, user: null }; state = initialState(); lastPayload = '' }
      emit()
    }
    finally { refreshing = undefined }
  })()
  return refreshing
}

export const auth = {
  subscribe,
  getSnapshot: () => session,
  async initialize(force = false) {
    if (initialized && !force) return
    initialized = true
    session = { ...session, loading: true, error: '' }; emit()
    try {
      const result = await api<{ user: User | null; users: User[] }>('/auth')
      session = { ...result, loading: false, error: '' }
      if (session.user) { state = { ...initialState(), ...preferences() }; lastPayload = ''; await refresh(true) }
    } catch (error) { session = { ...session, loading: false, error: (error as Error).message } }
    emit()
  },
  async login(userId: string) {
    session = { ...session, loading: true, error: '' }; emit()
    try {
      const result = await api<{ user: User }>('/auth/login', { method: 'POST', body: JSON.stringify({ userId }) })
      session = { ...session, user: result.user }
      state = { ...initialState(), ...preferences() }; lastPayload = ''; await refresh(true)
      session = { ...session, loading: false }
    } catch (error) { session = { ...session, loading: false, user: null, error: (error as Error).message } }
    emit()
  },
  async logout() {
    await api('/auth/logout', { method: 'POST' })
    session = { ...session, user: null }; state = initialState(); lastPayload = ''; emit()
  },
}

export const library = {
  subscribe,
  getSnapshot: () => state,
  getError: () => storageError,
  reportError(error: unknown) { storageError = error instanceof Error ? error.message : 'Не удалось выполнить действие.'; emit() },
  async createCourse(title: string, workspace: 'personal' | 'shared') {
    const result = await api<{ id: string }>('/courses', { method: 'POST', body: JSON.stringify({ title: title.trim(), workspace }) })
    await refresh(true)
    state = { ...state, expandedCourses: [...new Set([...state.expandedCourses, result.id])] }; savePreferences()
    return result.id
  },
  async createNote(courseId: string) {
    const note = await api<Note>('/notes', { method: 'POST', body: JSON.stringify({ courseId }) })
    await refresh(true)
    if (!state.expandedCourses.includes(courseId)) library.toggleCourse(courseId)
    return note
  },
  async saveNote(id: string, title: string, markdown: string, revision: number) {
    const note = await api<Note>(`/notes/${id}`, { method: 'PATCH', body: JSON.stringify({ title, markdown, revision }) })
    await refresh(true)
    return note
  },
  async createJob(input: JobInput, video?: File, pdf?: File) {
    const body = new FormData(); body.set('metadata', JSON.stringify(input))
    if (video) body.set('video', video)
    if (pdf) body.set('pdf', pdf)
    const result = await api<{ id: string }>('/jobs', { method: 'POST', body })
    await refresh(true)
    if (!state.expandedCourses.includes(input.courseId)) library.toggleCourse(input.courseId)
    return result.id
  },
  async cancel(id: string) { await api(`/jobs/${id}/cancel`, { method: 'POST' }); await refresh(true) },
  async retry(id: string) { await api(`/jobs/${id}/retry`, { method: 'POST' }); await refresh(true) },
  async uploadImage(noteId: string, file: File) {
    const body = new FormData(); body.set('file', file)
    return (await api<{ url: string }>(`/notes/${noteId}/assets`, { method: 'POST', body })).url
  },
  async importLocal() {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return
    await api('/import', { method: 'POST', body: raw }); await refresh(true)
  },
  hasLegacy() { try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}').courses?.length > 0 } catch { return false } },
  toggleCourse(id: string) { state = { ...state, expandedCourses: state.expandedCourses.includes(id) ? state.expandedCourses.filter(v => v !== id) : [...state.expandedCourses, id] }; savePreferences() },
  setReaderTheme(theme: 'light' | 'dark') { state = { ...state, readerTheme: theme }; savePreferences() },
  tick: () => refresh(),
}

export function useLibrary() { return useSyncExternalStore(subscribe, library.getSnapshot) }
export function useSession() { return useSyncExternalStore(subscribe, auth.getSnapshot) }
