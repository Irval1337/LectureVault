import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) { return twMerge(clsx(inputs)) }

export function downloadText(text: string, filename: string) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown;charset=utf-8' }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename.replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_').slice(0, 120)
  anchor.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function plural(count: number, forms: [string, string, string]) {
  const n = Math.abs(count) % 100
  return `${count} ${n >= 11 && n <= 14 ? forms[2] : forms[n % 10 === 1 ? 0 : n % 10 >= 2 && n % 10 <= 4 ? 1 : 2]}`
}

export function formatDate(date: number) {
  return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long' }).format(date)
}
