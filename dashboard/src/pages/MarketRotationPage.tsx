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
import { useState, useCallback, useMemo } from 'react'
import clsx from 'clsx'
import { useUIStore, useStockProfileStore, useMarketStore } from '@/store'
import { useRotationData } from '@/hooks/useRotationData'
import { TvHeatmap } from '@/components/rotation/TvHeatmap'
import { LiveSectorStrip } from '@/components/rotation/LiveSectorStrip'
import { RotationQuadrant } from '@/components/rotation/RotationQuadrant'
import { PerformanceHeatmap } from '@/components/rotation/PerformanceHeatmap'
import { SectorLeaders } from '@/components/rotation/SectorLeaders'
import { useLiveSectorPrices } from '@/components/rotation/useLiveSectorPrices'
import { Q_COLORS, Q_LABEL, ROTATION_ARROWS, type Quadrant } from '@/components/rotation/constants'

// ── Local helpers ─────────────────────────────────────────────────────────────

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

// ── Page ─────────────────────────────────────────────────────────────────────

export default function MarketRotationPage() {
  const setRoute = useUIStore(s => s.setRoute)
  const setProfileSymbol = useStockProfileStore(s => s.setSymbol)
  const setSelectedSymbol = useMarketStore(s => s.setSelectedSymbol)

  const { rotation, heatmap, loading, error, lastUpdate, refresh } = useRotationData(90)
  const livePrices = useLiveSectorPrices()

  const [selectedSector, setSelectedSector] = useState('XLK')
  const [activeTab, setActiveTab] = useState<'heatmap' | 'quadrant'>('heatmap')

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

  const formatTime = (d: Date) =>
    d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' })

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
            onClick={() => { refresh() }}
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
          <button type="button" onClick={refresh} className="ml-3 text-xs text-red-400 hover:text-red-300 underline">Retry</button>
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
              <TvHeatmap />
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
                <RotationQuadrant
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
              <SectorLeaders
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
