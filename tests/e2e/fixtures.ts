import { test as base, expect, type APIRequestContext, type Page } from '@playwright/test'
import { randomUUID } from 'node:crypto'
import { readFile, writeFile, copyFile } from 'node:fs/promises'

export const headers = { 'X-LectureVault': '1' }
export const password = 'A secure phrase for browser acceptance'
export type Account = { id: string; email: string; name: string }
export const shots = 'test-results/screenshots/public/'
export async function emailLink(request: APIRequestContext, email: string, path: string) {
  const mailbox = process.env.LV_MAILPIT_URL!
  let link = ''
  await expect.poll(async () => {
    const result = await (await request.get(mailbox + '/api/v1/messages')).json()
    for (const message of result.messages ?? []) {
      if (!message.To.some((to: { Address: string }) => to.Address === email)) continue
      const body = await (await request.get(mailbox + '/api/v1/message/' + message.ID)).json()
      const match = body.Text?.match(new RegExp(`https?://[^\\s]+${path}#token=[A-Za-z0-9_-]+`))
      if (match) { link = match[0]; return true }
    }
    return false
  }, { timeout: 25000, message: 'A real message must arrive at the isolated SMTP inbox' }).toBe(true)
  return link
}
export async function createAccount(request: APIRequestContext, name: string): Promise<Account> {
  const email = `lv-${randomUUID()}@example.com`
  const response = await request.post('/api/auth/register', { headers, data: { name, email, password } })
  expect(response.status()).toBe(200)
  const link = await emailLink(request, email, '/verify-email')
  const token = new URL(link).hash.slice('#token='.length)
  const verified = await request.post('/api/auth/verify-email', { headers, data: { token } })
  expect(verified.status()).toBe(200)
  return (await verified.json()).user
}
export const test = base.extend<{}, { accounts: { author: Account; reader: Account } }>({
  accounts: [async ({ playwright }, use) => {
    const request = await playwright.request.newContext({ baseURL: process.env.TEST_BASE_URL })
    const saved = await readFile('test-results/.accounts.json', 'utf8').then(JSON.parse).catch(() => ({}))
    const author = saved.author ?? await createAccount(request, 'Автор')
    await writeFile('test-results/.accounts.json', JSON.stringify({ author }))
    const reader = saved.reader ?? await createAccount(request, 'Участник')
    await writeFile('test-results/.accounts.json', JSON.stringify({ author, reader }))
    await request.dispose()
    await use({ author, reader })
  }, { scope: 'worker' }],
})
export { expect }
export async function signIn(page: Page, account: Account) {
  const response = await page.request.post('/api/auth/login', { headers, data: { email: account.email, password } })
  expect(response.status()).toBe(200)
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Библиотека', exact: true })).toBeVisible()
}
export async function createCourse(request: APIRequestContext, title: string, workspace = 'personal') {
  const result = await request.post('/api/courses', { headers, data: { title, workspace } })
  expect(result.status()).toBe(201)
  return (await result.json()).id as string
}
export async function capture(page: Page, name: string) {
  await page.evaluate(() => document.fonts.ready)
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }))
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  const stamp = new Date().toISOString().replace(/[-:]/g, '').slice(0, 15)
  const path = shots + name + '-' + stamp + '.png'
  await page.screenshot({ path, animations: 'disabled' })
  await copyFile(path, shots + name + '.png')
}
