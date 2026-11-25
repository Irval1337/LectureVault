import { test, expect, headers, signIn, createAccount, createCourse, capture } from './fixtures'

test('import selected Markdown files into a subcategory with preview, recovery and original export', async ({ page, accounts }) => {
  await signIn(page, accounts.author)
  const course = await createCourse(page.request, 'Системное программирование')
  const folder = await page.request.post(`/api/courses/${course}/folders`, { headers, data: { name: 'Семинары' } })
  expect(folder.ok()).toBe(true)
  await page.goto(`/courses/${course}?` + new URLSearchParams({ folder: 'Семинары' }))
  await page.getByRole('main').getByRole('button', { name: 'Добавить заметку', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Импортировать Markdown' }).click()
  await expect(page.getByLabel('Куда сохранить')).toHaveValue(course + '/Семинары')

  const source = '# Планировщик\r\n\r\n## Алгоритмы\r\n\r\n![Схема](./scheme.png)\r\n'
  const upload = page.locator('input[type=file][accept=".md,.markdown"]')
  await upload.setInputFiles([
    { name: '01-scheduler.md', mimeType: 'text/markdown', buffer: Buffer.from(source) },
    { name: 'Потоки.markdown', mimeType: 'text/markdown', buffer: Buffer.from('## Контекст и стеки\n\n$T=n^2$') },
    { name: 'broken.md', mimeType: 'text/markdown', buffer: Buffer.from([0xff]) },
  ])
  await expect(page.getByRole('alert')).toContainText('UTF-8')
  await expect(page.getByLabel('Название заметки')).toHaveValue('Планировщик')
  await expect(page.getByRole('region', { name: 'Текст заметки' }).getByRole('heading', { name: 'Алгоритмы' })).toBeVisible()
  await expect(page.getByText('В тексте есть локальные ссылки.', { exact: false })).toBeVisible()
  await page.getByRole('button', { name: 'Потоки.markdown', exact: true }).click()
  await expect(page.getByLabel('Название заметки')).toHaveValue('Потоки')
  await page.getByLabel('Название заметки').fill('Стеки потоков')
  await capture(page, 'md-import-review-1440')
  await page.setViewportSize({ width: 390, height: 844 })
  await capture(page, 'md-import-review-390')
  expect(await page.getByRole('button', { name: 'Импортировать 2 файла' }).evaluate(button => button.getBoundingClientRect().bottom)).toBeLessThanOrEqual(844)

  await page.route('**/api/notes/import-markdown', route => route.fulfill({ status: 507, json: { detail: 'Лимит заметок исчерпан.' } }), { times: 1 })
  await page.getByRole('button', { name: 'Импортировать 2 файла' }).click()
  await expect(page.getByText('Лимит заметок исчерпан.')).toBeVisible()
  await expect(page.getByLabel('Название заметки')).toHaveValue('Стеки потоков')
  await page.getByRole('button', { name: 'Импортировать 2 файла' }).click()
  await expect(page.getByRole('heading', { name: 'Импорт завершён' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Стеки потоков' })).toBeVisible()
  const target = await page.getByRole('link', { name: 'Планировщик' }).getAttribute('href')
  const exportResponse = await page.request.get('/api' + target + '/export')
  expect(await exportResponse.text()).toBe(source)

  await page.getByRole('button', { name: 'Импортировать ещё' }).click()
  await upload.setInputFiles({ name: '01-scheduler.md', mimeType: 'text/markdown', buffer: Buffer.from(source) })
  await page.getByRole('button', { name: 'Импортировать 1 файл' }).click()
  await expect(page.getByRole('heading', { name: 'Эти заметки уже есть' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Планировщик' })).toHaveAttribute('href', target!)
  await page.getByRole('link', { name: 'Перейти в категорию' }).click()
  await expect(page.getByRole('main').getByRole('link', { name: 'Планировщик', exact: true })).toHaveCount(1)
})

test('import from an empty library after dropping a file and creating a destination', async ({ page }) => {
  const account = await createAccount(page.request, 'Пользователь импорта')
  await signIn(page, account)
  await page.getByRole('link', { name: 'Импортировать Markdown' }).click()
  const dropzone = page.getByRole('button', { name: 'Выберите или перетащите Markdown-файлы', exact: false })
  const transfer = await page.evaluateHandle(() => {
    const data = new DataTransfer()
    data.items.add(new File(['# Готовая лекция\n\nТекст конспекта'], 'lecture.md', { type: 'text/markdown' }))
    return data
  })
  await dropzone.dispatchEvent('drop', { dataTransfer: transfer })
  await expect(page.getByLabel('Название заметки')).toHaveValue('Готовая лекция')
  const submit = page.getByRole('button', { name: 'Импортировать 1 файл' })
  await expect(submit).toBeDisabled()
  await page.getByLabel('Новая личная категория').fill('Лекции')
  await page.getByRole('button', { name: 'Создать', exact: true }).click()
  await expect(page.getByLabel('Куда сохранить')).toBeVisible()
  await submit.click()
  await expect(page.getByRole('heading', { name: 'Импорт завершён' })).toBeVisible()
  await page.getByRole('region', { name: 'Результат импорта' }).getByRole('link', { name: 'Готовая лекция' }).click()
  await expect(page.getByText('Текст конспекта')).toBeVisible()
})
