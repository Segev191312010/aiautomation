import MarketPage from '@/pages/MarketPage'

const TRADINGVIEW_DATA_FAQ = 'https://www.tradingview.com/widget-docs/faq/data/'

export default function ChartsPage() {
  return (
    <div className="flex min-h-0 flex-col gap-4">
      <section className="shell-panel flex flex-wrap items-center justify-between gap-4 px-5 py-4">
        <div>
          <div className="shell-kicker">TradingView chart workspace</div>
          <p className="mt-1 text-sm text-[var(--text-secondary)]">
            Charts render with TradingView Lightweight Charts. Live latency follows the
            connected IBKR market-data entitlement, not a TradingView account plan.
          </p>
        </div>
        <a
          href={TRADINGVIEW_DATA_FAQ}
          target="_blank"
          rel="noopener noreferrer"
          className="shell-chip px-3 py-2 text-xs font-semibold text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)]"
        >
          TradingView data policy
        </a>
      </section>

      <MarketPage />
    </div>
  )
}
