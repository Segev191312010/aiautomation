import { useEffect, useCallback } from 'react'
import { useUIStore, useMarketStore } from '@/store'
import type { AppRoute } from '@/types'

// ── Route shortcuts: Ctrl+1 … Ctrl+9 ────────────────────────────────────────

const DIGIT_ROUTES: Record<string, AppRoute> = {
  '1': 'dashboard',
  '2': 'tradebot',
  '3': 'market',
  '4': 'screener',
  '5': 'stock',
  '6': 'backtest',
  '7': 'alerts',
  '8': 'analytics',
  '9': 'settings',
}

// ── Shortcut definitions (shown in help modal) ───────────────────────────────

export interface ShortcutDef {
  key: string          // human-readable key combo
  description: string
  group: string
}

export const SHORTCUT_DEFS: ShortcutDef[] = [
  { group: 'Navigation',    key: 'Ctrl+1',   description: 'Go to Dashboard' },
  { group: 'Navigation',    key: 'Ctrl+2',   description: 'Go to TradeBot' },
  { group: 'Navigation',    key: 'Ctrl+3',   description: 'Go to Market' },
  { group: 'Navigation',    key: 'Ctrl+4',   description: 'Go to Screener' },
  { group: 'Navigation',    key: 'Ctrl+5',   description: 'Go to Stock Analysis' },
  { group: 'Navigation',    key: 'Ctrl+6',   description: 'Go to Backtest' },
  { group: 'Navigation',    key: 'Ctrl+7',   description: 'Go to Alerts' },
  { group: 'Navigation',    key: 'Ctrl+8',   description: 'Go to Analytics' },
  { group: 'Navigation',    key: 'Ctrl+9',   description: 'Go to Settings' },
  { group: 'UI',            key: 'Ctrl+K',   description: 'Focus ticker search' },
  { group: 'UI',            key: 'Ctrl+\\',  description: 'Toggle sidebar' },
  { group: 'UI',            key: 'Escape',   description: 'Close modals / dropdowns' },
  { group: 'UI',            key: '?',        description: 'Show keyboard shortcuts' },
]

// ── Custom events ─────────────────────────────────────────────────────────────

export const SHORTCUT_FOCUS_SEARCH = 'shortcut:focus-search'
export const SHORTCUT_SHOW_HELP    = 'shortcut:show-help'

function emit(name: string) {
  window.dispatchEvent(new CustomEvent(name))
}

// ── Hook ─────────────────────────────────────────────────────────────────────

interface Options {
  /** When true, skip all handlers (e.g. when an input is focused). */
  disabled?: boolean
}

export function useKeyboardShortcuts({ disabled = false }: Options = {}) {
  const setRoute   = useUIStore((s) => s.setRoute)
  const toggleSidebar = useUIStore((s) => s.toggleSidebar)
  const closeOrderModal = useUIStore((s) => s.closeOrderModal)

  const handle = useCallback((e: KeyboardEvent) => {
    if (disabled) return

    const tag = (e.target as HTMLElement)?.tagName ?? ''
    const inInput = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT'

    // Ctrl+K — focus search (always active, even in inputs)
    if (e.ctrlKey && e.key === 'k') {
      e.preventDefault()
      emit(SHORTCUT_FOCUS_SEARCH)
      return
    }

    // Ctrl+\ — toggle sidebar (always active)
    if (e.ctrlKey && e.key === '\\') {
      e.preventDefault()
      toggleSidebar()
      return
    }

    // Ctrl+1-9 — navigate routes
    if (e.ctrlKey && DIGIT_ROUTES[e.key]) {
      e.preventDefault()
      setRoute(DIGIT_ROUTES[e.key])
      return
    }

    // Keys below only fire when NOT in an input
    if (inInput) return

    // ? — show shortcut help
    if (e.key === '?' && !e.ctrlKey && !e.altKey) {
      emit(SHORTCUT_SHOW_HELP)
      return
    }

    // Escape — close modals
    if (e.key === 'Escape') {
      closeOrderModal()
      return
    }
  }, [disabled, setRoute, toggleSidebar, closeOrderModal])

  useEffect(() => {
    window.addEventListener('keydown', handle)
    return () => window.removeEventListener('keydown', handle)
  }, [handle])
}
