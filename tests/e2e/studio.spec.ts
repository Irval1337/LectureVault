import { readFile } from 'node:fs/promises'
import { test, expect, headers, signIn, createCourse, capture } from './fixtures'

test('Studio reader: actual inner scroll, TOC, Escape, search, active tree and back at 1440/768/390', async ({ page, accounts }) => {
  await signIn(page, accounts.author)
  const cid = await createCourse(page.request, `Широкие материалы ${Date.now()}`)
  const markdown = '# Широкие материалы\n\n## Таблицы\n\n| A | B | C |\n| - | - | - |\n| ' + 'long_column_'.repeat(40) + ' | Данные | Пример |\n\n| A | B |\n| - | - |\n| 1 | 2 |\n\n## Код\n\n```text\n' + 'long_command_'.repeat(70) + '\n```\n\n## Формулы\n\n$$\n\\text{' + 'WideTerm'.repeat(70) + '}\n$$\n\nInline: $\\text{' + 'InlineTerm'.repeat(50) + '}$\n\n## Изображение\n\n![Материал](/demo-replication.svg)'
  const note = await (await page.request.post('/api/notes', { headers, data: { courseId: cid, title: 'Широкие материалы', markdown } })).json()
  await page.goto(`/notes/${note.id}`); await expect(page.locator('.quartz-reader article')).toBeVisible()
  await page.getByRole('button', { name: 'Скрыть проводник', exact: true }).focus()
  expect(await page.getByRole('button', { name: 'Скрыть проводник', exact: true }).evaluate(element => getComputedStyle(element).outlineStyle)).not.toBe('none')
  await page.keyboard.press('Space'); await expect(page.locator('.desktop-sidebar')).not.toBeVisible()
  await page.keyboard.press('Control+k'); await expect(page.getByLabel('Поиск по библиотеке')).toBeFocused()
  for (const width of [1440, 768, 390]) {
    await page.setViewportSize({ width, height: width === 1440 ? 1000 : 844 })
    await expect(page.locator('.table-container').first()).toHaveAttribute('data-horizontal-scroll', '')
    await expect(page.locator('.table-container').nth(1)).not.toHaveAttribute('data-horizontal-scroll')
    for (const selector of ['.table-container', 'pre > code', '.katex-display', 'p > .katex']) {
      const block = page.locator(`.quartz-reader ${selector}`).first()
      await expect(block).toHaveAttribute('data-horizontal-scroll', '')
      await block.evaluate(element => { element.scrollLeft = 0 }); await block.focus(); await page.keyboard.press('ArrowRight')
      await expect.poll(() => block.evaluate(element => element.scrollLeft)).toBeGreaterThan(0)
    }
    await capture(page, `wide-reader-${width}`)
    if (width < 1180) {
      await page.getByRole('button', { name: 'Оглавление', exact: true }).click()
      await page.locator('.mobile-toc').getByRole('link', { name: 'Изображение', exact: true }).click()
      await expect(page.locator('.mobile-toc > button')).toHaveAttribute('aria-expanded', 'false')
      await expect.poll(() => page.evaluate(() => decodeURIComponent(location.hash))).toBe('#section-изображение')
    }
  }
  await page.keyboard.press('Control+k'); await expect(page.getByRole('dialog').getByLabel('Поиск по библиотеке')).toBeFocused()
  await expect(page.getByRole('dialog').locator('.tree-children a.active')).toHaveText('Широкие материалы')
  await page.keyboard.press('Escape'); await expect(page.getByRole('dialog')).not.toBeVisible()
  await page.getByRole('link', { name: 'К категории', exact: true }).click()
  await page.keyboard.press('Control+k'); await page.getByRole('dialog').getByLabel('Поиск по библиотеке').fill('несуществующая-заметка')
  await expect(page.locator('.note-list-row')).toHaveCount(0)
})

test('explicit legacy import preserves source localStorage and exports exact Markdown; course deletion is functional', async ({ page, accounts }) => {
  const markdown = await readFile('fixtures/workspace-reader.md', 'utf8')
  const seed = { version: 1, courses: [{ id: 'old', title: `Импорт ${Date.now()}`, workspace: 'personal' }], notes: [{ id: 'old-note', courseId: 'old', title: 'Импортированный текст', markdown }], jobs: [] }
  await page.addInitScript(seed => { localStorage.setItem('lecturevault.library.v1', JSON.stringify(seed)) }, seed)
  await signIn(page, accounts.author)
  await page.getByRole('button', { name: 'Импортировать локальные категории и заметки' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Импортировано' })).toBeVisible()
  const note = (await (await page.request.get('/api/state')).json()).notes.find((n: { title: string }) => n.title === 'Импортированный текст')
  await page.goto(`/notes/${note.id}`); await expect(page.locator('.quartz-reader article')).toBeVisible()
  const download = page.waitForEvent('download'); await page.getByRole('link', { name: 'Скачать .md', exact: true }).click()
  expect(await readFile((await (await download).path())!, 'utf8')).toBe(markdown)
  expect(await page.evaluate(() => localStorage.getItem('lecturevault.library.v1'))).toBe(JSON.stringify(seed))
  await page.getByRole('link', { name: 'К категории', exact: true }).click()
  await page.getByLabel('Действия категории', { exact: true }).click()
  await page.getByRole('button', { name: 'Удалить категорию', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Удалить категорию', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Библиотека', exact: true })).toBeVisible()
  expect((await page.request.get(`/api/notes/${note.id}`)).status()).toBe(404)
})

test('editor keeps exact source on rename and changes typed while a save is pending', async ({ page, accounts }) => {
  await signIn(page, accounts.author)
  const cid = await createCourse(page.request, 'Черновики ' + Date.now())
  const markdown = '# Внутренний раздел\n\n* ручное форматирование\n\n'
  const note = await (await page.request.post('/api/notes', { headers, data: { courseId: cid, title: 'Занятие', markdown } })).json()
  await page.goto('/notes/' + note.id)
  await expect(page.locator('.quartz-reader article')).toContainText('Внутренний раздел')
  await page.getByRole('button', { name: 'Редактировать' }).click()
  const save = page.getByRole('button', { name: 'Сохранить', exact: true })
  await expect(save).toBeEnabled()
  await page.getByLabel('Название заметки').fill('Переименовано')
  await save.click()
  await expect.poll(async () => (await (await page.request.get('/api/notes/' + note.id)).json()).title).toBe('Переименовано')
  expect((await (await page.request.get('/api/notes/' + note.id)).json()).markdown).toBe(markdown)
  await page.getByRole('tab', { name: 'Markdown', exact: true }).click()
  const source = page.getByRole('textbox', { name: 'Исходный Markdown' })
  await source.fill('Версия 1')
  let allowSave!: () => void
  const ready = new Promise<void>(resolve => { allowSave = resolve })
  await page.route('**/api/notes/' + note.id, async route => {
    if (route.request().method() === 'PATCH') await ready
    await route.continue()
  })
  const requestStarted = page.waitForRequest(r => r.method() === 'PATCH')
  await save.click()
  await requestStarted
  await source.fill('Версия 2, дописана во время сохранения')
  allowSave()
  await expect(save).toBeEnabled()
  await expect(source).toContainText('Версия 2')
  await save.click()
  await expect.poll(async () => (await (await page.request.get('/api/notes/' + note.id)).json()).markdown).toBe('Версия 2, дописана во время сохранения')
  await expect(save).toBeEnabled()
  await page.unroute('**/api/notes/' + note.id)
  let release!: () => void
  const pending = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/notes/' + note.id, async route => {
    if (route.request().method() === 'PATCH') await pending
    await route.continue()
  })
  await source.fill('Версия 3')
  const started = page.waitForRequest(r => r.method() === 'PATCH')
  await save.click()
  await started
  await source.fill('Версия 2, дописана во время сохранения')
  await page.getByRole('link', { name: 'Чтение' }).click()
  await page.getByRole('button', { name: 'Редактировать' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Завершаем сохранение' })).toBeVisible()
  release()
  await expect.poll(async () => (await (await page.request.get('/api/notes/' + note.id)).json()).markdown).toBe('Версия 3')
  await expect(page.locator('.milkdown .ProseMirror')).toContainText('Версия 2, дописана во время сохранения')
  await save.click()
  await expect.poll(async () => (await (await page.request.get('/api/notes/' + note.id)).json()).markdown).toBe('Версия 2, дописана во время сохранения')

})

test('reader uses rendered headings for links and handles a mobile drawer across a resize', async ({ page, accounts }) => {
  await signIn(page, accounts.author)
  const courseId = await createCourse(page.request, 'Навигация по конспекту')
  async function addNote(title: string, markdown: string) {
    const result = await page.request.post('/api/notes', { headers, data: { courseId, title, markdown } })
    expect(result.status()).toBe(201)
    return (await result.json()).id
  }
  const article = await addNote('Практикум', 'Определения\n-----------\n\n' + 'Пояснение.\n\n'.repeat(80) + 'Вывод\n-----\n\n' + 'Пример.\n\n'.repeat(40))
  const code = await addNote('Сценарий', '~~~bash\n## Это комментарий\n~~~\n\nПояснение')

  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/notes/' + article + '#section-' + encodeURIComponent('вывод'))
  const heading = page.getByRole('heading', { name: 'Вывод', exact: true })
  await expect(heading).toBeInViewport()
  await page.getByRole('button', { name: 'Оглавление', exact: true }).click()
  await expect(page.locator('.mobile-toc a')).toHaveText(['Определения', 'Вывод'])
  await page.locator('.mobile-toc').getByRole('link', { name: 'Определения' }).click()
  await expect(page.getByRole('heading', { name: 'Определения', exact: true })).toBeInViewport()

  await page.getByRole('button', { name: 'Открыть навигацию' }).click()
  await expect(page.getByRole('dialog', { name: 'Навигация' })).toBeVisible()
  await page.setViewportSize({ width: 768, height: 844 })
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await page.keyboard.press('Control+k')
  await expect(page.locator('.desktop-sidebar').getByLabel('Поиск по библиотеке')).toBeFocused()

  await page.goto('/notes/' + code)
  await expect(page.locator('.quartz-reader pre')).toHaveText(/## Это комментарий/)
  await expect(page.getByRole('button', { name: 'Оглавление', exact: true })).toHaveCount(0)
  await page.setViewportSize({ width: 1440, height: 1000 })
  await expect(page.locator('.context-sidebar')).toHaveCount(0)

  await page.getByRole('button', { name: 'Включить светлую тему' }).click()
  await page.getByRole('button', { name: 'Редактировать' }).click()
  await expect(page.getByRole('textbox', { name: 'Текст заметки' })).toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Исходный текст блока' })).toBeVisible()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  const style = page.locator('.top-bar-heading-button')
  await style.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('button', { name: 'Заголовок 2' })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(style).toBeFocused()
  await expect(page.getByRole('button', { name: 'Заголовок 2' })).toHaveCount(0)
  const paragraph = page.locator('.ProseMirror > p').filter({ hasText: 'Пояснение' })
  await paragraph.click()
  await page.keyboard.press('Home')
  await page.keyboard.press('Shift+End')
  await page.locator('.milkdown-top-bar').getByRole('button', { name: 'Полужирный', exact: true }).focus()
  await page.keyboard.press('Enter')
  await expect(paragraph.locator('strong')).toHaveText('Пояснение')
  await page.getByRole('button', { name: 'Сохранить', exact: true }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Сохранено на сервере' })).toBeVisible()
  await page.getByRole('link', { name: 'Чтение' }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  await expect(page.locator('.quartz-reader strong')).toHaveText('Пояснение')
})
