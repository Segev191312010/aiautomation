import { describe, expect, it } from 'vitest'
import type { MarketQuote } from '@/types'
import { buildOpportunitySignal, rankOpportunitySignals } from '@/utils/opportunitySignals'

function quote(overrides: Partial<MarketQuote>): MarketQuote {
  return {
    symbol: 'TEST',
    price: 100,
    change: 0,
    change_pct: 0,
    year_high: 120,
    year_low: 80,
    volume: 1_000_000,
    avg_volume: 1_000_000,
    last_update: new Date().toISOString(),
    ...overrides,
  }
}

describe('opportunitySignals', () => {
  it('classifies deep pullback as buy opportunity', () => {
    const signal = buildOpportunitySignal(
      quote({
        symbol: 'PAYPAL',
        price: 72,
        change_pct: -4.7,
        year_high: 130,
        year_low: 66,
        volume: 2_800_000,
        avg_volume: 1_200_000,
      }),
    )

    expect(signal).not.toBeNull()
    expect(signal?.kind).toBe('buy_opportunity')
    expect(signal?.confidence).toBeGreaterThanOrEqual(50)
  })

  it('classifies extended rally near highs as sell risk', () => {
    const signal = buildOpportunitySignal(
      quote({
        symbol: 'HYPE',
        price: 118,
        change_pct: 8.9,
        year_high: 120,
        year_low: 70,
        volume: 3_000_000,
        avg_volume: 1_300_000,
      }),
    )

    expect(signal).not.toBeNull()
    expect(signal?.kind).toBe('sell_risk')
    expect(signal?.confidence).toBeGreaterThanOrEqual(50)
  })

  it('filters very stale quotes', () => {
    const signal = buildOpportunitySignal(
      quote({
        symbol: 'STALE',
        stale_s: 120,
      }),
    )
    expect(signal).toBeNull()
  })

  it('ranks strongest signal first', () => {
    const ranked = rankOpportunitySignals([
      quote({
        symbol: 'MILD',
        change_pct: -1,
        year_high: 115,
        year_low: 90,
      }),
      quote({
        symbol: 'STRONG',
        price: 70,
        change_pct: -5.2,
        year_high: 140,
        year_low: 66,
        volume: 3_600_000,
        avg_volume: 1_200_000,
      }),
    ])

    expect(ranked.length).toBe(2)
    expect(ranked[0].symbol).toBe('STRONG')
  })
})
