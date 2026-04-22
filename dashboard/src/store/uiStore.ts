import { create } from 'zustand'

export type ThemePreference = 'light' | 'dark' | 'system'

interface UIState {
  sidebarCollapsed: boolean
  showOrderModal: boolean
  orderModalSymbol: string
  theme: ThemePreference
  tradebotTab: 'positions' | 'rules' | 'insights' | 'activity'

  setSidebarCollapsed: (v: boolean) => void
  toggleSidebar: () => void
  openOrderModal: (symbol?: string) => void
  closeOrderModal: () => void
  setTheme: (t: ThemePreference) => void
  setTradebotTab: (tab: 'positions' | 'rules' | 'insights' | 'activity') => void
}

function applyTheme(pref: ThemePreference) {
  localStorage.setItem('theme', pref)
  const resolved: 'light' | 'dark' =
    pref === 'system'
      ? window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
      : pref
  document.documentElement.setAttribute('data-theme', resolved)
}

export const useUIStore = create<UIState>((set) => ({
  sidebarCollapsed: false,
  showOrderModal: false,
  orderModalSymbol: '',
  tradebotTab: 'positions',
  theme: ((): ThemePreference => {
    try {
      const stored = localStorage.getItem('theme')
      if (stored === 'dark' || stored === 'light' || stored === 'system') return stored
    } catch {
      /* SSR / test env */
    }
    return 'system'
  })(),

  setSidebarCollapsed: (v) => set({ sidebarCollapsed: v }),
  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
  openOrderModal: (symbol = '') => set({ showOrderModal: true, orderModalSymbol: symbol }),
  closeOrderModal: () => set({ showOrderModal: false }),
  setTheme: (t) => {
    applyTheme(t)
    set({ theme: t })
  },
  setTradebotTab: (tab) => set({ tradebotTab: tab }),
}))
