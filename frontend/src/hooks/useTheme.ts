import { useSyncExternalStore } from 'react'

/** The colour theme: a per-browser preference, kept in localStorage.
 *  It sets <html data-theme>, which swaps the CSS variables every colour class
 *  reads (tailwind.config.js). index.html applies the stored choice before the
 *  first paint, so a light theme doesn't flash dark on load. */
export type ThemeChoice = 'dark' | 'light' | 'system'
export type Theme = 'dark' | 'light'

const KEY = 'sfs-theme'
const prefersLight = () => window.matchMedia?.('(prefers-color-scheme: light)')

export function getThemeChoice(): ThemeChoice {
  try {
    const v = localStorage.getItem(KEY)
    if (v === 'dark' || v === 'light' || v === 'system') return v
  } catch { /* storage blocked: default */ }
  return 'dark'
}

const resolve = (c: ThemeChoice): Theme => (c === 'system' ? (prefersLight()?.matches ? 'light' : 'dark') : c)
const listeners = new Set<() => void>()

function apply() {
  document.documentElement.dataset.theme = resolve(getThemeChoice())
  listeners.forEach((l) => l())
}

export function setThemeChoice(c: ThemeChoice) {
  try { localStorage.setItem(KEY, c) } catch { /* still applies for this page */ }
  apply()
}

/** Call once at startup: applies the choice and follows the OS setting
 *  (for "system") and other tabs of the app. */
export function initTheme() {
  apply()
  prefersLight()?.addEventListener?.('change', () => { if (getThemeChoice() === 'system') apply() })
  window.addEventListener('storage', (e) => { if (e.key === KEY) apply() })
}

const subscribe = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l) } }
const current = (): Theme => (document.documentElement.dataset.theme === 'light' ? 'light' : 'dark')

/** The theme in effect now, for components that pick their own colours
 *  (Monaco, React Flow, Graphic Walker, charts). */
export const useTheme = () => useSyncExternalStore(subscribe, current)
export const useThemeChoice = () => useSyncExternalStore(subscribe, getThemeChoice)
export const monacoTheme = (t: Theme) => (t === 'light' ? 'vs' : 'vs-dark')
