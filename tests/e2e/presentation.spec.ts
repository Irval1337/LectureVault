import { execFileSync } from 'node:child_process'
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test, expect, signIn, createCourse, headers, capture } from './fixtures'

test('video and dropped slides reach Whisper, the compatible API, Markdown and a private paginated reader', async ({ page, accounts, browser }) => {
  test.setTimeout(90000)
  await signIn(page, accounts.author)
  const course = await createCourse(page.request, 'Системы хранения')
  const baseUrl = 'http://model-api:8091/v1'
  const connect = await page.request.post('/api/providers/openai/key', { headers, data: { baseUrl, apiKey: '' } })
  expect(connect.ok()).toBe(true)
  const slides = await browser.newPage()
  await slides.setContent('<style>@page{size:600px 360px;margin:32px}section{break-after:page;font:24px sans-serif}</style><section><h1>Реплики</h1>Три узла хранят копии данных.</section><section><h1>Кворум</h1>R + W &gt; N</section>')
  const pdf = await slides.pdf({ preferCSSPageSize: true })
  await slides.close()
  const temp = await mkdtemp(join(tmpdir(), 'lecturevault-speech-'))
  let video: Buffer
  try {
    const file = join(temp, 'lecture.mp4')
    // Several Whisper windows leave a measurable intermediate stage even on fast hosts.
    const speech = 'Today we study data storage. Three servers keep a copy of every record. Two copies are needed to confirm a write. '.repeat(28)
    execFileSync('ffmpeg', ['-nostdin', '-v', 'error', '-f', 'lavfi', '-i', `flite=text=${speech}:voice=slt`, '-c:a', 'aac', file])
    video = await readFile(file)
  } finally { await rm(temp, { recursive: true, force: true }) }
  await page.goto('/new?course=' + course)
  await page.getByLabel('Провайдер', { exact: true }).selectOption('openai')
  await expect(page.getByLabel('Модель', { exact: true })).toHaveValue('local-study:v2')
  await page.locator('#video-file').setInputFiles({ name: 'Репликация.mp4', mimeType: 'video/mp4', buffer: video })
  const transfer = await page.evaluateHandle(data => {
    const transfer = new DataTransfer()
    transfer.items.add(new File([Uint8Array.from(atob(data), c => c.charCodeAt(0))], 'Репликация.pdf', { type: 'application/pdf' }))
    return transfer
  }, pdf.toString('base64'))
  await page.locator('.pdf-drop').dispatchEvent('drop', { dataTransfer: transfer })
  await expect(page.locator('.pdf-chip')).toContainText('Репликация.pdf')
  await page.getByLabel('Ориентир в словах').fill('750')
  const policy = page.getByLabel('Иллюстрации в тексте')
  await policy.selectOption({label:'Без ограничения'})
  await expect(policy).toHaveValue('unlimited')
  await policy.selectOption('3')
  await page.getByLabel('Язык речи').selectOption('en')
  await page.getByRole('button', { name: 'Включить светлую тему' }).click()
  await capture(page, 'light-generation')
  await page.getByRole('button', { name: 'Создать конспект', exact: true }).click()
  await expect(page).toHaveURL(/queue\?job=/)
  const progress = page.getByRole('progressbar', { name: 'Распознано аудио' })
  await expect(progress).toBeVisible({ timeout: 30000 })
  await expect(progress).toHaveAttribute('max', '100')
  await expect(progress).toHaveAttribute('value', /^[1-9]\d?$/, { timeout: 30000 })
  await capture(page, 'light-queue-recognition')
  const elapsed = page.locator('time[aria-label="Время ожидания ответа модели"]:visible')
  await expect(elapsed).toHaveText(/0:0[1-9]/, { timeout: 40000 })
  expect((await page.request.get('/api/providers', { timeout: 1500 })).ok()).toBe(true)
  await capture(page, 'light-queue-generation')
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.locator('.job-item.selected + .inline-job-steps')).toBeVisible()
  const selected = page.locator('.job-item.selected')
  await expect(selected).toContainText('local-study:v2')
  await selected.getByRole('link', { name: 'Открыть' }).click({ timeout: 60000 })
  const gallery = page.getByRole('region', { name: 'Слайды презентации' })
  let allowSlide!: () => void
  const gate = new Promise<void>(resolve => { allowSlide = resolve })
  await page.route('**/slides/1.jpg', async route => { await gate; await route.continue() })
  await gallery.getByRole('button', { name: 'Показать все слайды' }).click()
  const frame = gallery.locator('.slide-frame')
  await expect(frame).toBeVisible()
  const reserved = await frame.evaluate(el => el.clientHeight)
  expect(reserved).toBeGreaterThan(100)
  allowSlide()
  const image = gallery.getByRole('img', { name: 'Слайд 1 из 2' })
  await image.scrollIntoViewIfNeeded()
  await expect.poll(() => image.evaluate(el => (el as HTMLImageElement).naturalWidth)).toBeGreaterThan(100)
  expect(await frame.evaluate(el => el.clientHeight)).toBe(reserved)
  await gallery.getByRole('button', { name: 'Следующий слайд' }).click()
  const next = gallery.getByRole('img', { name: 'Слайд 2 из 2' })
  await expect.poll(() => next.evaluate(el => (el as HTMLImageElement).naturalWidth)).toBeGreaterThan(100)
  expect(await frame.evaluate(el => el.clientHeight)).toBe(reserved)
  await expect(gallery.getByRole('button', { name: 'Следующий слайд' })).toBeDisabled()
  await gallery.getByLabel('Номер слайда').selectOption('1')
  await expect(gallery.getByRole('img', { name: 'Слайд 1 из 2' })).toBeVisible()
  const inline = page.locator('.quartz-reader img[alt="Слайд 2"]')
  await expect(inline).toHaveCount(1)
  const src = await inline.getAttribute('src')
  const exported = await page.request.get((await page.getByRole('link', { name: 'Скачать .md' }).getAttribute('href'))!)
  expect(await exported.text()).toContain('![Слайд 2](' + src + ')')
  const outsider = await browser.newPage({ baseURL: process.env.TEST_BASE_URL })
  await signIn(outsider, accounts.reader)
  expect((await outsider.request.get(src!)).status()).toBe(404)
  await outsider.close()
  for (const width of [1440, 768, 390]) {
    await page.setViewportSize({ width, height: width === 1440 ? 1000 : 844 })
    if (width === 1440) {
      await page.getByRole('tab',{name:'Материалы'}).click()
      const details = page.locator('.context-sidebar .source-details')
      for (const option of ['Английский','750 слов','local-study:v2','до 3']) await expect(details).toContainText(option)
    }
    await capture(page, 'light-slides-' + width)
  }
  await page.getByRole('button', { name: 'Редактировать' }).click()
  await expect(page.getByRole('textbox', { name: 'Текст заметки' })).toBeVisible()
  await capture(page, 'light-editor-390')
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  await expect(page.getByRole('textbox', { name: 'Текст заметки' })).toBeVisible()
  await page.request.post('/api/providers/openai/disconnect', { headers, data: {} })
})

test('two lectures from one account reach the compatible API concurrently', async ({page, accounts}) => {
  test.setTimeout(90000)
  await signIn(page, accounts.author)
  const courseId = await createCourse(page.request, 'Параллельные лекции')
  const baseUrl = 'http://model-api:8091/v1'
  expect((await page.request.post('/api/providers/openai/key', {headers, data:{baseUrl, apiKey:''}})).ok()).toBe(true)
  const temp = await mkdtemp(join(tmpdir(), 'lecturevault-parallel-'))
  try {
    const file = join(temp,'lecture.mp4')
    execFileSync('ffmpeg', ['-nostdin','-v','error','-f','lavfi','-i','flite=text=Today we will study the replication of data across three different computers.','-c:a','aac',file])
    const buffer = await readFile(file)
    const jobs = await Promise.all([1,2].map(async index => {
      const title = 'Параллельная проверка ' + index
      const res = await page.request.post('/api/jobs', { headers, multipart: {
        metadata:JSON.stringify({courseId,title,sourceType:'file',sourceName:'lecture.mp4',mode:'real',provider:'openai',apiBase:baseUrl,model:'local-study:v2',language:'en',targetWords:750}),
        video:{name:'lecture.mp4',mimeType:'video/mp4',buffer}
      }})
      expect(res.status()).toBe(201)
      return (await res.json()).id
    }))
    // The endpoint releases either response only after both generation requests arrive.
    await expect.poll(async () => Promise.all(jobs.map(async id => (await (await page.request.get('/api/jobs/'+id)).json()).status)), {timeout:60000}).toEqual(['ready','ready'])
    const completed = await Promise.all(jobs.map(async id => (await page.request.get('/api/jobs/'+id)).json()))
    expect(completed.filter(job => job.transcriptReused)).toHaveLength(1)
  } finally {
    await rm(temp,{recursive:true,force:true})
    await page.request.post('/api/providers/openai/disconnect',{headers,data:{}})
  }
})
