import { useState } from 'react'
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
      {/* Search bar — wider with indigo button */}
      <div className="flex items-center gap-4">
        <form onSubmit={handleSearch} className="flex items-center gap-0 flex-1 max-w-lg">
          <input
            value={searchInput}
            onChange={(e) => setSearch(e.target.value.toUpperCase())}
            placeholder="Enter ticker symbol..."
            className="flex-1 text-sm font-mono bg-terminal-input border border-terminal-border rounded-l-xl px-4 py-2.5 text-terminal-text placeholder:text-terminal-ghost focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500/30 transition-colors"
          />
          <button
            type="submit"
            className="text-sm font-sans font-semibold px-6 py-2.5 rounded-r-xl bg-indigo-600 hover:bg-indigo-500 active:bg-indigo-700 border border-indigo-500 text-white transition-colors shrink-0"
          >
            Search
          </button>
        </form>
        <span className="text-xs font-sans text-terminal-dim hidden sm:block">Stock Intelligence</span>
        {loading && (
          <span className="text-xs font-sans text-terminal-amber animate-pulse ml-auto">
            Loading...
          </span>
        )}
      </div>

      {/* Sticky section nav */}
      <SectionNav />

      {/* Error banner */}
      {error && (
        <div className="rounded-xl border border-terminal-red/40 bg-terminal-red/10 px-4 py-2.5 text-[11px] font-sans text-terminal-red">
          {error}
        </div>
      )}

      {/* 1. Hero */}
      <ErrorBoundary>
        <HeroModule data={overview} loading={loading} />
      </ErrorBoundary>

      {/* 2. Key stats strip */}
      <ErrorBoundary>
        <KeyStatsStrip data={keyStats} companyInfo={companyInfo} loading={loading} />
      </ErrorBoundary>

      {/* 3. Rating scorecard */}
      <ErrorBoundary>
        <RatingScorecardModule data={ratingScorecard} loading={loading} />
      </ErrorBoundary>

      {/* 4. Financial statements (falls back to financial health) */}
      <ErrorBoundary>
        {financialStatements != null ? (
          <FinancialStatementsModule data={financialStatements} loading={loading} />
        ) : (
          <FinancialHealthModule data={financials} loading={loading} />
        )}
      </ErrorBoundary>

      {/* 5. Company overview */}
      <ErrorBoundary>
        <CompanyOverviewModule
          data={companyInfo}
          overview={overview}
          earningsDetail={earningsDetail}
          loading={loading}
        />
      </ErrorBoundary>

      {/* 6. Stock splits */}
      <ErrorBoundary>
        <StockSplitsModule data={stockSplits} loading={loading} />
      </ErrorBoundary>

      {/* 7. Analyst sentiment */}
      <ErrorBoundary>
        <AnalystSentimentModule data={analyst} loading={loading} />
      </ErrorBoundary>

      {/* 8. Price targets */}
      <ErrorBoundary>
        <PriceTargetsModule analyst={analyst} overview={overview} />
      </ErrorBoundary>

      {/* 9. Analyst detail */}
      <ErrorBoundary>
        <AnalystDetailModule data={analystDetail} loading={loading} />
      </ErrorBoundary>

      {/* 10. Ownership + Events side by side */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <ErrorBoundary>
          <OwnershipModule data={ownership} loading={loading} />
        </ErrorBoundary>
        <ErrorBoundary>
          <EventsModule data={events} loading={loading} />
        </ErrorBoundary>
      </div>

      {/* 11. Narrative */}
      <ErrorBoundary>
        <NarrativeModule data={narrative} loading={loading} />
      </ErrorBoundary>
    </div>
  )
}
