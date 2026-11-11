import { describe, expect, it } from 'vitest'
import { advanceQueue, buildMockMarkdown, initialState, isActive, parseState, TOTAL_DURATION, validateUrl, type AppState, type Job } from '../web/src/data/model'

const job = (id: string, time = 1000): Job => ({ id, courseId: 'course', title: `Лекция ${id}`, sourceType: 'file', sourceName: 'lecture.mp4', preset: 'lecture', length: 'medium', instructions: '', timestamps: false, status: 'queued', createdAt: time, attempt: 0 })
const queued = (...jobs: Job[]): AppState => ({ ...initialState(), courses: [{ id: 'course', title: 'Курс', workspace: 'personal', createdAt: 0 }], jobs })

describe('persistent sequential mock queue', () => {
  it('runs one job at a time and catches up after a refresh without duplicate notes', () => {
    const started = advanceQueue(queued(job('a'), job('b', 1200)), 1500)
    expect(started.jobs.map(j => j.status)).toEqual(['acquiring', 'queued'])
    const refreshed = parseState(JSON.stringify(started))
    const after = advanceQueue(refreshed, 1000 + TOTAL_DURATION + 500)
    expect(after.jobs.map(j => j.status)).toEqual(['ready', 'acquiring'])
    expect(after.jobs[1].startedAt).toBe(1000 + TOTAL_DURATION)
    expect(after.notes).toHaveLength(1)
    const done = advanceQueue(after, 1000 + TOTAL_DURATION * 2 + 100)
    expect(done.notes).toHaveLength(2)
    expect(advanceQueue(done, 999999).notes).toHaveLength(2)
  })
  it('does not process cancelled jobs', () => {
    const state = advanceQueue(queued({ ...job('cancelled'), status: 'cancelled' }, job('next')), 1500)
    expect(state.jobs[0].status).toBe('cancelled')
    expect(state.jobs[1].status).toBe('acquiring')
  })
  it('queues retries after older waiting jobs', () => {
    const state = advanceQueue(queued({ ...job('retry', 9000), attempt: 1 }, job('waiting', 4000)), 9100)
    expect(isActive(state.jobs.find(j => j.id === 'waiting')!)).toBe(true)
    expect(state.jobs.find(j => j.id === 'retry')?.status).toBe('queued')
  })
  it('fails the documented synthetic error source, then succeeds on retry', () => {
    const failJob = { ...job('error'), sourceType: 'url' as const, sourceName: 'https://error.example/lecture' }
    const failed = advanceQueue(queued(failJob), 1000 + TOTAL_DURATION)
    expect(failed.jobs[0].status).toBe('failed')
    expect(failed.notes).toHaveLength(0)
    const retried = advanceQueue(queued({ ...failJob, attempt: 1 }), 1000 + TOTAL_DURATION)
    expect(retried.jobs[0].status).toBe('ready')
    expect(retried.notes).toHaveLength(1)
  })
  it('retains source settings without reading media and escapes the note title', () => {
    const markdown = buildMockMarkdown({ ...job('a'), title: '<img src=x onerror=alert(1)>', length: 'detailed', preset: 'seminar' })
    expect(markdown).toContain('\\<img')
    expect(markdown).toContain('Вопрос для самопроверки')
    expect(markdown).toContain('Для семинара')
    expect(markdown).toContain('не анализировались')
    expect(markdown).toContain('$$')
    expect(markdown).toContain('```text')
  })
})

describe('local state and URL validation', () => {
  it('starts empty and rejects malformed persisted state', () => {
    expect(parseState(null)).toEqual(initialState())
    expect(() => parseState('{broken')).toThrow()
    expect(() => parseState('{"version":1,"courses":[]}')).toThrow()
    expect(() => parseState(JSON.stringify(queued({ ...job('a'), sourceType: 'url', sourceName: 'javascript:alert(1)' })))).toThrow()
  })
  it.each(['javascript:alert(1)', 'file:///etc/passwd', 'https://user:password@example.com', 'http://localhost/video', 'http://127.0.0.1/x', 'not a link'])('rejects invalid demo URL %s', value => expect(validateUrl(value)).toBeTruthy())
  it('accepts a normal external HTTPS URL without fetching it', () => expect(validateUrl('https://www.youtube.com/watch?v=example')).toBeNull())
})
