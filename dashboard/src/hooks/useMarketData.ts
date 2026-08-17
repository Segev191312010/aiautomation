/**
 * useMarketData drives market/account data into the Zustand stores.
 *
 * Live (WebSocket):
 *   - Subscribes active watchlist + chart symbols on /ws/market-data
 *   - Applies each live tick through applyLiveQuote (quotes + bars)
 *
 * Background (REST polling):
 *   - Full quote objects refresh every 5 s
 *   - Account + positions refresh every 10 s
 */
import { useEffect, useCallback, useRef } from 'react'
import {
  fetchWatchlist,
  fetchAccountSummary,
  fetchPositions,
  subscribeRtBars,
  unsubscribeRtBars,
} from '@/services/api'
import { wsMdService } from '@/services/ws'
import { useMarketStore, useAccountStore, useBotStore } from '@/store'

const QUOTE_INTERVAL_FAST = 5_000   // WS disconnected — poll aggressively
const QUOTE_INTERVAL_SLOW = 30_000  // WS connected — live ticks cover freshness
const ACCOUNT_INTERVAL    = 10_000

export function useMarketData(): void {
  const watchlists        = useMarketStore((s) => s.watchlists)
  const activeWatchlist   = useMarketStore((s) => s.activeWatchlist)
  const selectedSymbol    = useMarketStore((s) => s.selectedSymbol)
  const compSymbol        = useMarketStore((s) => s.compSymbol)
  const chartResolution   = useMarketStore((s) => s.chartResolution)
  const ibkrConnected     = useBotStore((s) => s.ibkrConnected)
  const setQuotes         = useMarketStore((s) => s.setQuotes)
  const applyLiveQuote    = useMarketStore((s) => s.applyLiveQuote)
  const setLoading        = useMarketStore((s) => s.setLoading)
  const setAccount        = useAccountStore((s) => s.setAccount)
  const setPositions      = useAccountStore((s) => s.setPositions)

  const quoteTimer   = useRef<ReturnType<typeof setTimeout> | null>(null)
  const accountTimer = useRef<ReturnType<typeof setInterval> | null>(null)

  const isStockLike = (symbol: string): boolean => {
    const s = symbol.trim().toUpperCase()
    return !!s && !s.endsWith('-USD')
  }

  // ---- Quotes (full REST refresh) ----

  const refreshQuotes = useCallback(async () => {
    const wl = watchlists.find((w) => w.id === activeWatchlist)
    if (!wl || wl.symbols.length === 0) return
    try {
      const quotes = await fetchWatchlist(wl.symbols.join(','))
      setQuotes(quotes)
    } catch (err) {
      console.warn('[useMarketData] Quote fetch failed:', err)
    }
  }, [watchlists, activeWatchlist, setQuotes])

  // ---- Account ----

  const refreshAccount = useCallback(async () => {
    try {
      const account = await fetchAccountSummary()
      setAccount(account)
    } catch (err) {
      console.warn('[useMarketData] Account fetch failed:', err)
    }
    try {
      const positions = await fetchPositions()
      setPositions(positions)
    } catch (err) {
      // Do NOT clear positions on transient fetch failure (401, 503, network
      // hiccup). A trading UI that flips to "no open positions" because of a
      // momentary auth/network error is a dangerous false-flat indicator —
      // the user may think they are flat when they are not. Keep the last
      // known book and let the 401 handler (api:unauthorized) drive reauth.
      console.warn('[useMarketData] Positions fetch failed (keeping last known):', err)
    }
  }, [setAccount, setPositions])

  // ---- Startup ----

  useEffect(() => {
    wsMdService.connect()
  }, [])

  useEffect(() => {
    setLoading(true)
    Promise.all([refreshQuotes(), refreshAccount()]).finally(() => setLoading(false))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Live WebSocket quote updates (watchlist + active chart symbols)

  useEffect(() => {
    const wl = watchlists.find((w) => w.id === activeWatchlist)
    const symbols = [
      ...(wl?.symbols ?? []),
      selectedSymbol,
      compSymbol,
    ]
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean)
      .filter((s, i, arr) => arr.indexOf(s) === i)
    if (!symbols.length) return

    const unsubs = symbols.map((sym) =>
      wsMdService.subscribe(sym, (msg) => {
        applyLiveQuote(
          sym,
          msg.price,
          msg.time ?? Math.floor(Date.now() / 1000),
          chartResolution,
          msg.source ?? 'yahoo',
          msg.stale_s ?? 0,
          msg.market_state,
        )
      }),
    )

    return () => unsubs.forEach((u) => u())
  }, [watchlists, activeWatchlist, selectedSymbol, compSymbol, chartResolution, applyLiveQuote])

  // ---- REST polling (full quote data -- change_pct, 52W, vol, etc.) ----
  // Also fires immediately whenever refreshQuotes changes (= watchlist changed),
  // so new symbols appear with full data right away, not after the next tick.

  useEffect(() => {
    let cancelled = false
    refreshQuotes() // immediate on watchlist change
    const scheduleNext = () => {
      if (cancelled) return
      if (quoteTimer.current) clearTimeout(quoteTimer.current)
      const delay = wsMdService.connected ? QUOTE_INTERVAL_SLOW : QUOTE_INTERVAL_FAST
      quoteTimer.current = setTimeout(() => {
        if (cancelled) return
        refreshQuotes()
        scheduleNext()
      }, delay)
    }
    scheduleNext()
    return () => {
      cancelled = true
      if (quoteTimer.current) clearTimeout(quoteTimer.current)
    }
  }, [refreshQuotes])

  useEffect(() => {
    accountTimer.current = setInterval(refreshAccount, ACCOUNT_INTERVAL)
    return () => { if (accountTimer.current) clearInterval(accountTimer.current) }
  }, [refreshAccount])

  // Subscribe selected chart symbols to broker 5-second bars when IBKR is connected.
  // This gives smoother chart movement than relying only on quote ticks.
  useEffect(() => {
    if (!ibkrConnected) return
    const wanted = [selectedSymbol, compSymbol]
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean)
      .filter((s, i, arr) => arr.indexOf(s) === i)
      .filter(isStockLike)
    if (!wanted.length) return

    const active = new Set<string>()
    let cancelled = false
    void (async () => {
      for (const sym of wanted) {
        try {
          const resp = await subscribeRtBars(sym)
          if (!cancelled && resp?.subscribed) {
            active.add(sym)
          }
        } catch {
          // best-effort
        }
      }
    })()

    return () => {
      cancelled = true
      for (const sym of active) {
        void unsubscribeRtBars(sym).catch(() => undefined)
      }
    }
  }, [ibkrConnected, selectedSymbol, compSymbol])
}
