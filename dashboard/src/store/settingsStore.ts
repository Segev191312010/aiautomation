import { create } from 'zustand'
import type { UserSettings } from '@/types'

interface SettingsState {
  settings:    UserSettings | null
  loading:     boolean

  setSettings: (s: UserSettings) => void
  setLoading:  (v: boolean) => void
}

export const useSettingsStore = create<SettingsState>((set) => ({
  settings: null,
  loading:  false,

  setSettings: (s) => set({ settings: s }),
  setLoading:  (v) => set({ loading: v }),
}))
