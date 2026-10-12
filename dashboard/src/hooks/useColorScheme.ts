import { useSyncExternalStore } from 'react'

export type ColorScheme = 'light' | 'dark'

const DARK_QUERY = '(prefers-color-scheme: dark)'

function canMatchMedia(): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
}

function subscribe(onChange: () => void): () => void {
  if (!canMatchMedia()) return () => undefined
  const media = window.matchMedia(DARK_QUERY)
  media.addEventListener('change', onChange)
  return () => media.removeEventListener('change', onChange)
}

function readScheme(): ColorScheme {
  return canMatchMedia() && window.matchMedia(DARK_QUERY).matches ? 'dark' : 'light'
}

/** The operating system's light/dark preference, kept live. */
export function useColorScheme(): ColorScheme {
  return useSyncExternalStore(subscribe, readScheme, () => 'light')
}
