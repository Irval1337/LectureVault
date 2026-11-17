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
  await page.getByRole('link', { name: 'К курсу', exact: true }).click()
  await page.keyboard.press('Control+k'); await page.getByRole('dialog').getByLabel('Поиск по библиотеке').fill('несуществующая-заметка')
  await expect(page.locator('.note-list-row')).toHaveCount(0)
})

test('explicit legacy import preserves source localStorage and exports exact Markdown; course deletion is functional', async ({ page, accounts }) => {
  const markdown = await readFile('fixtures/workspace-reader.md', 'utf8')
  const seed = { version: 1, courses: [{ id: 'old', title: `Импорт ${Date.now()}`, workspace: 'personal' }], notes: [{ id: 'old-note', courseId: 'old', title: 'Импортированный текст', markdown }], jobs: [] }
  await page.addInitScript(seed => { localStorage.setItem('lecturevault.library.v1', JSON.stringify(seed)) }, seed)
  await signIn(page, accounts.author)
  await page.getByRole('button', { name: 'Импортировать локальные курсы и заметки' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Импортировано' })).toBeVisible()
  const note = (await (await page.request.get('/api/state')).json()).notes.find((n: { title: string }) => n.title === 'Импортированный текст')
  await page.goto(`/notes/${note.id}`); await expect(page.locator('.quartz-reader article')).toBeVisible()
  const download = page.waitForEvent('download'); await page.getByRole('link', { name: 'Скачать .md', exact: true }).click()
  expect(await readFile((await (await download).path())!, 'utf8')).toBe(markdown)
  expect(await page.evaluate(() => localStorage.getItem('lecturevault.library.v1'))).toBe(JSON.stringify(seed))
  await page.getByRole('link', { name: 'К курсу', exact: true }).click()
  page.once('dialog', dialog => dialog.accept())
  await page.getByRole('button', { name: 'Удалить курс', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Библиотека', exact: true })).toBeVisible()
  expect((await page.request.get(`/api/notes/${note.id}`)).status()).toBe(404)
})
