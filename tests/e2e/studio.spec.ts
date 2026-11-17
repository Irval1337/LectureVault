import { test, expect, type Page } from '@playwright/test'
import { readFile } from 'node:fs/promises'
const headers = { 'X-LectureVault': '1' }
async function login(page: Page) { await page.request.post('/api/auth/login', { headers, data: { userId: 'alice' } }) }

test('explicit legacy import preserves original localStorage and canonically exported Markdown', async ({ page }) => {
  const markdown = await readFile('fixtures/workspace-reader.md', 'utf8')
  const seed = { version: 1, courses: [{ id: 'old', title: `Локальная библиотека ${Date.now()}`, workspace: 'personal' }], notes: [{ id: 'old-note', courseId: 'old', title: 'Импортированный текст', markdown }], jobs: [], expandedCourses: ['old'], readerTheme: 'light' }
  await page.addInitScript(seed => { localStorage.setItem('lecturevault.library.v1', JSON.stringify(seed)) }, seed)
  await login(page); await page.goto('/')
  await page.getByRole('button', { name: 'Импортировать локальные курсы и заметки' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Импортировано' })).toBeVisible()
  const state = await (await page.request.get('/api/state')).json()
  const note = state.notes.find((n: { title: string }) => n.title === 'Импортированный текст')
  expect(note.markdown).toBe(markdown)
  await page.goto(`/notes/${note.id}`)
  await expect(page.locator('.quartz-reader table')).toBeVisible()
  expect(await page.evaluate(() => localStorage.getItem('lecturevault.library.v1'))).toBe(JSON.stringify(seed))
  const again = await page.request.post('/api/import', { headers, data: { ...seed, readerTheme: 'dark' } })
  expect((await again.json()).imported).toBe(0)
})

test('Studio shell, real TOC, keyboard focus, native overflow at 1440/768/390', async ({ page }) => {
  await login(page)
  const cid = (await (await page.request.post('/api/courses', { headers, data: { title: `Широкие материалы ${Date.now()}` } })).json()).id
  const markdown = '# Широкие материалы\n\n## Таблицы\n\n| A | B | C |\n| - | - | - |\n| ' + 'long_column_'.repeat(40) + ' | Данные | Пример |\n\n| A | B |\n| - | - |\n| 1 | 2 |\n\n## Код\n\n```text\n' + 'long_command_'.repeat(70) + '\n```\n\n## Формулы\n\n$$\n\\text{' + 'WideTerm'.repeat(70) + '}\n$$\n\nInline: $\\text{' + 'InlineTerm'.repeat(50) + '}$\n\n## Изображение\n\n![Материал](/demo-replication.svg)'
  const note = (await (await page.request.post('/api/notes', { headers, data: { courseId: cid, title: 'Широкие материалы', markdown } })).json())
  await page.goto(`/notes/${note.id}`)
  await page.getByRole('button', { name: 'Скрыть проводник', exact: true }).focus()
  expect(await page.getByRole('button', { name: 'Скрыть проводник', exact: true }).evaluate(el => getComputedStyle(el).outlineStyle)).not.toBe('none')
  await page.keyboard.press('Space'); await expect(page.locator('.desktop-sidebar')).not.toBeVisible()
  await page.keyboard.press('Control+k'); await expect(page.getByLabel('Поиск по библиотеке')).toBeFocused()
  await page.getByRole('button', { name: 'Скрыть контекст', exact: true }).click()
  await expect(page.locator('.context-sidebar')).toHaveCount(0)
  await page.getByRole('button', { name: 'Показать контекст', exact: true }).click()
  await page.getByRole('button', { name: 'Светлая тема ридера', exact: true }).click()
  await expect(page.locator('main')).toHaveClass(/reader-light/)
  await page.getByRole('button', { name: 'Тёмная тема ридера', exact: true }).click()
  for (const width of [1440, 768, 390]) {
    await page.setViewportSize({ width, height: width === 1440 ? 1000 : 844 })
    await expect(page.locator('.table-container').first()).toHaveAttribute('data-horizontal-scroll', '')
    await expect(page.locator('.table-container').nth(1)).not.toHaveAttribute('data-horizontal-scroll')
    for (const selector of ['.table-container', 'pre > code', '.katex-display', 'p > .katex']) {
      const block = page.locator(`.quartz-reader ${selector}`).first()
      await expect(block).toHaveAttribute('data-horizontal-scroll', '')
      await block.evaluate(el => { el.scrollLeft = 0 }); await block.focus(); await page.keyboard.press('ArrowRight')
      await expect.poll(() => block.evaluate(el => el.scrollLeft)).toBeGreaterThan(0)
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width)
    await page.screenshot({ path: `test-results/screenshots/final/overflow-${width}.png` })
    if (width < 1180) {
      await page.getByRole('button', { name: 'Оглавление', exact: true }).click()
      await page.locator('.mobile-toc').getByRole('link', { name: 'Изображение', exact: true }).click()
      await expect(page.locator('.mobile-toc > button')).toHaveAttribute('aria-expanded', 'false')
      await expect.poll(() => page.evaluate(() => decodeURIComponent(location.hash))).toBe('#section-изображение')
    }
  }
  await page.keyboard.press('Control+k')
  await expect(page.getByRole('dialog').getByLabel('Поиск по библиотеке')).toBeFocused()
  await expect(page.getByRole('dialog').locator('.tree-children a.active')).toHaveText('Широкие материалы')
  await page.keyboard.press('Escape'); await expect(page.getByRole('dialog')).not.toBeVisible()
  await page.getByRole('link', { name: 'К курсу', exact: true }).click()
  await expect(page.getByRole('heading', { level: 1, name: /Широкие материалы/ })).toBeVisible()
  await page.keyboard.press('Control+k')
  await page.getByRole('dialog').getByLabel('Поиск по библиотеке').fill('несуществующая-заметка')
  await expect(page.locator('.note-list-row')).toHaveCount(0)
})
