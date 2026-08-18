import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ScanResultRow } from '@/types'

vi.mock('@/services/api', () => ({
  runScan: vi.fn(),
  enrichSymbols: vi.fn(),
}))

import * as api from '@/services/api'
import { useScreenerStore } from '@/store/screenerStore'

const previousResult: ScanResultRow = {
  symbol: 'OLD',
  price: 10,
  change_pct: 0,
  volume: 100,
  indicators: {},
  screener_score: 1,
  setup: 'mixed',
  relative_volume: 1,
  momentum_20d: 0,
  trend_strength: 0,
  notes: [],
}

const nextResult: ScanResultRow = {
  ...previousResult,
  symbol: 'NEW',
  price: 20,
}

beforeEach(() => {
  vi.clearAllMocks()
  useScreenerStore.setState({
    results: [previousResult],
    skippedSymbols: [],
    enriched: { OLD: { symbol: 'OLD', name: 'Old Company' } },
    selectedUniverse: 'sp500',
    customSymbols: '',
    interval: '1d',
    period: '1y',
    scanning: false,
    elapsedMs: 10,
    totalSymbols: 1,
  })
  vi.mocked(api.enrichSymbols).mockResolvedValue([])
})

describe('screenerStore runScan', () => {
  it('ignores a second request while a scan is in progress', async () => {
    let resolveScan!: (value: Awaited<ReturnType<typeof api.runScan>>) => void
    vi.mocked(api.runScan).mockReturnValue(new Promise((resolve) => {
      resolveScan = resolve
    }))

    const first = useScreenerStore.getState().runScan()
    const second = useScreenerStore.getState().runScan()

    expect(api.runScan).toHaveBeenCalledTimes(1)
    resolveScan({ results: [], skipped_symbols: [], elapsed_ms: 1, total_symbols: 0 })
    await Promise.all([first, second])
  })

  it('keeps the previous result visible until its replacement arrives', async () => {
    let resolveScan!: (value: Awaited<ReturnType<typeof api.runScan>>) => void
    vi.mocked(api.runScan).mockReturnValue(new Promise((resolve) => {
      resolveScan = resolve
    }))

    const pending = useScreenerStore.getState().runScan()

    expect(useScreenerStore.getState().scanning).toBe(true)
    expect(useScreenerStore.getState().results).toEqual([previousResult])

    resolveScan({
      results: [nextResult],
      skipped_symbols: [],
      elapsed_ms: 25,
      total_symbols: 2,
    })
    await pending

    const state = useScreenerStore.getState()
    expect(state.scanning).toBe(false)
    expect(state.results).toEqual([nextResult])
    expect(state.enriched).toEqual({})
  })
})
