import type { SectorRotation } from '@/types'

export const SECTOR_ETFS = [
  { symbol: 'XLK',  name: 'Technology' },
  { symbol: 'XLV',  name: 'Health Care' },
  { symbol: 'XLF',  name: 'Financials' },
  { symbol: 'XLY',  name: 'Consumer Disc.' },
  { symbol: 'XLP',  name: 'Consumer Staples' },
  { symbol: 'XLE',  name: 'Energy' },
  { symbol: 'XLI',  name: 'Industrials' },
  { symbol: 'XLB',  name: 'Materials' },
  { symbol: 'XLRE', name: 'Real Estate' },
  { symbol: 'XLU',  name: 'Utilities' },
  { symbol: 'XLC',  name: 'Communication' },
] as const

export type Quadrant = SectorRotation['quadrant']

export const Q_COLORS: Record<Quadrant, { dot: string; badge: string; text: string; svgFill: string; svgStroke: string; svgDot: string }> = {
  LEADING:   { dot: 'bg-emerald-500', badge: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30', text: 'text-emerald-400', svgFill: 'rgba(16,185,129,0.08)', svgStroke: '#059669', svgDot: '#34d399' },
  IMPROVING: { dot: 'bg-blue-400',    badge: 'bg-blue-400/10 text-blue-400 border-blue-500/30',          text: 'text-blue-400',    svgFill: 'rgba(96,165,250,0.08)',  svgStroke: '#2563eb', svgDot: '#60a5fa' },
  WEAKENING: { dot: 'bg-amber-400',   badge: 'bg-amber-400/10 text-amber-400 border-amber-500/30',      text: 'text-amber-400',   svgFill: 'rgba(251,191,36,0.08)',  svgStroke: '#d97706', svgDot: '#fbbf24' },
  LAGGING:   { dot: 'bg-red-500',     badge: 'bg-red-500/10 text-red-400 border-red-500/30',             text: 'text-red-400',     svgFill: 'rgba(239,68,68,0.08)',   svgStroke: '#dc2626', svgDot: '#f87171' },
}

export const Q_LABEL: Record<Quadrant, string> = {
  LEADING: 'Leading', IMPROVING: 'Improving', WEAKENING: 'Weakening', LAGGING: 'Lagging',
}

export const ROTATION_ARROWS: Record<Quadrant, string> = {
  LEADING: '\u2197', IMPROVING: '\u2191', WEAKENING: '\u2198', LAGGING: '\u2193',
}

export type HeatmapSortKey = '1w' | '1m' | '3m' | '6m' | '1y'
