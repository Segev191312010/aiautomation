import { describe, it, expect, beforeEach } from 'vitest'
import { useScreenerStore } from '@/store'
import type { ScanFilter, ScreenerPreset } from '@/types'

// Reset store between tests
beforeEach(() => {
  useScreenerStore.setState({
    results: [],
    skippedSymbols: [],
    enriched: {},
    presets: [],
    universes: [],
    filters: [{
      indicator: 'RSI',
      params: { length: 14 },
      operator: 'LT',
      value: { type: 'number', number: 30 },
    }],
    selectedUniverse: 'sp500',
    customSymbols: '',
    interval: '1d',
    period: '1y',
    scanning: false,
    enriching: false,
    presetsLoaded: false,
  })
})

// ── Filter management ──────────────────────────────────────────────────────

describe('ScreenerStore filter management', () => {
  it('starts with one default filter', () => {
    const { filters } = useScreenerStore.getState()
    expect(filters).toHaveLength(1)
    expect(filters[0].indicator).toBe('RSI')
  })

  it('adds a filter', () => {
    useScreenerStore.getState().addFilter()
    const { filters } = useScreenerStore.getState()
    expect(filters).toHaveLength(2)
  })

  it('removes a filter (keeps at least one)', () => {
    useScreenerStore.getState().addFilter()
    useScreenerStore.getState().removeFilter(0)
    const { filters } = useScreenerStore.getState()
    expect(filters).toHaveLength(1)
  })

  it('does not remove the last filter', () => {
    useScreenerStore.getState().removeFilter(0)
    const { filters } = useScreenerStore.getState()
    expect(filters).toHaveLength(1)
  })

  it('updates a filter', () => {
    const newFilter: ScanFilter = {
      indicator: 'SMA',
      params: { length: 50 },
      operator: 'GT',
      value: { type: 'indicator', indicator: 'SMA', params: { length: 200 } },
    }
    useScreenerStore.getState().updateFilter(0, newFilter)
    const { filters } = useScreenerStore.getState()
    expect(filters[0].indicator).toBe('SMA')
    expect(filters[0].operator).toBe('GT')
  })

  it('setFilters replaces all filters', () => {
    const newFilters: ScanFilter[] = [
      { indicator: 'EMA', params: { length: 20 }, operator: 'GTE', value: { type: 'number', number: 100 } },
      { indicator: 'ATR', params: { length: 14 }, operator: 'LT', value: { type: 'number', number: 5 } },
    ]
    useScreenerStore.getState().setFilters(newFilters)
    const { filters } = useScreenerStore.getState()
    expect(filters).toHaveLength(2)
    expect(filters[0].indicator).toBe('EMA')
    expect(filters[1].indicator).toBe('ATR')
  })
})

// ── Universe selection ─────────────────────────────────────────────────────

describe('ScreenerStore universe selection', () => {
  it('defaults to sp500', () => {
    expect(useScreenerStore.getState().selectedUniverse).toBe('sp500')
  })

  it('sets universe', () => {
    useScreenerStore.getState().setUniverse('nasdaq100')
    expect(useScreenerStore.getState().selectedUniverse).toBe('nasdaq100')
  })

  it('sets custom symbols', () => {
    useScreenerStore.getState().setCustomSymbols('AAPL, MSFT, TSLA')
    expect(useScreenerStore.getState().customSymbols).toBe('AAPL, MSFT, TSLA')
  })
})

// ── Timeframe ──────────────────────────────────────────────────────────────

describe('ScreenerStore timeframe', () => {
  it('defaults to 1d / 1y', () => {
    const s = useScreenerStore.getState()
    expect(s.interval).toBe('1d')
    expect(s.period).toBe('1y')
  })

  it('sets interval and period', () => {
    useScreenerStore.getState().setInterval('1h')
    useScreenerStore.getState().setPeriod('3mo')
    const s = useScreenerStore.getState()
    expect(s.interval).toBe('1h')
    expect(s.period).toBe('3mo')
  })
})

// ── Preset management ──────────────────────────────────────────────────────

describe('ScreenerStore preset management', () => {
  it('applies a preset', () => {
    const preset: ScreenerPreset = {
      id: 'test-1',
      name: 'Test Preset',
      filters: [
        { indicator: 'SMA', params: { length: 50 }, operator: 'CROSSES_ABOVE', value: { type: 'indicator', indicator: 'SMA', params: { length: 200 } } },
      ],
      built_in: true,
      created_at: '2024-01-01T00:00:00Z',
    }
    useScreenerStore.getState().applyPreset(preset)
    const { filters } = useScreenerStore.getState()
    expect(filters).toHaveLength(1)
    expect(filters[0].indicator).toBe('SMA')
    expect(filters[0].operator).toBe('CROSSES_ABOVE')
  })
})
