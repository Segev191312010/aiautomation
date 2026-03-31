import { create } from 'zustand'
import type {
  EnrichResult,
  ScanFilter,
  ScanResultRow,
  ScreenerPreset,
  UniverseInfo,
} from '@/types'
import * as api from '@/services/api'

interface ScreenerState {
  results:          ScanResultRow[]
  skippedSymbols:   string[]
  enriched:         Record<string, EnrichResult>
  presets:          ScreenerPreset[]
  universes:        UniverseInfo[]
  filters:          ScanFilter[]
  selectedUniverse: string
  customSymbols:    string
  interval:         string
  period:           string
  scanning:         boolean
  enriching:        boolean
  presetsLoaded:    boolean
  elapsedMs:        number
  totalSymbols:     number

  addFilter:        () => void
  removeFilter:     (index: number) => void
  updateFilter:     (index: number, filter: ScanFilter) => void
  setFilters:       (filters: ScanFilter[]) => void
  setUniverse:      (universe: string) => void
  setCustomSymbols: (symbols: string) => void
  setInterval:      (interval: string) => void
  setPeriod:        (period: string) => void

  loadPresets:      () => Promise<void>
  loadUniverses:    () => Promise<void>
  applyPreset:      (preset: ScreenerPreset) => void
  savePreset:       (name: string) => Promise<void>
  deletePreset:     (id: string) => Promise<void>

  runScan:          () => Promise<void>
  enrichResults:    () => Promise<void>
}

function makeDefaultFilter(): ScanFilter {
  return {
    id: crypto.randomUUID(),
    indicator: 'RSI',
    params: { length: 14 },
    operator: 'LT',
    value: { type: 'number', number: 30 },
  }
}

export const useScreenerStore = create<ScreenerState>((set, get) => ({
  results:          [],
  skippedSymbols:   [],
  enriched:         {},
  presets:          [],
  universes:        [],
  filters:          [makeDefaultFilter()],
  selectedUniverse: 'sp500',
  customSymbols:    '',
  interval:         '1d',
  period:           '1y',
  scanning:         false,
  enriching:        false,
  presetsLoaded:    false,
  elapsedMs:        0,
  totalSymbols:     0,

  addFilter: () =>
    set((s) => ({ filters: [...s.filters, makeDefaultFilter()] })),

  removeFilter: (index) =>
    set((s) => ({
      filters: s.filters.length > 1
        ? s.filters.filter((_, i) => i !== index)
        : s.filters,
    })),

  updateFilter: (index, filter) =>
    set((s) => ({
      filters: s.filters.map((f, i) => (i === index ? filter : f)),
    })),

  setFilters: (filters) => set({ filters }),

  setUniverse: (universe) => set({ selectedUniverse: universe }),

  setCustomSymbols: (symbols) => set({ customSymbols: symbols }),

  setInterval: (interval) => set({ interval }),

  setPeriod: (period) => set({ period }),

  loadPresets: async () => {
    try {
      const presets = await api.fetchScreenerPresets()
      set({ presets, presetsLoaded: true })
    } catch {
      set({ presetsLoaded: true })
    }
  },

  loadUniverses: async () => {
    try {
      const universes = await api.fetchUniverses()
      set({ universes })
    } catch {
      // backend offline
    }
  },

  applyPreset: (preset) =>
    set({ filters: preset.filters.map((f) => ({ ...f, id: crypto.randomUUID() })) }),

  savePreset: async (name) => {
    const preset = await api.saveScreenerPreset(name, get().filters)
    set((s) => ({ presets: [...s.presets, preset] }))
  },

  deletePreset: async (id) => {
    await api.deleteScreenerPreset(id)
    set((s) => ({ presets: s.presets.filter((p) => p.id !== id) }))
  },

  runScan: async () => {
    const { filters, selectedUniverse, customSymbols, interval, period } = get()
    set({ scanning: true, results: [], skippedSymbols: [], enriched: {}, elapsedMs: 0, totalSymbols: 0 })
    try {
      const symbols = selectedUniverse === 'custom'
        ? customSymbols.split(',').map((s) => s.trim()).filter(Boolean)
        : undefined
      const resp = await api.runScan({
        universe: selectedUniverse,
        symbols,
        filters,
        interval,
        period,
        limit: 100,
      })
      set({
        results: resp.results,
        skippedSymbols: resp.skipped_symbols,
        elapsedMs: resp.elapsed_ms ?? 0,
        totalSymbols: resp.total_symbols ?? 0,
      })
      // Auto-enrich (await so scanning spinner covers enrichment)
      if (resp.results.length > 0) {
        await get().enrichResults()
      }
    } finally {
      set({ scanning: false })
    }
  },

  enrichResults: async () => {
    const { results } = get()
    if (results.length === 0) return
    set({ enriching: true })
    try {
      const symbols = results.map((r) => r.symbol)
      const enriched = await api.enrichSymbols(symbols)
      const map: Record<string, EnrichResult> = {}
      enriched.forEach((e) => { map[e.symbol] = e })
      set({ enriched: map })
    } catch {
      // error
    } finally {
      set({ enriching: false })
    }
  },
}))
