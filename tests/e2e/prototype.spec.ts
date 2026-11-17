import { test, expect, type Page, type APIRequestContext } from '@playwright/test'
import { readFile } from 'node:fs/promises'

const prefix = `Приёмка ${Date.now()}`
const shots = 'test-results/screenshots/final/'
const headers = { 'X-LectureVault': '1' }
const video = { name: 'Лекция — очень длинное название исходного видео о распределённых системах.mp4', mimeType: 'video/mp4', buffer: Buffer.concat([Buffer.from([0, 0, 0, 24]), Buffer.from('ftypmp42'), Buffer.alloc(100)]) }
const pdf = { name: 'Кворумы и репликация — презентация.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4\nMock fixture; not parsed in demo mode.') }
async function login(page: Page, user = 'alice') {
  await page.request.post('/api/auth/login', { headers, data: { userId: user } })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Библиотека', exact: true })).toBeVisible()
}
async function course(request: APIRequestContext, title: string, workspace = 'personal') {
  const response = await request.post('/api/courses', { headers, data: { title, workspace } })
  expect(response.status()).toBe(201)
  return (await response.json()).id as string
}
async function capture(page: Page, name: string) {
  await page.evaluate(() => document.fonts.ready)
  if (/\/notes\/[^/]+$/.test(new URL(page.url()).pathname)) await expect(page.locator('.quartz-reader article')).toBeVisible()
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }))
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: `${shots}${name}.png`, animations: 'disabled' })
}
async function selectDemo(page: Page) {
  await page.getByRole('button', { name: 'Демо без анализа', exact: true }).click()
}

test('empty library → first course → filled form → persisted mock queue → reader and export', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await login(page)
  if (process.env.LV_E2E_ISOLATED) await expect(page.getByRole('heading', { name: 'Нет курсов' })).toBeVisible()
  for (const width of [1440, 768, 390]) {
    await page.setViewportSize({ width, height: width === 1440 ? 1000 : 844 }); await capture(page, `empty-${width}`)
  }
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto('/new')
  if (process.env.LV_E2E_ISOLATED) {
    await page.locator('main').getByRole('button', { name: 'Создать курс', exact: true }).click()
    const dialog = page.getByRole('dialog')
    await dialog.getByLabel('Название курса', { exact: true }).fill(`${prefix} · Системы`)
    await dialog.getByRole('button', { name: 'Создать курс', exact: true }).click()
    await expect(dialog).not.toBeVisible()
  } else {
    const id = await course(page.request, `${prefix} · Системы`)
    await page.goto(`/new?course=${id}`)
  }
  await selectDemo(page)
  await page.locator('#video-file').setInputFiles(video)
  await page.getByLabel('PDF-презентация', { exact: true }).setInputFiles(pdf)
  await page.getByLabel('Название', { exact: true }).fill('Репликация — итоговый конспект')
  await page.getByLabel('Инструкции к конспекту', { exact: false }).fill('Сохранить определения, формулы, таблицы и код.')
  await page.getByRole('button', { name: 'Семинар', exact: false }).click()
  await page.getByRole('button', { name: 'Подробно', exact: true }).click()
  await page.getByRole('tab', { name: 'Ссылка', exact: true }).click()
  await page.getByRole('tab', { name: 'Видеофайл', exact: true }).click()
  await expect(page.getByLabel('Инструкции к конспекту', { exact: false })).toHaveValue('Сохранить определения, формулы, таблицы и код.')
  await expect(page.locator('.selected-file').first()).toContainText('Лекция')
  for (const width of [1440, 768, 390]) {
    await page.setViewportSize({ width, height: width === 1440 ? 1000 : 844 }); await capture(page, `create-${width}`)
  }
  await page.getByRole('button', { name: 'Создать конспект', exact: true }).click()
  await expect(page).toHaveURL(/\/queue\?job=/)
  const jobId = new URL(page.url()).searchParams.get('job')!
  for (const width of [1440, 768, 390]) {
    await page.setViewportSize({ width, height: width === 1440 ? 1000 : 844 }); await capture(page, `queue-${width}`)
  }
  await page.goto('/'); await page.goto(`/queue?job=${jobId}`); await page.reload()
  const row = page.locator('.job-item').filter({ hasText: 'Репликация — итоговый конспект' })
  await row.getByRole('link', { name: 'Открыть', exact: true }).click()
  await expect(page.locator('.quartz-reader table')).toBeVisible()
  await expect(page.locator('.quartz-reader pre')).toBeVisible()
  await expect(page.locator('.quartz-reader .katex').first()).toBeVisible()
  await expect(page.locator('.quartz-reader img')).toBeVisible()
  const noteId = new URL(page.url()).pathname.split('/').at(-1)!
  const note = await (await page.request.get(`/api/notes/${noteId}`)).json()
  expect(note.isMock).toBe(true)
  const download = page.waitForEvent('download')
  await page.getByRole('link', { name: 'Скачать .md', exact: true }).click()
  expect(await readFile((await (await download).path())!, 'utf8')).toBe(note.markdown)
  await page.reload(); await expect(page.locator('.quartz-reader table')).toBeVisible()
  expect(errors).toEqual([])
})

test('manual Note: create, Crepe and CodeMirror roundtrip, rename, reload and export', async ({ page }) => {
  await login(page)
  const cid = await course(page.request, `${prefix} · Ручные заметки`)
  await page.goto(`/courses/${cid}`)
  await page.locator('main').getByRole('button', { name: 'Новый', exact: true }).first().click()
  await page.getByRole('dialog').getByRole('button', { name: 'Создать заметку', exact: false }).click()
  await expect(page.locator('.milkdown .ProseMirror')).toBeVisible()
  const id = new URL(page.url()).pathname.split('/')[2]
  const empty = await (await page.request.get(`/api/notes/${id}`)).json()
  expect(empty.markdown).toBe(''); expect(empty.origin).toBe('manual'); expect(empty.jobId).toBeNull()
  await page.getByLabel('Название заметки', { exact: true }).fill('Технический документ')
  await page.getByRole('tab', { name: 'Markdown', exact: true }).click()
  const markdown = await readFile('fixtures/workspace-reader.md', 'utf8')
  const source = page.getByRole('textbox', { name: 'Исходный Markdown' })
  await source.fill(markdown)
  await page.getByRole('tab', { name: 'Визуально', exact: true }).click()
  await expect(page.locator('.milkdown .ProseMirror table.children')).toBeVisible()
  await expect(page.locator('.milkdown .ProseMirror')).toContainText('Репликация')
  await page.locator('.milkdown .ProseMirror > p').last().click()
  await page.keyboard.press('End')
  await page.keyboard.press('Enter'); await page.keyboard.insertText('Проверено после редактирования.')
  for (const width of [1440, 768, 390]) {
    await page.setViewportSize({ width, height: width === 1440 ? 1000 : 844 }); await capture(page, `editor-${width}`)
  }
  await page.getByRole('button', { name: 'Сохранить', exact: true }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Сохранено на сервере' })).toBeVisible()
  const saved = await (await page.request.get(`/api/notes/${id}`)).json()
  expect(saved.markdown).toContain('Проверено после редактирования.')
  for (const part of ['```', '$$', '|', '/demo-replication.svg']) expect(saved.markdown).toContain(part)
  await page.getByRole('link', { name: 'Чтение', exact: true }).click()
  await expect(page.locator('.quartz-reader table')).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Технический документ', level: 1 })).toBeVisible()
  for (const width of [1440, 768, 390]) {
    await page.setViewportSize({ width, height: width === 1440 ? 1000 : 844 }); await capture(page, `reader-${width}`)
  }
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.getByRole('button', { name: 'Редактировать', exact: true }).click()
  await page.getByLabel('Название заметки', { exact: true }).fill('Переименованный документ')
  await page.getByRole('button', { name: 'Сохранить', exact: true }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Сохранено на сервере' })).toBeVisible()
  await page.getByRole('link', { name: 'Чтение', exact: true }).click()
  const download = page.waitForEvent('download')
  await page.getByRole('link', { name: 'Скачать .md', exact: true }).click()
  expect((await download).suggestedFilename()).toBe('Переименованный документ.md')
  expect(await readFile((await (await download).path())!, 'utf8')).toBe(saved.markdown)
  await page.goto('/'); await capture(page, 'library-1440')
})

test('queue: several jobs, cancel waiting, simulated error retry, no duplicate notes', async ({ page }) => {
  await login(page)
  const cid = await course(page.request, `${prefix} · Очередь`)
  const ids: string[] = []
  for (let i = 0; i < 4; i++) {
    const response = await page.request.post('/api/jobs', { headers, multipart: { metadata: JSON.stringify({ courseId: cid, title: `Очередь ${i}`, sourceType: 'url', sourceName: i === 2 ? 'https://error.example/lecture' : 'https://example.com/no-network', mode: 'mock' }) } })
    expect(response.status()).toBe(201); ids.push((await response.json()).id)
  }
  await page.goto(`/queue?job=${ids[3]}`)
  const cancelled = page.locator('.job-item').filter({ hasText: 'Очередь 3' })
  await cancelled.getByRole('button', { name: 'Отменить', exact: true }).click()
  await expect(cancelled).toContainText('Отменено')
  const failed = page.locator('.job-item').filter({ hasText: 'Очередь 2' })
  await failed.getByRole('button', { name: 'Повторить', exact: true }).click()
  await failed.getByRole('link', { name: 'Открыть', exact: true }).waitFor()
  await page.reload()
  const state = await (await page.request.get('/api/state')).json()
  expect(state.jobs.find((j: { id: string }) => j.id === ids[2]).attempt).toBe(1)
  expect(state.notes.filter((n: { courseId: string }) => n.courseId === cid)).toHaveLength(3)
})
