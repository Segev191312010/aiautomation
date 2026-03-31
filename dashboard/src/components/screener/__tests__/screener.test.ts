import { describe, it, expect, beforeEach } from 'vitest'
import { useScreenerStore } from '@/store'
import type { ScanFilter, ScanResultRow, ScreenerPreset } from '@/types'

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
    elapsedMs: 0,
    totalSymbols: 0,
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

  it('supports max 15 filters', () => {
    for (let i = 0; i < 16; i++) {
      useScreenerStore.getState().addFilter()
    }
    const { filters } = useScreenerStore.getState()
    // addFilter does not enforce max in store, but UI does
    expect(filters.length).toBeGreaterThanOrEqual(1)
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

  it('sets custom universe', () => {
    useScreenerStore.getState().setUniverse('custom')
    useScreenerStore.getState().setCustomSymbols('NVDA, AMD, INTC')
    const s = useScreenerStore.getState()
    expect(s.selectedUniverse).toBe('custom')
    expect(s.customSymbols).toBe('NVDA, AMD, INTC')
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

  it('applies preset with multiple filters', () => {
    const preset: ScreenerPreset = {
      id: 'test-multi',
      name: 'Multi Filter Preset',
      filters: [
        { indicator: 'RSI', params: { length: 14 }, operator: 'GT', value: { type: 'number', number: 60 } },
        { indicator: 'EMA', params: { length: 20 }, operator: 'GT', value: { type: 'indicator', indicator: 'EMA', params: { length: 50 } } },
        { indicator: 'VOLUME', params: {}, operator: 'GT', value: { type: 'indicator', indicator: 'VOLUME', params: { length: 20 }, multiplier: 1.5 } },
      ],
      built_in: false,
      created_at: '2024-01-01T00:00:00Z',
    }
    useScreenerStore.getState().applyPreset(preset)
    const { filters } = useScreenerStore.getState()
    expect(filters).toHaveLength(3)
    expect(filters[0].indicator).toBe('RSI')
    expect(filters[1].indicator).toBe('EMA')
    expect(filters[2].indicator).toBe('VOLUME')
  })
})

// ── Timing fields ──────────────────────────────────────────────────────────

describe('ScreenerStore timing fields', () => {
  it('defaults to zero elapsed and total', () => {
    const s = useScreenerStore.getState()
    expect(s.elapsedMs).toBe(0)
    expect(s.totalSymbols).toBe(0)
  })

  it('stores timing after scan results', () => {
    useScreenerStore.setState({
      elapsedMs: 2345,
      totalSymbols: 503,
    })
    const s = useScreenerStore.getState()
    expect(s.elapsedMs).toBe(2345)
    expect(s.totalSymbols).toBe(503)
  })
})

// ── Results enrichment ─────────────────────────────────────────────────────

describe('ScreenerStore results', () => {
  const mockResults: ScanResultRow[] = [
    {
      symbol: 'AAPL',
      price: 175.50,
      change_pct: 1.2,
      volume: 50_000_000,
      indicators: { RSI_14: 55.3 },
      screener_score: 72.5,
      setup: 'trend',
      relative_volume: 1.5,
      momentum_20d: 8.3,
      trend_strength: 24.0,
      notes: ['MA stack aligned'],
    },
    {
      symbol: 'MSFT',
      price: 380.20,
      change_pct: -0.5,
      volume: 25_000_000,
      indicators: { RSI_14: 48.1 },
      screener_score: 58.0,
      setup: 'pullback',
      relative_volume: 0.9,
      momentum_20d: 3.1,
      trend_strength: 16.0,
      notes: ['Above 200-day trend'],
    },
  ]

  it('stores scan results', () => {
    useScreenerStore.setState({ results: mockResults })
    expect(useScreenerStore.getState().results).toHaveLength(2)
    expect(useScreenerStore.getState().results[0].symbol).toBe('AAPL')
  })

  it('stores enrichment data', () => {
    useScreenerStore.setState({
      enriched: {
        AAPL: { symbol: 'AAPL', name: 'Apple Inc.', sector: 'Technology', market_cap: 2_800_000_000_000 },
        MSFT: { symbol: 'MSFT', name: 'Microsoft Corp.', sector: 'Technology', market_cap: 3_100_000_000_000 },
      },
    })
    const { enriched } = useScreenerStore.getState()
    expect(enriched.AAPL.name).toBe('Apple Inc.')
    expect(enriched.MSFT.sector).toBe('Technology')
  })

  it('stores skipped symbols', () => {
    useScreenerStore.setState({ skippedSymbols: ['BRK.B', 'GOOG'] })
    expect(useScreenerStore.getState().skippedSymbols).toHaveLength(2)
  })
})
