import { useSyncExternalStore } from 'react'
import { Moon, Sun } from 'lucide-react'

const key = 'lecturevault.theme'
export const isLight = () => document.documentElement.dataset.theme === 'light'
export function setTheme(light: boolean) {
  document.documentElement.dataset.theme = light ? 'light' : 'dark'
  try { localStorage.setItem(key, light ? 'light' : 'dark') } catch { /* apply for this session */ }
  window.dispatchEvent(new Event('lecturevault-theme'))
}
export function initializeTheme(previous?: 'dark' | 'light') {
  try {
    const stored = localStorage.getItem(key)
    if (previous && !stored) setTheme(previous === 'light')
    else document.documentElement.dataset.theme = stored === 'light' ? 'light' : 'dark'
  } catch { /* default palette */ }
}
export function subscribeTheme(listener: () => void) {
  const sync = (event: StorageEvent) => { if (event.key === key) { initializeTheme(); listener() } }
  window.addEventListener('lecturevault-theme', listener)
  window.addEventListener('storage', sync)
  return () => { window.removeEventListener('lecturevault-theme', listener); window.removeEventListener('storage', sync) }
}
export function ThemeButton() {
  const light = useSyncExternalStore(subscribeTheme, isLight)
  const label = light ? 'Включить тёмную тему' : 'Включить светлую тему'
  return <button type="button" className="icon-action workspace-theme" aria-label={label} title={label} onClick={() => setTheme(!light)}>{light ? <Moon size={17}/> : <Sun size={17}/>}</button>
}
