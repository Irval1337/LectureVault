import { defineConfig } from '@playwright/test'
import { existsSync, readdirSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'

function cachedChromium() {
  if (process.env.CHROMIUM_PATH) return process.env.CHROMIUM_PATH
  const root = join(homedir(), '.cache/ms-playwright')
  if (!existsSync(root)) return undefined
  return readdirSync(root).filter(name => /^chromium-\d+$/.test(name)).sort((a, b) => Number(b.split('-')[1]) - Number(a.split('-')[1]))
    .flatMap(name => [join(root, name, 'chrome-linux64/chrome'), join(root, name, 'chrome-linux/chrome')]).find(existsSync)
}

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 45000,
  expect: { timeout: 20000 },
  workers: 1,
  reporter: 'list',
  use: {
    baseURL: process.env.TEST_BASE_URL ?? 'http://127.0.0.1:4173',
    viewport: { width: 1440, height: 1000 },
    locale: 'ru-RU',
    launchOptions: { executablePath: cachedChromium() },
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },

})
