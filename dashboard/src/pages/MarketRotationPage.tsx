import { useCallback, useMemo, useState } from 'react'
import clsx from 'clsx'
import PageErrorBanner from '@/components/common/PageErrorBanner'
import ErrorBoundary from '@/components/ui/ErrorBoundary'
import { SectionHeader } from '@/components/common/SectionHeader'
import { IconArrows, IconBarChart, IconGrid, IconTrendUp } from '@/components/icons'
import { LiveSectorStrip } from '@/components/rotation/LiveSectorStrip'
import { PerformanceHeatmap } from '@/components/rotation/PerformanceHeatmap'
import { RotationQuadrant } from '@/components/rotation/RotationQuadrant'
import { SectorLeaders } from '@/components/rotation/SectorLeaders'
import { TvHeatmap } from '@/components/rotation/TvHeatmap'
import { useLiveSectorPrices } from '@/components/rotation/useLiveSectorPrices'
import { Q_COLORS, Q_LABEL, ROTATION_ARROWS, type Quadrant } from '@/components/rotation/constants'
import { useRotationData } from '@/hooks/useRotationData'
import { useMarketStore, useStockProfileStore, useUIStore } from '@/store'

function SignalCard({ label, value, tone = 'default' }: { label: string; value: string; tone?: 'default' | 'success' | 'warning' }) {
  const toneClass =
    tone === 'success'
      ? 'border-[rgba(31,157,104,0.18)] bg-[rgba(31,157,104,0.1)] text-[var(--success)]'
      : tone === 'warning'
        ? 'border-[rgba(245,158,11,0.2)] bg-[rgba(245,158,11,0.1)] text-[var(--accent)]'
        : 'border-[var(--border)] bg-[var(--bg-hover)] text-[var(--text-primary)]'

  return (
    <div className={`rounded-[24px] border p-4 ${toneClass}`}>
      <div className="shell-kicker">{label}</div>
      <div className="mt-3 text-2xl font-semibold leading-none">{value}</div>
    </div>
  )
}

function SkeletonCard({ className }: { className?: string }) {
  return (
    <div className={clsx('rounded-[24px] border border-[var(--border)] bg-[var(--bg-hover)] p-4 animate-pulse', className)}>
      <div className="h-2.5 w-24 rounded bg-[var(--bg-card)]" />
      <div className="mt-4 h-7 w-32 rounded bg-[var(--bg-card)]" />
      <div className="mt-4 space-y-2">
        {Array.from({ length: 4 }).map((_, index) => <div key={index} className="h-3 rounded bg-[var(--bg-card)]" />)}
      </div>
    </div>
  )
}

export default function MarketRotationPage() {
  const setRoute = useUIStore((state) => state.setRoute)
  const setProfileSymbol = useStockProfileStore((state) => state.setSymbol)
  const setSelectedSymbol = useMarketStore((state) => state.setSelectedSymbol)
  const { rotation, heatmap, loading, error, lastUpdate, refresh } = useRotationData(90)
  const livePrices = useLiveSectorPrices()
  const [selectedSector, setSelectedSector] = useState('XLK')
  const [activeTab, setActiveTab] = useState<'heatmap' | 'quadrant'>('heatmap')

  const handleNavigateToStock = useCallback((symbol: string) => {
    setSelectedSymbol(symbol)
    setProfileSymbol(symbol)
    setRoute('stock')
  }, [setRoute, setProfileSymbol, setSelectedSymbol])

  const rotationMap = useMemo(() => new Map(rotation.map((sector) => [sector.symbol, sector])), [rotation])
  const sortedByMomentum = useMemo(() => [...rotation].sort((a, b) => b.rs_momentum - a.rs_momentum), [rotation])
  const selectedRotation = rotationMap.get(selectedSector) ?? null
  const quadrantCoverage = useMemo(() => new Set(rotation.map((sector) => sector.quadrant)).size, [rotation])
  const topSector = sortedByMomentum[0] ?? null

  return (
    <div className="flex flex-col gap-6 pb-4">
      <section className="grid gap-6 xl:grid-cols-[minmax(0,1.25fr)_minmax(320px,0.75fr)]">
        <div className="shell-panel relative overflow-hidden p-6 sm:p-7">
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_right,rgba(245,158,11,0.16),transparent_34%)]" />
          <div className="relative">
            <div className="shell-kicker">Relative strength radar</div>
            <div className="mt-3 flex flex-wrap items-center gap-2.5">
              <h1 className="display-font text-[2.7rem] leading-none text-[var(--text-primary)] sm:text-[3.2rem]">Market Rotation</h1>
              <span className="shell-chip text-[11px] font-semibold">Selected {selectedSector}</span>
              <span className="shell-chip text-[11px] font-semibold">{livePrices.size} live streams</span>
              {lastUpdate && <span className="shell-chip text-[11px] font-semibold">Updated {lastUpdate.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</span>}
            </div>
            <p className="mt-4 max-w-3xl text-sm leading-7 text-[var(--text-secondary)]">
              Track sector leadership, relative-strength momentum, and top stock handoffs in the same workspace.
            </p>
            <div className="mt-5 flex flex-wrap gap-2">
              <span className="shell-chip text-[11px] font-medium">{rotation.length}/11 sectors loaded</span>
              {topSector && <span className="shell-chip text-[11px] font-medium">Leader {topSector.symbol}</span>}
              <span className="shell-chip text-[11px] font-medium">{activeTab === 'heatmap' ? 'S&P 500 heatmap' : 'Quadrant view'}</span>
            </div>
            <div className="mt-6 flex flex-wrap gap-2">
              {(['heatmap', 'quadrant'] as const).map((tab) => (
                <button
                  key={tab}
                  type="button"
                  onClick={() => setActiveTab(tab)}
                  className={clsx(
                    'rounded-2xl px-4 py-3 text-xs font-semibold transition-colors',
                    activeTab === tab
                      ? 'bg-[var(--accent)] text-white hover:bg-[var(--accent-hover)]'
                      : 'border border-[var(--border)] bg-[var(--bg-hover)] text-[var(--text-secondary)] hover:border-[var(--accent)] hover:text-[var(--text-primary)]',
                  )}
                >
                  {tab === 'heatmap' ? 'S&P 500 heatmap' : 'Rotation quadrant'}
                </button>
              ))}
              <button
                type="button"
                onClick={() => void refresh()}
                disabled={loading}
                className="rounded-2xl border border-[var(--border)] bg-transparent px-4 py-3 text-xs font-semibold text-[var(--text-secondary)] transition-colors hover:border-[var(--accent)] hover:text-[var(--text-primary)] disabled:opacity-50"
              >
                {loading ? 'Refreshing...' : 'Refresh data'}
              </button>
            </div>
          </div>
        </div>

        <div className="grid gap-4 sm:grid-cols-3 xl:grid-cols-1">
          <SignalCard label="Coverage" value={`${quadrantCoverage}/4`} tone={quadrantCoverage === 4 ? 'success' : 'warning'} />
          <SignalCard label="Selected" value={selectedSector} />
          <SignalCard label="Live" value={String(livePrices.size)} tone={livePrices.size > 0 ? 'success' : 'warning'} />
        </div>
      </section>

      <PageErrorBanner show={Boolean(error)} message={error || undefined} />

      <ErrorBoundary>
      <section className="shell-panel p-5 sm:p-6">
        <SectionHeader icon={<IconTrendUp className="h-3.5 w-3.5 text-indigo-500" />} eyebrow="Sector strip" title="Live Sector ETF Tape" badge={<span className="shell-chip px-3 py-1 text-[10px] font-mono">11 ETFs</span>} />
        <div className="mt-4">
          {loading && !rotation.length
            ? <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-6">{Array.from({ length: 6 }).map((_, index) => <SkeletonCard key={index} className="min-h-[130px]" />)}</div>
            : <LiveSectorStrip rotation={rotation} livePrices={livePrices} selectedSector={selectedSector} onSelectSector={setSelectedSector} />}
        </div>
      </section>
      </ErrorBoundary>

      <ErrorBoundary>
      <section className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="shell-panel p-5 sm:p-6">
          <SectionHeader
            icon={activeTab === 'heatmap' ? <IconGrid className="h-3.5 w-3.5 text-emerald-500" /> : <IconArrows className="h-3.5 w-3.5 text-[var(--accent)]" />}
            eyebrow="Workspace"
            title={activeTab === 'heatmap' ? 'S&P 500 Sector Heatmap' : 'Rotation Quadrant'}
            badge={selectedRotation ? <span className="shell-chip px-3 py-1 text-[10px] font-mono">{selectedRotation.quadrant}</span> : null}
          />
          <div className="mt-4">
            {activeTab === 'heatmap' ? (
              <div role="img" aria-label="S&P 500 sector heatmap" className="overflow-hidden rounded-[24px] border border-[var(--border)] bg-[var(--bg-hover)]">
                <TvHeatmap />
              </div>
            ) : loading && !rotation.length ? (
              <SkeletonCard className="min-h-[420px]" />
            ) : rotation.length ? (
              <div className="rounded-[24px] border border-[var(--border)] bg-[var(--bg-hover)] p-4">
                <details className="mb-4">
                  <summary className="cursor-pointer text-[11px] font-semibold text-[var(--text-secondary)]">Quadrant legend</summary>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {(Object.keys(Q_LABEL) as Quadrant[]).map((quadrant) => (
                      <span key={quadrant} className={clsx('inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[10px] font-mono', Q_COLORS[quadrant].badge)}>
                        <span className={clsx('h-1.5 w-1.5 rounded-full', Q_COLORS[quadrant].dot)} />
                        {Q_LABEL[quadrant]} {ROTATION_ARROWS[quadrant]}
                      </span>
                    ))}
                  </div>
                </details>
                <RotationQuadrant sectors={rotation} selectedSector={selectedSector} onSelectSector={setSelectedSector} />
              </div>
            ) : (
              <div className="flex items-center justify-center rounded-[24px] border border-[var(--border)] bg-[var(--bg-hover)] px-5 py-16 text-sm text-[var(--text-muted)]">No sector data available.</div>
            )}
          </div>
        </div>

        <aside className="shell-panel p-5 sm:p-6">
          <SectionHeader icon={<IconBarChart className="h-3.5 w-3.5 text-slate-500" />} eyebrow="Rankings" title="Performance Table" badge={<span className="shell-chip px-3 py-1 text-[10px] font-mono">Sortable</span>} />
          <div className="mt-4">
            {loading && !heatmap.length
              ? <SkeletonCard className="min-h-[420px]" />
              : heatmap.length
                ? <PerformanceHeatmap rows={heatmap} rotationMap={rotationMap} onSelectSector={setSelectedSector} />
                : <div className="flex items-center justify-center rounded-[24px] border border-[var(--border)] bg-[var(--bg-hover)] px-5 py-16 text-sm text-[var(--text-muted)]">No ranking data available.</div>}
          </div>
        </aside>
      </section>
      </ErrorBoundary>

      <ErrorBoundary>
      <section className="shell-panel p-5 sm:p-6">
        <SectionHeader icon={<IconGrid className="h-3.5 w-3.5 text-indigo-500" />} eyebrow="Leaders" title="Sector Leaders" badge={selectedRotation ? <span className="shell-chip px-3 py-1 text-[10px] font-mono">{selectedRotation.name}</span> : null} />
        <div className="mt-4">
          {loading && !rotation.length ? (
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">{Array.from({ length: 4 }).map((_, index) => <SkeletonCard key={index} className="min-h-[260px]" />)}</div>
          ) : rotation.length ? (
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
              {sortedByMomentum.map((sector) => (
                <SectorLeaders key={sector.symbol} sector={sector} onNavigateToStock={handleNavigateToStock} />
              ))}
            </div>
          ) : (
            <div className="flex items-center justify-center rounded-[24px] border border-[var(--border)] bg-[var(--bg-hover)] px-5 py-16 text-sm text-[var(--text-muted)]">No leader data available.</div>
          )}
        </div>
      </section>
      </ErrorBoundary>
    </div>
  )
}
