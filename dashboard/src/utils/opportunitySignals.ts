import type { MarketQuote } from '@/types'

export type OpportunitySignalKind = 'buy_opportunity' | 'sell_risk' | 'neutral'

export interface OpportunitySignal {
  symbol: string
  kind: OpportunitySignalKind
  confidence: number
  sentimentProxy: number
  score: number
  reasons: string[]
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

function toNumber(value: unknown): number | null {
  const num = Number(value)
  return Number.isFinite(num) ? num : null
}

export function buildOpportunitySignal(quote: MarketQuote): OpportunitySignal | null {
  if (!quote?.symbol || !Number.isFinite(quote.price) || quote.price <= 0) return null
  if ((quote.stale_s ?? 0) > 90) return null

  const discountFromHigh = (() => {
    const high = toNumber(quote.year_high)
    if (high == null || high <= 0) return null
    return ((high - quote.price) / high) * 100
  })()

  const reboundFromLow = (() => {
    const low = toNumber(quote.year_low)
    if (low == null || low <= 0) return null
    return ((quote.price - low) / low) * 100
  })()

  const volumeRatio = (() => {
    const vol = toNumber(quote.volume)
    const avg = toNumber(quote.avg_volume)
    if (vol == null || avg == null || avg <= 0) return null
    return vol / avg
  })()

  const changePct = toNumber(quote.change_pct) ?? 0
  const heavyVolume = volumeRatio != null && volumeRatio >= 1.5
  const deepDiscount = discountFromHigh != null && discountFromHigh >= 20
  const nearHigh = discountFromHigh != null && discountFromHigh <= 5

  let buyScore = 0
  let sellScore = 0
  const buyReasons: string[] = []
  const sellReasons: string[] = []

  if (deepDiscount) {
    buyScore += 35
    buyReasons.push(`-${Math.round(discountFromHigh!)}% from 52W high`)
  }
  if (changePct <= -3) {
    buyScore += 25
    buyReasons.push(`strong pullback (${changePct.toFixed(1)}%)`)
  }
  if (reboundFromLow != null && reboundFromLow <= 18) {
    buyScore += 14
    buyReasons.push(`near 52W low (+${Math.round(reboundFromLow)}%)`)
  }
  if (heavyVolume && changePct < 0) {
    buyScore += 18
    buyReasons.push(`capitulation volume (${volumeRatio!.toFixed(1)}x)`)
  }

  if (nearHigh) {
    sellScore += 35
    sellReasons.push(`near 52W high (-${Math.round(discountFromHigh!)}%)`)
  }
  if (changePct >= 4) {
    sellScore += 25
    sellReasons.push(`extended rally (+${changePct.toFixed(1)}%)`)
  }
  if (changePct >= 8) {
    sellScore += 10
    sellReasons.push('overextended move')
  }
  if (heavyVolume && changePct > 0) {
    sellScore += 18
    sellReasons.push(`crowded volume (${volumeRatio!.toFixed(1)}x)`)
  }

  const sentimentProxy = clamp(
    Math.round((changePct * 8) + ((volumeRatio ?? 1) - 1) * 12),
    -100,
    100,
  )

  const net = buyScore - sellScore
  if (buyScore >= 50 && net >= 12) {
    return {
      symbol: quote.symbol,
      kind: 'buy_opportunity',
      confidence: clamp(Math.round(buyScore), 50, 99),
      sentimentProxy,
      score: buyScore,
      reasons: buyReasons.slice(0, 3),
    }
  }

  if (sellScore >= 50 && net <= -12) {
    return {
      symbol: quote.symbol,
      kind: 'sell_risk',
      confidence: clamp(Math.round(sellScore), 50, 99),
      sentimentProxy,
      score: sellScore,
      reasons: sellReasons.slice(0, 3),
    }
  }

  return {
    symbol: quote.symbol,
    kind: 'neutral',
    confidence: clamp(Math.round(Math.max(buyScore, sellScore)), 0, 49),
    sentimentProxy,
    score: Math.max(buyScore, sellScore),
    reasons: ['mixed setup'],
  }
}

export function rankOpportunitySignals(quotes: MarketQuote[]): OpportunitySignal[] {
  return quotes
    .map(buildOpportunitySignal)
    .filter((signal): signal is OpportunitySignal => signal != null)
    .sort((a, b) => b.score - a.score)
}
