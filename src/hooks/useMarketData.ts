/**
 * useMarketData — drives all market data into the Zustand store.
 *
 * Live (WebSocket):
 *   - All active watchlist symbols are subscribed to /ws/market-data
 *   - Every quote tick updates the price field in the store in real-time
 *   - TradingChart separately subscribes for live candle updates
 *
 * Background (REST polling):
 *   - Full quote objects (change_pct, 52W range, volume, etc.) refresh every 5 s
 *   - Account + positions refresh every 10 s
 *   - Immediately refreshes full quotes when watchlist changes
 */
import { useEffect, useCallback, useRef } from 'react'
import { fetchWatchlist, fetchYahooBars, fetchAccountSummary, fetchPositions } from '@/services/api'
import { getMockQuotes, getMockBars, getMockAccount } from '@/services/mockService'
import { useMarketStore, useAccountStore, useBotStore } from '@/store'
import { wsMdService } from '@/services/ws'

const QUOTE_INTERVAL_FAST = 5_000    // when WS is disconnected
const QUOTE_INTERVAL_SLOW = 30_000   // when WS is delivering live ticks
const ACCOUNT_INTERVAL = 10_000

export function useMarketData(): void {
  const watchlists        = useMarketStore((s) => s.watchlists)
  const activeWatchlist   = useMarketStore((s) => s.activeWatchlist)
  const selectedSymbol    = useMarketStore((s) => s.selectedSymbol)
  const compSymbol        = useMarketStore((s) => s.compSymbol)
  const setQuotes         = useMarketStore((s) => s.setQuotes)
  const updateQuotePrice  = useMarketStore((s) => s.updateQuotePrice)
  const setBars           = useMarketStore((s) => s.setBars)
  const setCompBars       = useMarketStore((s) => s.setCompBars)
  const setLoading        = useMarketStore((s) => s.setLoading)
  const setAccount        = useAccountStore((s) => s.setAccount)
  const setPositions      = useAccountStore((s) => s.setPositions)

  const quoteTimer   = useRef<ReturnType<typeof setInterval> | null>(null)
  const accountTimer = useRef<ReturnType<typeof setInterval> | null>(null)

  // ── Quotes (full REST refresh) ────────────────────────────────────────────

  const refreshQuotes = useCallback(async () => {
    const wl = watchlists.find((w) => w.id === activeWatchlist)
    if (!wl || wl.symbols.length === 0) return
    try {
      const quotes = await fetchWatchlist(wl.symbols.join(','))
      setQuotes(quotes)
    } catch {
      setQuotes(getMockQuotes(wl.symbols))
    }
  }, [watchlists, activeWatchlist, setQuotes])

  // ── Chart bars ────────────────────────────────────────────────────────────

  const refreshBars = useCallback(
    async (symbol: string, setter: typeof setBars) => {
      try {
        const bars = await fetchYahooBars(symbol, '3mo', '1d')
        setter(symbol, bars)
      } catch {
        setter(symbol, getMockBars(symbol, 90))
      }
    },
    [],
  )

  // ── Account ───────────────────────────────────────────────────────────────

  const refreshAccount = useCallback(async () => {
    try {
      const account = await fetchAccountSummary()
      setAccount(account)
    } catch {
      setAccount(getMockAccount())
    }
    try {
      const positions = await fetchPositions()
      setPositions(positions)
    } catch {
      setPositions([])
    }
  }, [setAccount, setPositions])

  // ── Startup ───────────────────────────────────────────────────────────────

  useEffect(() => {
    wsMdService.connect()
  }, [])

  useEffect(() => {
    setLoading(true)
    Promise.all([refreshQuotes(), refreshAccount()]).finally(() => setLoading(false))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ── Live WebSocket price updates for ALL watchlist symbols ────────────────
  //
  // Each WS tick calls updateQuotePrice → store patch → TickerCard re-renders.
  // Re-subscribes whenever the active watchlist symbols change.

  useEffect(() => {
    const wl = watchlists.find((w) => w.id === activeWatchlist)
    const symbols = wl?.symbols ?? []
    if (!symbols.length) return

    const unsubs = symbols.map((sym) =>
      wsMdService.subscribe(sym, (msg) => {
        updateQuotePrice(sym, msg.price)
      }),
    )

    return () => unsubs.forEach((u) => u())
  }, [watchlists, activeWatchlist, updateQuotePrice])

  // ── REST polling (full quote data — change_pct, 52W, vol, etc.) ───────────
  //
  // Also fires immediately whenever refreshQuotes changes (= watchlist changed),
  // so new symbols appear with full data right away, not after the next tick.

  useEffect(() => {
    refreshQuotes() // immediate on watchlist change
    const scheduleNext = () => {
      if (quoteTimer.current) clearInterval(quoteTimer.current)
      const interval = wsMdService.connected ? QUOTE_INTERVAL_SLOW : QUOTE_INTERVAL_FAST
      quoteTimer.current = setInterval(() => {
        refreshQuotes()
        scheduleNext()
      }, interval)
    }
    scheduleNext()
    return () => { if (quoteTimer.current) clearInterval(quoteTimer.current) }
  }, [refreshQuotes])

  useEffect(() => {
    accountTimer.current = setInterval(refreshAccount, ACCOUNT_INTERVAL)
    return () => { if (accountTimer.current) clearInterval(accountTimer.current) }
  }, [refreshAccount])

  // ── Chart bars ────────────────────────────────────────────────────────────

  useEffect(() => {
    if (selectedSymbol) refreshBars(selectedSymbol, setBars)
  }, [selectedSymbol, refreshBars, setBars])

  useEffect(() => {
    if (compSymbol) refreshBars(compSymbol, setCompBars)
  }, [compSymbol, refreshBars, setCompBars])
}
