/**
 * Theme utilities — read/write the active theme preference.
 *
 * The resolved theme is applied via `data-theme` on <html>.
 * Tailwind's `darkMode: ['attribute', '[data-theme="dark"]']` picks it up.
 */

export type ThemeValue = 'light' | 'dark' | 'system'

/** Read the stored preference (never 'system' resolved). */
export function getThemePref(): ThemeValue {
  const stored = localStorage.getItem('theme')
  if (stored === 'dark' || stored === 'light' || stored === 'system') return stored
  return 'system'
}

/** Resolve 'system' → actual 'light' | 'dark'. */
export function resolveTheme(pref: ThemeValue): 'light' | 'dark' {
  if (pref === 'system') {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  }
  return pref
}

/**
 * Apply a theme preference: persist to localStorage and update the DOM.
 * Returns the resolved value ('light' or 'dark').
 */
export function setTheme(pref: ThemeValue): 'light' | 'dark' {
  localStorage.setItem('theme', pref)
  const resolved = resolveTheme(pref)
  document.documentElement.setAttribute('data-theme', resolved)
  return resolved
}

/** Read the currently active resolved theme directly from the DOM. */
export function getActiveTheme(): 'light' | 'dark' {
  return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light'
}
