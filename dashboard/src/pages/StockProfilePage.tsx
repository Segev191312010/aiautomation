import { useEffect, useState } from 'react'
import { useStockProfileStore, useMarketStore } from '@/store'
import { useStockProfile } from '@/hooks/useStockProfile'
import { useToast } from '@/components/ui/ToastProvider'
import ErrorBoundary from '@/components/ui/ErrorBoundary'
import SectionNav from '@/components/stock-profile/SectionNav'
import HeroModule from '@/components/stock-profile/HeroModule'
import KeyStatsStrip from '@/components/stock-profile/KeyStatsStrip'
import RatingScorecardModule from '@/components/stock-profile/RatingScorecardModule'
import FinancialStatementsModule from '@/components/stock-profile/FinancialStatementsModule'
import FinancialHealthModule from '@/components/stock-profile/FinancialHealthModule'
import CompanyOverviewModule from '@/components/stock-profile/CompanyOverviewModule'
import StockSplitsModule from '@/components/stock-profile/StockSplitsModule'
import AnalystSentimentModule from '@/components/stock-profile/AnalystSentimentModule'
import PriceTargetsModule from '@/components/stock-profile/PriceTargetsModule'
import AnalystDetailModule from '@/components/stock-profile/AnalystDetailModule'
import OwnershipModule from '@/components/stock-profile/OwnershipModule'
import EventsModule from '@/components/stock-profile/EventsModule'
import NarrativeModule from '@/components/stock-profile/NarrativeModule'

export default function StockProfilePage() {
  const toast = useToast()
  const selectedSymbol = useMarketStore((s) => s.selectedSymbol)
  const storeSymbol = useStockProfileStore((s) => s.symbol)
  const symbol = storeSymbol || selectedSymbol

  const {
    loading,
    error,
    overview,
    keyStats,
    financials,
    financialStatements,
    analyst,
    analystDetail,
    ratingScorecard,
    companyInfo,
    stockSplits,
    earningsDetail,
    ownership,
    events,
    narrative,
  } = useStockProfileStore()

  const setSymbol = useStockProfileStore((s) => s.setSymbol)
  const loadAll = useStockProfileStore((s) => s.loadAll)

  useStockProfile(symbol)

  const [searchInput, setSearch] = useState(symbol)

  useEffect(() => {
    setSearch(symbol)
  }, [symbol])

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault()
    const sym = searchInput.trim().toUpperCase()
    if (sym) {
      setSymbol(sym)
      loadAll(sym)
      toast.info(`Loading ${sym} profile`)
    }
  }

  return (
    <div className="flex flex-col gap-4 h-full overflow-y-auto">
      <section className="card rounded-lg p-5 shadow-card">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="text-[10px] font-sans uppercase tracking-[0.22em] text-gray-400">Stock Analysis</div>
            <h1 className="mt-1 text-3xl font-sans font-semibold tracking-tight text-gray-900">
              {overview?.symbol ?? symbol}
            </h1>
            <p className="mt-2 text-sm font-sans text-gray-600">
              Fundamentals-first company profile with statements, narrative context, analyst opinion, catalysts, and ownership.
            </p>
          </div>

          <div className="flex flex-col gap-3 lg:min-w-[420px]">
            <form onSubmit={handleSearch} className="flex items-center gap-0">
              <input
                value={searchInput}
                onChange={(e) => setSearch(e.target.value.toUpperCase())}
                placeholder="Enter ticker symbol..."
                className="flex-1 text-sm font-mono bg-white border border-gray-200 rounded-l-lg px-4 py-2.5 text-gray-800 placeholder:text-gray-400 focus:border-gray-400 focus:outline-none transition-colors"
              />
              <button
                type="submit"
                className="text-sm font-sans font-semibold px-5 py-2.5 rounded-r-lg bg-gray-900 hover:bg-gray-800 border border-gray-900 text-white transition-colors shrink-0"
              >
                Search
              </button>
            </form>

            <div className="flex flex-wrap gap-2">
              <span className="rounded-full border border-gray-200 bg-gray-50 px-2.5 py-1 text-[10px] font-sans uppercase tracking-[0.18em] text-gray-600">
                {overview?.exchange ?? 'Profile'}
              </span>
              {overview?.sector && (
                <span className="rounded-full border border-gray-200 bg-gray-50 px-2.5 py-1 text-[10px] font-sans uppercase tracking-[0.18em] text-gray-600">
                  {overview.sector}
                </span>
              )}
              {loading && (
                <span className="rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 text-[10px] font-sans uppercase tracking-[0.18em] text-amber-700">
                  Loading
                </span>
              )}
            </div>
          </div>
        </div>
      </section>

      <SectionNav />

      {error && (
        <div className="rounded-lg border border-red-300 bg-red-50 px-4 py-2.5 text-[11px] font-sans text-red-600">
          {error}
        </div>
      )}

      <ErrorBoundary>
        <HeroModule data={overview} loading={loading} fiftyTwoWeekHigh={keyStats?.fifty_two_week_high} fiftyTwoWeekLow={keyStats?.fifty_two_week_low} />
      </ErrorBoundary>

      <ErrorBoundary>
        <KeyStatsStrip data={keyStats} companyInfo={companyInfo} loading={loading} currentPrice={overview?.price} />
      </ErrorBoundary>

      <ErrorBoundary>
        {financialStatements != null ? (
          <FinancialStatementsModule data={financialStatements} loading={loading} />
        ) : (
          <FinancialHealthModule data={financials} loading={loading} />
        )}
      </ErrorBoundary>

      <ErrorBoundary>
        <CompanyOverviewModule
          data={companyInfo}
          overview={overview}
          earningsDetail={earningsDetail}
          loading={loading}
        />
      </ErrorBoundary>

      <ErrorBoundary>
        <NarrativeModule data={narrative} loading={loading} />
      </ErrorBoundary>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <ErrorBoundary>
          <EventsModule data={events} earningsDetail={earningsDetail} loading={loading} />
        </ErrorBoundary>
        <ErrorBoundary>
          <StockSplitsModule data={stockSplits} loading={loading} />
        </ErrorBoundary>
      </div>

      <ErrorBoundary>
        <RatingScorecardModule data={ratingScorecard} loading={loading} />
      </ErrorBoundary>

      <ErrorBoundary>
        <AnalystSentimentModule data={analyst} loading={loading} />
      </ErrorBoundary>

      <ErrorBoundary>
        <AnalystDetailModule data={analystDetail} loading={loading} />
      </ErrorBoundary>

      <ErrorBoundary>
        <PriceTargetsModule analyst={analyst} overview={overview} loading={loading} />
      </ErrorBoundary>

      <ErrorBoundary>
        <OwnershipModule data={ownership} loading={loading} />
      </ErrorBoundary>
    </div>
  )
}
