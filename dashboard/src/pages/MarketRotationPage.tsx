/**
 * MarketRotationPage — Professional sector rotation dashboard.
 *
 * Features:
 *   1. TradingView Ticker Tape — live scrolling sector ETF prices
 *   2. TradingView Stock Heatmap — S&P 500 sector heatmap
 *   3. Live IBKR WebSocket prices for all 11 sector ETFs
 *   4. Rotation Quadrant Chart with momentum arrows
 *   5. Multi-timeframe Performance Heatmap (sortable)
 *   6. TradingView Advanced Chart for selected sector
 *   7. Sector Leaders with expandable per-sector cards
 */
import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import clsx from 'clsx'
import { useUIStore, useStockProfileStore, useMarketStore } from '@/store'
import { wsMdService, type QuoteMsg } from '@/services/ws'
import {
  fetchSectorRotation,
  fetchSectorHeatmap,
  fetchSectorLeaders,
} from '@/services/api'
import type {
  SectorRotation,
  SectorHeatmapRow,
  SectorLeadersResponse,
} from '@/types'

// ── Constants ────────────────────────────────────────────────────────────────

const AUTO_REFRESH_MS = 5 * 60 * 1000

const SECTOR_ETFS = [
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

type Quadrant = SectorRotation['quadrant']

const Q_COLORS: Record<Quadrant, { dot: string; badge: string; text: string; svgFill: string; svgStroke: string; svgDot: string }> = {
  LEADING:   { dot: 'bg-emerald-500', badge: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30', text: 'text-emerald-400', svgFill: 'rgba(16,185,129,0.08)', svgStroke: '#059669', svgDot: '#34d399' },
  IMPROVING: { dot: 'bg-blue-400',    badge: 'bg-blue-400/10 text-blue-400 border-blue-500/30',          text: 'text-blue-400',    svgFill: 'rgba(96,165,250,0.08)',  svgStroke: '#2563eb', svgDot: '#60a5fa' },
  WEAKENING: { dot: 'bg-amber-400',   badge: 'bg-amber-400/10 text-amber-400 border-amber-500/30',      text: 'text-amber-400',   svgFill: 'rgba(251,191,36,0.08)',  svgStroke: '#d97706', svgDot: '#fbbf24' },
  LAGGING:   { dot: 'bg-red-500',     badge: 'bg-red-500/10 text-red-400 border-red-500/30',             text: 'text-red-400',     svgFill: 'rgba(239,68,68,0.08)',   svgStroke: '#dc2626', svgDot: '#f87171' },
}

const Q_LABEL: Record<Quadrant, string> = { LEADING: 'Leading', IMPROVING: 'Improving', WEAKENING: 'Weakening', LAGGING: 'Lagging' }

const ROTATION_ARROWS: Record<Quadrant, string> = { LEADING: '\u2197', IMPROVING: '\u2191', WEAKENING: '\u2198', LAGGING: '\u2193' }

type HeatmapSortKey = '1w' | '1m' | '3m' | '6m' | '1y'

// ── Helpers ──────────────────────────────────────────────────────────────────

function fmtPct(v: number | null | undefined, decimals = 2): string {
  if (v == null) return '--'
  const s = v.toFixed(decimals)
  return v >= 0 ? `+${s}%` : `${s}%`
}

function fmtPrice(v: number | null | undefined): string {
  if (v == null) return '--'
  return `$${v.toFixed(2)}`
}

function pctColor(v: number): string {
  return v >= 0 ? 'text-emerald-400' : 'text-red-400'
}

function heatmapCellColor(v: number): string {
  if (v >= 10)  return 'bg-emerald-500/30 text-emerald-300'
  if (v >= 5)   return 'bg-emerald-500/20 text-emerald-300'
  if (v >= 2)   return 'bg-emerald-500/10 text-emerald-400'
  if (v > 0)    return 'bg-emerald-500/5 text-emerald-400'
  if (v >= -2)  return 'bg-red-500/5 text-red-400'
  if (v >= -5)  return 'bg-red-500/10 text-red-400'
  if (v >= -10) return 'bg-red-500/20 text-red-300'
  return 'bg-red-500/30 text-red-300'
}

// ── TradingView Widget Wrappers ──────────────────────────────────────────────

function TradingViewTickerTape() {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    el.innerHTML = ''
    const wrapper = document.createElement('div')
    wrapper.className = 'tradingview-widget-container'
    const widgetDiv = document.createElement('div')
    widgetDiv.className = 'tradingview-widget-container__widget'
    wrapper.appendChild(widgetDiv)
    const script = document.createElement('script')
    script.src = 'https://s3.tradingview.com/external-embedding/embed-widget-ticker-tape.js'
    script.async = true
    script.type = 'text/javascript'
    script.textContent = JSON.stringify({
      symbols: SECTOR_ETFS.map(s => ({ proName: `AMEX:${s.symbol}`, title: s.name })),
      showSymbolLogo: true,
      isTransparent: true,
      displayMode: 'adaptive',
      colorTheme: 'dark',
      locale: 'en',
    })
    wrapper.appendChild(script)
    el.appendChild(wrapper)
    return () => { el.innerHTML = '' }
  }, [])

  return <div ref={containerRef} className="w-full overflow-hidden" />
}

function TradingViewHeatmap() {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    el.innerHTML = ''
    const wrapper = document.createElement('div')
    wrapper.className = 'tradingview-widget-container'
    wrapper.style.height = '100%'
    wrapper.style.width = '100%'
    const widgetDiv = document.createElement('div')
    widgetDiv.className = 'tradingview-widget-container__widget'
    widgetDiv.style.height = 'calc(100% - 32px)'
    widgetDiv.style.width = '100%'
    wrapper.appendChild(widgetDiv)
    const script = document.createElement('script')
    script.src = 'https://s3.tradingview.com/external-embedding/embed-widget-stock-heatmap.js'
    script.async = true
    script.type = 'text/javascript'
    script.textContent = JSON.stringify({
      exchanges: [],
      dataSource: 'SPX500',
      grouping: 'sector',
      blockSize: 'market_cap_basic',
      blockColor: 'change',
      locale: 'en',
      symbolUrl: '',
      colorTheme: 'dark',
      hasTopBar: true,
      isDataSetEnabled: true,
      isZoomEnabled: true,
      hasSymbolTooltip: true,
      isMonoSize: false,
      width: '100%',
      height: '100%',
    })
    wrapper.appendChild(script)
    el.appendChild(wrapper)
    return () => { el.innerHTML = '' }
  }, [])

  return <div ref={containerRef} className="w-full h-full min-h-[420px]" />
}

function TradingViewChart({ symbol }: { symbol: string }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const prevSymbolRef = useRef(symbol)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    prevSymbolRef.current = symbol
    el.innerHTML = ''
    const wrapper = document.createElement('div')
    wrapper.className = 'tradingview-widget-container'
    wrapper.style.height = '100%'
    wrapper.style.width = '100%'
    const widgetDiv = document.createElement('div')
    widgetDiv.className = 'tradingview-widget-container__widget'
    widgetDiv.style.height = 'calc(100% - 32px)'
    widgetDiv.style.width = '100%'
    wrapper.appendChild(widgetDiv)
    const script = document.createElement('script')
    script.src = 'https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js'
    script.async = true
    script.type = 'text/javascript'
    script.textContent = JSON.stringify({
      autosize: true,
      symbol: `AMEX:${symbol}`,
      interval: 'D',
      timezone: 'America/New_York',
      theme: 'dark',
      style: '1',
      locale: 'en',
      allow_symbol_change: true,
      support_host: 'https://www.tradingview.com',
      hide_side_toolbar: false,
      studies: ['RSI@tv-basicstudies', 'MASimple@tv-basicstudies'],
    })
    wrapper.appendChild(script)
    el.appendChild(wrapper)
    return () => { el.innerHTML = '' }
  }, [symbol])

  return <div ref={containerRef} className="w-full h-full min-h-[400px]" />
}

function TradingViewMiniWidget({ symbol }: { symbol: string }) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    el.innerHTML = ''
    const wrapper = document.createElement('div')
    wrapper.className = 'tradingview-widget-container'
    wrapper.style.height = '100%'
    wrapper.style.width = '100%'
    const widgetDiv = document.createElement('div')
    widgetDiv.className = 'tradingview-widget-container__widget'
    widgetDiv.style.height = 'calc(100% - 32px)'
    widgetDiv.style.width = '100%'
    wrapper.appendChild(widgetDiv)
    const script = document.createElement('script')
    script.src = 'https://s3.tradingview.com/external-embedding/embed-widget-mini-symbol-overview.js'
    script.async = true
    script.type = 'text/javascript'
    script.textContent = JSON.stringify({
      symbol: `AMEX:${symbol}`,
      width: '100%',
      height: '100%',
      locale: 'en',
      dateRange: '1M',
      colorTheme: 'dark',
      isTransparent: true,
      autosize: true,
      largeChartUrl: '',
    })
    wrapper.appendChild(script)
    el.appendChild(wrapper)
    return () => { el.innerHTML = '' }
  }, [symbol])

  return <div ref={containerRef} className="w-full h-full" />
}

// ── Live Sector Prices (IBKR WebSocket) ──────────────────────────────────────

interface LivePrice {
  price: number
  prevPrice: number
  time: number
  source: string
}

function useLiveSectorPrices(): Map<string, LivePrice> {
  const [prices, setPrices] = useState<Map<string, LivePrice>>(new Map())

  useEffect(() => {
    const unsubs: (() => void)[] = []

    for (const s of SECTOR_ETFS) {
      const unsub = wsMdService.subscribe(s.symbol, (msg: QuoteMsg) => {
        setPrices(prev => {
          const next = new Map(prev)
          const existing = prev.get(msg.symbol)
          next.set(msg.symbol, {
            price: msg.price,
            prevPrice: existing?.price ?? msg.price,
            time: msg.time ?? Date.now() / 1000,
            source: msg.source ?? 'ibkr',
          })
          return next
        })
      })
      unsubs.push(unsub)
    }

    return () => unsubs.forEach(u => u())
  }, [])

  return prices
}

// ── Sub-Components ───────────────────────────────────────────────────────────

function QuadrantBadge({ quadrant }: { quadrant: Quadrant }) {
  const c = Q_COLORS[quadrant]
  return (
    <span className={clsx(
      'inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10px] font-mono font-medium uppercase tracking-wider',
      c.badge,
    )}>
      <span className={clsx('h-1.5 w-1.5 rounded-full shrink-0', c.dot)} />
      {Q_LABEL[quadrant]}
    </span>
  )
}

// ── Live Sector Strip ────────────────────────────────────────────────────────

function LiveSectorStrip({
  rotation,
  livePrices,
  selectedSector,
  onSelectSector,
}: {
  rotation: SectorRotation[]
  livePrices: Map<string, LivePrice>
  selectedSector: string
  onSelectSector: (s: string) => void
}) {
  const rotMap = useMemo(() => new Map(rotation.map(r => [r.symbol, r])), [rotation])

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 2xl:grid-cols-11 gap-2">
      {SECTOR_ETFS.map(({ symbol, name }) => {
        const rot = rotMap.get(symbol)
        const live = livePrices.get(symbol)
        const price = live?.price ?? rot?.price ?? null
        const perf1m = rot?.perf_1m ?? null
        const quadrant = rot?.quadrant
        const isSelected = symbol === selectedSector
        const flash = live && live.price !== live.prevPrice
          ? live.price > live.prevPrice ? 'ring-1 ring-emerald-500/40' : 'ring-1 ring-red-500/40'
          : ''

        return (
          <button
            key={symbol}
            type="button"
            onClick={() => onSelectSector(symbol)}
            className={clsx(
              'rounded-lg border p-2.5 text-left transition-all duration-200 hover:border-zinc-700',
              isSelected
                ? 'border-blue-500/50 bg-blue-500/5'
                : 'border-zinc-700/50 bg-zinc-900/40',
              flash,
            )}
          >
            <div className="flex items-center justify-between gap-1 mb-1">
              <span className="font-mono text-xs font-bold text-zinc-600">{symbol}</span>
              {quadrant && (
                <span className={clsx('text-[9px]', Q_COLORS[quadrant].text)}>
                  {ROTATION_ARROWS[quadrant]}
                </span>
              )}
            </div>
            <div className="text-[10px] text-zinc-400 truncate mb-1.5">{name}</div>
            <div className="flex items-baseline justify-between gap-1">
              <span className="font-mono text-sm font-semibold text-zinc-700 tabular-nums">
                {price != null ? fmtPrice(price) : '--'}
              </span>
              {perf1m != null && (
                <span className={clsx('font-mono text-[10px] font-medium tabular-nums', pctColor(perf1m))}>
                  {fmtPct(perf1m, 1)}
                </span>
              )}
            </div>
            {live && (
              <div className="mt-1 flex items-center gap-1">
                <span className="h-1 w-1 rounded-full bg-emerald-500 animate-pulse" />
                <span className="text-[8px] text-zinc-400 uppercase tracking-wider">LIVE</span>
              </div>
            )}
          </button>
        )
      })}
    </div>
  )
}

// ── Rotation Quadrant Chart ──────────────────────────────────────────────────

function RotationQuadrantChart({
  sectors,
  selectedSector,
  onSelectSector,
}: {
  sectors: SectorRotation[]
  selectedSector: string
  onSelectSector: (s: string) => void
}) {
  const [tooltip, setTooltip] = useState<{ x: number; y: number; sector: SectorRotation } | null>(null)
  const svgRef = useRef<SVGSVGElement>(null)

  const W = 560, H = 420
  const PAD = { top: 30, right: 30, bottom: 45, left: 50 }
  const plotW = W - PAD.left - PAD.right
  const plotH = H - PAD.top - PAD.bottom

  const ratios = sectors.map(s => s.rs_ratio)
  const momenta = sectors.map(s => s.rs_momentum)

  const rMin = Math.min(...ratios, 0.85), rMax = Math.max(...ratios, 1.15)
  const mMin = Math.min(...momenta, -5), mMax = Math.max(...momenta, 5)
  const rPad = (rMax - rMin) * 0.12
  const mPad = (mMax - mMin) * 0.12
  const xMin = rMin - rPad, xMax = rMax + rPad
  const yMin = mMin - mPad, yMax = mMax + mPad

  const toX = (v: number) => PAD.left + ((v - xMin) / (xMax - xMin)) * plotW
  const toY = (v: number) => PAD.top + ((yMax - v) / (yMax - yMin)) * plotH

  const cx = toX(1.0)
  const cy = toY(0)

  const volumes = sectors.map(s => s.volume)
  const vMin = Math.min(...volumes), vMax = Math.max(...volumes)
  const toR = (vol: number) => vMax === vMin ? 10 : 7 + ((vol - vMin) / (vMax - vMin)) * 11

  return (
    <div className="relative">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        className="w-full h-auto"
        style={{ fontFamily: 'ui-monospace, monospace' }}
      >
        {/* Quadrant backgrounds */}
        <rect x={cx} y={PAD.top} width={W - PAD.right - cx} height={cy - PAD.top} fill={Q_COLORS.LEADING.svgFill} />
        <rect x={PAD.left} y={PAD.top} width={cx - PAD.left} height={cy - PAD.top} fill={Q_COLORS.IMPROVING.svgFill} />
        <rect x={cx} y={cy} width={W - PAD.right - cx} height={H - PAD.bottom - cy} fill={Q_COLORS.WEAKENING.svgFill} />
        <rect x={PAD.left} y={cy} width={cx - PAD.left} height={H - PAD.bottom - cy} fill={Q_COLORS.LAGGING.svgFill} />

        {/* Grid lines */}
        <line x1={cx} y1={PAD.top} x2={cx} y2={H - PAD.bottom} stroke="#374151" strokeWidth={1} strokeDasharray="4 3" />
        <line x1={PAD.left} y1={cy} x2={W - PAD.right} y2={cy} stroke="#374151" strokeWidth={1} strokeDasharray="4 3" />

        {/* Border */}
        <rect x={PAD.left} y={PAD.top} width={plotW} height={plotH} fill="none" stroke="#1f2937" strokeWidth={1} />

        {/* Quadrant labels */}
        <text x={W - PAD.right - 6} y={PAD.top + 16} textAnchor="end" fontSize={10} fill="#059669" fontWeight={700} letterSpacing="0.1em" opacity={0.7}>LEADING</text>
        <text x={PAD.left + 6} y={PAD.top + 16} textAnchor="start" fontSize={10} fill="#2563eb" fontWeight={700} letterSpacing="0.1em" opacity={0.7}>IMPROVING</text>
        <text x={W - PAD.right - 6} y={H - PAD.bottom - 6} textAnchor="end" fontSize={10} fill="#d97706" fontWeight={700} letterSpacing="0.1em" opacity={0.7}>WEAKENING</text>
        <text x={PAD.left + 6} y={H - PAD.bottom - 6} textAnchor="start" fontSize={10} fill="#dc2626" fontWeight={700} letterSpacing="0.1em" opacity={0.7}>LAGGING</text>

        {/* Axis labels */}
        <text x={W / 2} y={H - 4} textAnchor="middle" fontSize={9} fill="#6b7280" letterSpacing="0.08em">RELATIVE STRENGTH RATIO</text>
        <text x={12} y={H / 2} textAnchor="middle" fontSize={9} fill="#6b7280" letterSpacing="0.08em" transform={`rotate(-90, 12, ${H / 2})`}>RS MOMENTUM</text>

        {/* Rotation clockwise arrows (decorative) */}
        <path
          d={`M ${cx + 30} ${cy - 30} A 40 40 0 0 1 ${cx + 30} ${cy + 30}`}
          fill="none" stroke="#374151" strokeWidth={0.5} strokeDasharray="3 3"
          markerEnd="url(#arrowhead)"
        />
        <defs>
          <marker id="arrowhead" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
            <path d="M 0 0 L 6 3 L 0 6 Z" fill="#374151" />
          </marker>
        </defs>

        {/* Sector dots */}
        {sectors.map(s => {
          const dx = toX(s.rs_ratio)
          const dy = toY(s.rs_momentum)
          const r = toR(s.volume)
          const isSelected = s.symbol === selectedSector

          return (
            <g
              key={s.symbol}
              onClick={() => onSelectSector(s.symbol)}
              onMouseEnter={(e) => {
                const rect = svgRef.current?.getBoundingClientRect()
                if (rect) setTooltip({ x: e.clientX - rect.left, y: e.clientY - rect.top, sector: s })
              }}
              onMouseMove={(e) => {
                const rect = svgRef.current?.getBoundingClientRect()
                if (rect) setTooltip({ x: e.clientX - rect.left, y: e.clientY - rect.top, sector: s })
              }}
              onMouseLeave={() => setTooltip(null)}
              style={{ cursor: 'pointer' }}
            >
              {/* Selected ring */}
              {isSelected && (
                <circle cx={dx} cy={dy} r={r + 4} fill="none" stroke="#3b82f6" strokeWidth={2} opacity={0.6}>
                  <animate attributeName="r" from={r + 3} to={r + 7} dur="1.5s" repeatCount="indefinite" />
                  <animate attributeName="opacity" from="0.6" to="0.1" dur="1.5s" repeatCount="indefinite" />
                </circle>
              )}
              {/* Momentum arrow (trail) */}
              <line
                x1={dx} y1={dy}
                x2={dx + s.rs_momentum * 3} y2={dy - s.rs_momentum * 2}
                stroke={Q_COLORS[s.quadrant].svgStroke} strokeWidth={1.5} opacity={0.4}
                markerEnd="url(#arrowhead)"
              />
              {/* Dot */}
              <circle
                cx={dx} cy={dy} r={r}
                fill={Q_COLORS[s.quadrant].svgDot}
                stroke={isSelected ? '#3b82f6' : Q_COLORS[s.quadrant].svgStroke}
                strokeWidth={isSelected ? 2 : 1.5}
                opacity={0.9}
              />
              {/* Label */}
              <text x={dx} y={dy - r - 4} textAnchor="middle" fontSize={9} fill="#d1d5db" fontWeight={600}>
                {s.symbol}
              </text>
            </g>
          )
        })}
      </svg>

      {/* Tooltip */}
      {tooltip && (
        <div
          className="pointer-events-none absolute z-20 rounded-lg border border-zinc-700 bg-zinc-900 shadow-xl px-3 py-2.5 text-xs"
          style={{ left: Math.min(tooltip.x + 14, 380), top: tooltip.y - 12, minWidth: 200 }}
        >
          <div className="flex items-center gap-2 mb-1.5">
            <span className="font-mono font-bold text-zinc-700 text-sm">{tooltip.sector.symbol}</span>
            <QuadrantBadge quadrant={tooltip.sector.quadrant} />
          </div>
          <div className="text-zinc-500 mb-2 truncate">{tooltip.sector.name}</div>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px]">
            <div className="flex justify-between"><span className="text-zinc-400">RS Ratio</span><span className="font-mono text-zinc-500">{tooltip.sector.rs_ratio.toFixed(3)}</span></div>
            <div className="flex justify-between"><span className="text-zinc-400">Momentum</span><span className="font-mono text-zinc-500">{tooltip.sector.rs_momentum.toFixed(2)}</span></div>
            <div className="flex justify-between"><span className="text-zinc-400">1W</span><span className={clsx('font-mono', pctColor(tooltip.sector.perf_1w))}>{fmtPct(tooltip.sector.perf_1w)}</span></div>
            <div className="flex justify-between"><span className="text-zinc-400">1M</span><span className={clsx('font-mono', pctColor(tooltip.sector.perf_1m))}>{fmtPct(tooltip.sector.perf_1m)}</span></div>
            <div className="flex justify-between"><span className="text-zinc-400">3M</span><span className={clsx('font-mono', pctColor(tooltip.sector.perf_3m))}>{fmtPct(tooltip.sector.perf_3m)}</span></div>
            <div className="flex justify-between"><span className="text-zinc-400">Price</span><span className="font-mono text-zinc-500">{fmtPrice(tooltip.sector.price)}</span></div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Performance Heatmap ──────────────────────────────────────────────────────

function PerformanceHeatmap({
  rows,
  rotationMap,
  onSelectSector,
}: {
  rows: SectorHeatmapRow[]
  rotationMap: Map<string, SectorRotation>
  onSelectSector: (s: string) => void
}) {
  const [sortKey, setSortKey] = useState<HeatmapSortKey>('1m')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  const COLS: { key: HeatmapSortKey; label: string }[] = [
    { key: '1w', label: '1W' },
    { key: '1m', label: '1M' },
    { key: '3m', label: '3M' },
    { key: '6m', label: '6M' },
    { key: '1y', label: '1Y' },
  ]

  const sorted = [...rows].sort((a, b) => {
    const av = a[sortKey] ?? 0, bv = b[sortKey] ?? 0
    return sortDir === 'desc' ? bv - av : av - bv
  })

  const toggleSort = (key: HeatmapSortKey) => {
    if (key === sortKey) setSortDir(d => d === 'desc' ? 'asc' : 'desc')
    else { setSortKey(key); setSortDir('desc') }
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-zinc-700/50">
            <th className="text-left py-2 pr-3 font-mono font-medium text-zinc-400 text-[10px] uppercase tracking-wider min-w-[140px]">Sector</th>
            <th className="text-right py-2 px-2 font-mono text-[10px] text-zinc-400 uppercase tracking-wider">Quadrant</th>
            {COLS.map(col => (
              <th
                key={col.key}
                className={clsx(
                  'text-right py-2 px-2 font-mono font-medium text-[10px] uppercase tracking-wider cursor-pointer select-none transition-colors',
                  sortKey === col.key ? 'text-zinc-600' : 'text-zinc-400 hover:text-zinc-500',
                )}
                onClick={() => toggleSort(col.key)}
              >
                {col.label}{sortKey === col.key && (sortDir === 'desc' ? ' \u25BC' : ' \u25B2')}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map(row => {
            const rot = rotationMap.get(row.symbol)
            return (
              <tr
                key={row.symbol}
                className="border-b border-zinc-800/50 hover:bg-zinc-900/30 cursor-pointer transition-colors"
                onClick={() => onSelectSector(row.symbol)}
              >
                <td className="py-2 pr-3">
                  <div className="flex items-center gap-2">
                    {rot && <span className={clsx('h-2 w-2 rounded-full shrink-0', Q_COLORS[rot.quadrant].dot)} />}
                    <div>
                      <div className="font-mono font-semibold text-zinc-600 text-[11px]">{row.symbol}</div>
                      <div className="text-zinc-400 text-[10px] truncate max-w-[100px]">{row.name}</div>
                    </div>
                  </div>
                </td>
                <td className="py-2 px-2 text-right">
                  {rot && <QuadrantBadge quadrant={rot.quadrant} />}
                </td>
                {COLS.map(col => {
                  const v = row[col.key]
                  return (
                    <td key={col.key} className="py-1.5 px-2 text-right">
                      <span className={clsx(
                        'inline-block rounded px-1.5 py-0.5 font-mono text-[11px] tabular-nums',
                        v != null ? heatmapCellColor(v) : 'text-zinc-400',
                      )}>
                        {v != null ? fmtPct(v) : '--'}
                      </span>
                    </td>
                  )
                })}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── Sector Leaders Card ──────────────────────────────────────────────────────

function SectorLeadersCard({
  sector,
  onNavigateToStock,
}: {
  sector: SectorRotation
  onNavigateToStock: (symbol: string) => void
}) {
  const [leaders, setLeaders] = useState<SectorLeadersResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetchSectorLeaders(sector.symbol, 10, '3mo')
      .then(data => { if (!cancelled) setLeaders(data) })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [sector.symbol])

  const stocks = leaders?.leaders ?? []
  const displayed = expanded ? stocks : stocks.slice(0, 5)

  return (
    <div className="rounded-lg border border-zinc-700/50 bg-zinc-900/30 p-3 flex flex-col gap-2 min-w-[240px]">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-1.5 mb-0.5">
            <span className="font-mono font-bold text-zinc-700 text-sm">{sector.symbol}</span>
            <QuadrantBadge quadrant={sector.quadrant} />
          </div>
          <div className="text-[10px] text-zinc-400">{sector.name}</div>
        </div>
        <div className="text-right">
          <span className={clsx('font-mono font-semibold text-sm tabular-nums', pctColor(sector.perf_1m))}>
            {fmtPct(sector.perf_1m)}
          </span>
          <div className="text-[9px] text-zinc-400">1M</div>
        </div>
      </div>

      <div className="flex gap-3 text-[10px]">
        <div><span className="text-zinc-400">RS</span> <span className="font-mono text-zinc-500">{sector.rs_ratio.toFixed(3)}</span></div>
        <div><span className="text-zinc-400">Mom</span> <span className="font-mono text-zinc-500">{sector.rs_momentum.toFixed(2)}</span></div>
        <div><span className="text-zinc-400">3M</span> <span className={clsx('font-mono', pctColor(sector.perf_3m))}>{fmtPct(sector.perf_3m, 1)}</span></div>
      </div>

      <div className="border-t border-zinc-700/50 pt-2">
        <div className="text-[9px] font-mono uppercase tracking-wider text-zinc-400 mb-1.5">Top Performers</div>
        {loading && !stocks.length ? (
          <div className="space-y-1.5">
            {[...Array(5)].map((_, i) => <div key={i} className="h-3.5 bg-zinc-800/30 rounded animate-pulse" />)}
          </div>
        ) : stocks.length === 0 ? (
          <div className="text-[11px] text-zinc-400 italic">No data</div>
        ) : (
          <div className="space-y-0.5">
            {displayed.map((s, i) => (
              <button
                key={s.symbol}
                type="button"
                onClick={() => onNavigateToStock(s.symbol)}
                className="flex items-center justify-between w-full px-1 py-0.5 rounded hover:bg-zinc-800/30 group transition-colors"
              >
                <span className="flex items-center gap-1.5">
                  <span className="text-[9px] text-zinc-400 font-mono w-3">{i + 1}</span>
                  <span className="font-mono font-semibold text-[11px] text-zinc-500 group-hover:text-blue-400">{s.symbol}</span>
                </span>
                <div className="flex items-center gap-2 text-[10px]">
                  <span className="text-zinc-400">{fmtPrice(s.price)}</span>
                  <span className={clsx('font-mono font-medium tabular-nums', pctColor(s.perf))}>{fmtPct(s.perf)}</span>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>

      {stocks.length > 5 && (
        <button
          type="button"
          onClick={() => setExpanded(e => !e)}
          className="text-[10px] text-zinc-400 hover:text-zinc-500 transition-colors text-center"
        >
          {expanded ? 'Show less' : `View all ${stocks.length}`}
        </button>
      )}
    </div>
  )
}

// ── Loading Skeleton ─────────────────────────────────────────────────────────

function Skeleton({ className }: { className?: string }) {
  return (
    <div className={clsx('rounded-lg border border-zinc-700/30 bg-zinc-900/20 animate-pulse', className)}>
      <div className="p-4 space-y-3">
        <div className="h-3 w-28 bg-zinc-800/40 rounded" />
        <div className="h-5 w-40 bg-zinc-800/40 rounded" />
        <div className="space-y-2 mt-4">
          {[...Array(6)].map((_, i) => <div key={i} className="h-3 bg-zinc-800/30 rounded" />)}
        </div>
      </div>
    </div>
  )
}

// ── Main Page ────────────────────────────────────────────────────────────────

export default function MarketRotationPage() {
  const setRoute = useUIStore(s => s.setRoute)
  const setProfileSymbol = useStockProfileStore(s => s.setSymbol)
  const setSelectedSymbol = useMarketStore(s => s.setSelectedSymbol)

  const [rotation, setRotation] = useState<SectorRotation[]>([])
  const [heatmap, setHeatmap] = useState<SectorHeatmapRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null)
  const [selectedSector, setSelectedSector] = useState('XLK')
  const [activeTab, setActiveTab] = useState<'heatmap' | 'quadrant'>('heatmap')
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const livePrices = useLiveSectorPrices()

  const loadData = useCallback(async () => {
    setError(null)
    try {
      const [rotData, heatData] = await Promise.all([
        fetchSectorRotation(90),
        fetchSectorHeatmap(),
      ])
      setRotation(rotData)
      setHeatmap(heatData)
      setLastUpdate(new Date())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load sector data')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadData()
    timerRef.current = setInterval(loadData, AUTO_REFRESH_MS)
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [loadData])

  const handleNavigateToStock = useCallback((symbol: string) => {
    setSelectedSymbol(symbol)
    setProfileSymbol(symbol)
    setRoute('stock')
  }, [setRoute, setProfileSymbol, setSelectedSymbol])

  const rotationMap = useMemo(() => new Map(rotation.map(s => [s.symbol, s])), [rotation])

  const sortedByMomentum = useMemo(
    () => [...rotation].sort((a, b) => b.rs_momentum - a.rs_momentum),
    [rotation],
  )

  const formatTime = (d: Date) => d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' })

  return (
    <div className="flex flex-col gap-4 pb-8">

      {/* ── Header ───────────────────────────────────────────────── */}
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-sans font-bold text-zinc-700">Market Rotation</h1>
          <p className="text-xs text-zinc-400 mt-0.5">Sector relative strength, momentum rankings & live data</p>
        </div>
        <div className="flex items-center gap-3">
          {lastUpdate && (
            <span className="text-[10px] font-mono text-zinc-400">
              {formatTime(lastUpdate)}
            </span>
          )}
          <div className="flex items-center gap-1">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
            <span className="text-[10px] font-mono text-zinc-400">{livePrices.size} LIVE</span>
          </div>
          <button
            type="button"
            onClick={() => { setLoading(true); loadData() }}
            disabled={loading}
            className={clsx(
              'flex items-center gap-1.5 rounded-md border border-zinc-700 px-2.5 py-1',
              'text-[10px] font-mono text-zinc-500 hover:border-zinc-700 hover:text-zinc-600 transition-colors',
              loading && 'opacity-50 cursor-not-allowed',
            )}
          >
            <svg viewBox="0 0 24 24" fill="currentColor" className={clsx('w-3 h-3', loading && 'animate-spin')}>
              <path d="M17.65 6.35A7.958 7.958 0 0012 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08A5.99 5.99 0 0112 18c-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z" />
            </svg>
            Refresh
          </button>
        </div>
      </div>

      {/* ── Error ────────────────────────────────────────────────── */}
      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/5 px-4 py-3">
          <span className="text-sm text-red-400">{error}</span>
          <button type="button" onClick={loadData} className="ml-3 text-xs text-red-400 hover:text-red-300 underline">Retry</button>
        </div>
      )}

      {/* ── Live Sector ETF Strip ────────────────────────────────── */}
      {loading && !rotation.length ? (
        <div className="grid grid-cols-2 sm:grid-cols-4 xl:grid-cols-6 2xl:grid-cols-11 gap-2">
          {[...Array(11)].map((_, i) => <div key={i} className="h-24 bg-zinc-900/30 rounded-lg animate-pulse border border-zinc-700/30" />)}
        </div>
      ) : (
        <LiveSectorStrip
          rotation={rotation}
          livePrices={livePrices}
          selectedSector={selectedSector}
          onSelectSector={setSelectedSector}
        />
      )}

      {/* ── Tab Navigation ───────────────────────────────────────── */}
      <div className="flex gap-1 border-b border-zinc-700/50">
        {([
          { key: 'heatmap' as const, label: 'S&P 500 Heatmap' },
          { key: 'quadrant' as const, label: 'Rotation Quadrant' },
        ]).map(tab => (
          <button
            key={tab.key}
            type="button"
            onClick={() => setActiveTab(tab.key)}
            className={clsx(
              'px-4 py-2 text-xs font-mono transition-colors border-b-2 -mb-[1px]',
              activeTab === tab.key
                ? 'text-blue-400 border-blue-400'
                : 'text-zinc-400 border-transparent hover:text-zinc-500',
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* ── Main Content Area ────────────────────────────────────── */}
      <div className="grid grid-cols-1 xl:grid-cols-[1fr_380px] gap-4">
        {/* Left: Active tab content */}
        <div className="rounded-lg border border-zinc-700/30 bg-zinc-950/30 overflow-hidden">
          {activeTab === 'heatmap' && (
            <div className="h-[500px]">
              <TradingViewHeatmap />
            </div>
          )}
          {activeTab === 'quadrant' && (
            <div className="p-4">
              <details className="mb-3">
                <summary className="text-[10px] font-mono text-zinc-500 cursor-pointer hover:text-zinc-400 select-none">
                  Quadrant Legend
                </summary>
                <div className="flex flex-wrap gap-2 mt-2">
                  {(Object.keys(Q_LABEL) as Quadrant[]).map(q => (
                    <span key={q} className={clsx(
                      'inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[10px] font-mono',
                      Q_COLORS[q].badge,
                    )}>
                      <span className={clsx('h-1.5 w-1.5 rounded-full', Q_COLORS[q].dot)} />
                      {Q_LABEL[q]} {ROTATION_ARROWS[q]}
                    </span>
                  ))}
                </div>
              </details>
              {loading && !rotation.length ? (
                <Skeleton className="h-[400px]" />
              ) : rotation.length > 0 ? (
                <RotationQuadrantChart
                  sectors={rotation}
                  selectedSector={selectedSector}
                  onSelectSector={setSelectedSector}
                />
              ) : (
                <div className="flex items-center justify-center h-64 text-sm text-zinc-400">No data</div>
              )}
            </div>
          )}
        </div>

        {/* Right: Performance Table */}
        <div className="rounded-lg border border-zinc-700/30 bg-zinc-950/30 p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-xs font-mono font-semibold text-zinc-500 uppercase tracking-wider">Performance Rankings</h3>
            <span className="text-[9px] font-mono text-zinc-400">Click to sort</span>
          </div>
          {loading && !heatmap.length ? (
            <Skeleton className="h-[400px]" />
          ) : heatmap.length > 0 ? (
            <PerformanceHeatmap rows={heatmap} rotationMap={rotationMap} onSelectSector={setSelectedSector} />
          ) : (
            <div className="flex items-center justify-center h-64 text-sm text-zinc-400">No data</div>
          )}
        </div>
      </div>

      {/* ── Sector Leaders (collapsed by default) ───────────────── */}
      <details>
        <summary className="text-xs font-mono font-semibold text-zinc-500 uppercase tracking-wider cursor-pointer hover:text-zinc-400 select-none py-2">
          Sector Leaders — Top Stocks by Momentum
        </summary>
        {loading && !rotation.length ? (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 gap-3 mt-3">
            {[...Array(4)].map((_, i) => <Skeleton key={i} className="h-64" />)}
          </div>
        ) : rotation.length === 0 ? (
          <div className="text-sm text-zinc-400 text-center py-8">No sector data</div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 gap-3 mt-3">
            {sortedByMomentum.map(sector => (
              <SectorLeadersCard
                key={sector.symbol}
                sector={sector}
                onNavigateToStock={handleNavigateToStock}
              />
            ))}
          </div>
        )}
      </details>

    </div>
  )
}
