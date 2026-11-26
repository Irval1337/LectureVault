import { useSyncExternalStore } from 'react'
import { initialState, STORAGE_KEY, type AppState, type JobInput, type Note } from './model'
import { api, ApiError, uploadJob } from './api'

export type User = { id: string; name: string; email: string; verified: boolean }
type Session = { loading: boolean; user: User | null; emailVerification: boolean; supportEmail?: string; error: string }
let session: Session = { loading: true, user: null, emailVerification: true, error: '' }
let state = initialState()
let storageError = ''
let initialized = false
let refreshing: Promise<void> | undefined
let lastPayload = ''
const noteCache = new Map<string, Note>()
const noteRequests = new Map<string, Promise<void>>()
const listeners = new Set<() => void>()
const emit = () => listeners.forEach(fn => fn())
const subscribe = (fn: () => void) => { listeners.add(fn); return () => { listeners.delete(fn) } }
const notifyAuth = () => { try { localStorage.setItem('lecturevault.auth-event', crypto.randomUUID()) } catch { /* cookie session remains valid */ } }
const preferenceKey = () => `lecturevault.ui.${session.user?.id ?? 'anonymous'}`

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
      if (serialized !== lastPayload) {
        lastPayload = serialized
        const notes = payload.notes.map(note => {
          const cached = noteCache.get(note.id)
          const editing = window.location.pathname === `/notes/${note.id}/edit`
          if (cached && cached.revision !== note.revision && !editing) noteCache.delete(note.id)
          return cached && (cached.revision === note.revision || editing) ? { ...cached, courseId: note.courseId, category: note.category } : note
        })
        for (const id of noteCache.keys()) if (!notes.some(note => note.id === id)) noteCache.delete(id)
        state = { ...state, ...payload, notes }; emit()
      }
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

async function changeNote(id: string, endpoint: string, method: 'PATCH' | 'POST', body: object) {
  const owner = session.user?.id
  const note = await api<Note>(endpoint, { method, body: JSON.stringify(body) })
  if (session.user?.id !== owner) return note
  const loaded = { ...note, contentLoaded: true }
  noteCache.set(note.id, loaded)
  state = { ...state, notes: state.notes.map(item => item.id === id ? { ...loaded, category: item.category } : item) }; emit()
  await refresh(true)
  return note
}

export const auth = {
  subscribe,
  getSnapshot: () => session,
  async initialize(force = false) {
    if (initialized && !force) return
    initialized = true
    session = { ...session, loading: true, error: '' }; emit()
    try {
      const result = await api<{ user: User | null; emailVerification: boolean; supportEmail?: string }>('/auth')
      session = { ...result, loading: false, error: '' }
      if (session.user) { noteCache.clear(); state = { ...initialState(), ...preferences() }; lastPayload = ''; await refresh(true) }
    } catch (error) { session = { ...session, loading: false, error: (error as Error).message } }
    emit()
  },
  async login(email: string, password: string) {
    const result = await api<{ user: User }>('/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) })
    await auth.acceptUser(result.user)
  },
  async acceptUser(user: User) {
    session = { ...session, user, loading: false, error: '' }
    noteCache.clear(); state = { ...initialState(), ...preferences() }; lastPayload = ''; await refresh(true); emit(); notifyAuth()
  },
  eraseLocalUserData(userId: string) {
    for (const key of Object.keys(localStorage)) if (key === `lecturevault.ui.${userId}` || key === `lecturevault.space.${userId}` || key === `lecturevault.generation-draft.${userId}` || key.startsWith(`lecturevault.note-draft.${userId}.`)) localStorage.removeItem(key)
  },
  updateUser(user: User) { session = { ...session, user }; emit() },
  clear() { session = { ...session, user: null }; noteCache.clear(); state = initialState(); lastPayload = ''; emit(); notifyAuth() },
  async logout() {
    await api('/auth/logout', { method: 'POST' })
    session = { ...session, user: null }; noteCache.clear(); state = initialState(); lastPayload = ''; emit(); notifyAuth()
  },
}

export const library = {
  subscribe,
  getSnapshot: () => state,
  getError: () => storageError,
  reportError(error: unknown) { storageError = error instanceof Error ? error.message : 'Не удалось выполнить действие.'; emit() },
  async loadNote(id: string, force = false) {
    const owner = session.user?.id
    if (!owner) return
    const key = `${owner}:${id}`
    if (noteRequests.has(key)) return noteRequests.get(key)
    if (!force && noteCache.has(id)) return
    const request = api<Note>(`/notes/${id}`).then(result => {
      if (session.user?.id !== owner) return
      const note = { ...result, contentLoaded: true }
      noteCache.delete(id); noteCache.set(id, note)
      const evicted = new Set<string>()
      while (noteCache.size > 20) { const oldId = noteCache.keys().next().value!; noteCache.delete(oldId); evicted.add(oldId) }
      state = { ...state, notes: state.notes.map(existing => existing.id === id ? { ...note, category: existing.category } : evicted.has(existing.id) ? { ...existing, markdown: '', contentLoaded: false } : existing) }; emit()
    }).catch(error => library.reportError(error)).finally(() => { noteRequests.delete(key) })
    noteRequests.set(key, request); return request
  },
  async moveNote(id: string, courseId: string, path: string) {
    await api(`/notes/${id}/category`, { method: 'PUT', body: JSON.stringify({ courseId, path, existingOnly: true }) })
    await refresh(true)
  },
  async setCourseIcon(id: string, icon: string, path = '') {
    await api(`/courses/${id}/icon`, { method: 'PUT', body: JSON.stringify({ icon, path }) })
    await refresh(true)
  },
  async deleteNote(id: string) { await api(`/notes/${id}`, { method: 'DELETE' }); noteCache.delete(id); await refresh(true) },
  async deleteCourse(id: string) { await api(`/courses/${id}`, { method: 'DELETE' }); await refresh(true) },
  async createCourse(title: string, workspace: 'personal' | 'shared') {
    const result = await api<{ id: string }>('/courses', { method: 'POST', body: JSON.stringify({ title: title.trim(), workspace }) })
    await refresh(true)
    state = { ...state, expandedCourses: [...new Set([...state.expandedCourses, result.id])] }; savePreferences()
    return result.id
  },
  async reorganizeCategory(id: string, path: string, operation: 'rename'|'remove-level', name: string) {
    const result = await api<{path: string}>(`/courses/${id}/folders`, {method: 'PATCH', body: JSON.stringify({path, operation, name})})
    await refresh(true)
    return result.path
  },
  async moveCategory(id: string, path: string, courseId: string | null, parent: string, confirmation?: string) {
    const result = await api<{courseId: string; path: string}>(`/courses/${id}/location`, {
      method: 'PUT', body: JSON.stringify({ path, courseId, parent, confirmation }),
    })
    await refresh(true)
    const expandedCourses = state.expandedCourses.filter(course => path || course !== id)
    state = { ...state, expandedCourses: [...new Set([...expandedCourses, result.courseId])] }
    savePreferences()
    return result
  },
  async createFolder(courseId: string, parent: string, name: string) {
    const result = await api<{path: string}>(`/courses/${courseId}/folders`, {method: 'POST', body: JSON.stringify({ parent, name })})
    await refresh(true)
    return result.path
  },
  async createNote(courseId: string, category = '') {
    const note = await api<Note>('/notes', { method: 'POST', body: JSON.stringify({ courseId, category }) })
    noteCache.set(note.id, { ...note, contentLoaded: true })
    await refresh(true)
    if (!state.expandedCourses.includes(courseId)) library.toggleCourse(courseId)
    return note
  },
  saveNote(id: string, title: string, markdown: string, revision: number) {
    return changeNote(id, `/notes/${id}`, 'PATCH', { title, markdown, revision })
  },
  restoreNote(id: string, version: number, revision: number) {
    return changeNote(id, `/notes/${id}/revisions/${version}/restore`, 'POST', { revision })
  },
  async createJob(input: JobInput, video?: File, pdf?: File, progress?: (percent: number) => void) {
    const owner = session.user?.id
    const body = new FormData(); body.set('metadata', JSON.stringify(input))
    if (video) body.set('video', video)
    if (pdf) body.set('pdf', pdf)
    const result = progress ? await uploadJob(body, progress) : await api<{ id: string }>('/jobs', { method: 'POST', body })
    await refresh(true)
    if (owner === session.user?.id && !state.expandedCourses.includes(input.courseId)) library.toggleCourse(input.courseId)
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
  async importMarkdown(body: string, courseId: string) {
    const owner = session.user?.id
    const { documents } = await api<{ documents: { id: string; title: string; status: 'imported' | 'existing' }[] }>('/notes/import-markdown', { method: 'POST', body })
    await refresh(true)
    if (owner === session.user?.id && !state.expandedCourses.includes(courseId)) library.toggleCourse(courseId)
    return documents
  },
  hasLegacy() { try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}').courses?.length > 0 } catch { return false } },
  toggleCourse(id: string) { state = { ...state, expandedCourses: state.expandedCourses.includes(id) ? state.expandedCourses.filter(v => v !== id) : [...state.expandedCourses, id] }; savePreferences() },
  setReaderTheme(theme: 'light' | 'dark') { state = { ...state, readerTheme: theme }; savePreferences() },
  tick: () => refresh(),
}

export function useLibrary() { return useSyncExternalStore(subscribe, library.getSnapshot) }
export function useSession() { return useSyncExternalStore(subscribe, auth.getSnapshot) }

window.addEventListener('storage', event => { if (event.key === 'lecturevault.auth-event') void auth.initialize(true) })
window.addEventListener('pageshow', event => { if (event.persisted) void auth.initialize(true) })
