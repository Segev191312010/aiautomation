/**
 * useLiveStream — subscribes to the backend WebSocket and pushes live updates
 * into the existing Zustand stores.
 *
 * Wraps the singleton `wsService` (see services/ws.ts). It does NOT own the
 * socket lifecycle aggressively — it connects on mount, registers handlers,
 * and tears down only its own subscriptions on unmount so multiple callers can
 * coexist. All handlers are defensive: malformed / partial events never throw.
 *
 * Usage (any page):
 *   const { connected } = useLiveStream()
 */
import { useEffect, useRef, useState } from 'react'
import { wsService } from '@/services/ws'
import { useBotStore, useMarketStore } from '@/store'
import type { WsEvent } from '@/types'

// ── Narrowing helpers ─────────────────────────────────────────────────────────
// WsEvent is { type, [key]: unknown }, so we read fields defensively.

function asBool(v: unknown): boolean | undefined {
  return typeof v === 'boolean' ? v : undefined
}

function asString(v: unknown): string | undefined {
  return typeof v === 'string' && v.length > 0 ? v : undefined
}

function asNumber(v: unknown): number | undefined {
  return typeof v === 'number' && Number.isFinite(v) ? v : undefined
}

export interface LiveStream {
  connected: boolean
}

export function useLiveStream(): LiveStream {
  const [connected, setConnected] = useState<boolean>(() => wsService.connected)
  // Track the latest connected flag without re-subscribing handlers.
  const connectedRef = useRef(connected)
  connectedRef.current = connected

  useEffect(() => {
    const unsubs: Array<() => void> = []

    const syncConnected = (next: boolean): void => {
      if (connectedRef.current !== next) {
        connectedRef.current = next
        setConnected(next)
      }
    }

    // bot/running → useBotStore.setBotRunning
    unsubs.push(
      wsService.subscribe('bot', (ev: WsEvent) => {
        try {
          const running = asBool(ev.running) ?? asBool(ev.bot_running)
          if (running !== undefined) {
            useBotStore.getState().setBotRunning(running)
          }
        } catch {
          /* tolerate malformed event */
        }
      }),
    )

    // ibkr_state → reflects socket connectivity (emitted on open) + IBKR flag.
    unsubs.push(
      wsService.subscribe('ibkr_state', (ev: WsEvent) => {
        try {
          syncConnected(wsService.connected)
          const ibkr = asBool(ev.connected)
          if (ibkr !== undefined) {
            useBotStore.getState().setIBKR(ibkr)
          }
        } catch {
          /* ignore */
        }
      }),
    )

    // signal / filled → live trade activity. We have no dedicated log store here,
    // so surface to console for observability without mutating unrelated state.
    const logActivity = (ev: WsEvent): void => {
      try {
        const symbol = asString(ev.symbol) ?? '?'
        // eslint-disable-next-line no-console
        console.info(`[live] ${ev.type}`, symbol, ev)
      } catch {
        /* ignore */
      }
    }
    unsubs.push(wsService.subscribe('signal', logActivity))
    unsubs.push(wsService.subscribe('filled', logActivity))

    // quote / bar → live price → useMarketStore.updateQuotePrice (no-op if the
    // symbol is not already tracked; the store guards that internally).
    const onPrice = (ev: WsEvent): void => {
      try {
        const symbol = asString(ev.symbol)
        const price =
          asNumber(ev.price) ?? asNumber(ev.close) ?? asNumber(ev.last)
        if (symbol && price !== undefined) {
          useMarketStore.getState().updateQuotePrice(symbol, price)
        }
      } catch {
        /* ignore */
      }
    }
    unsubs.push(wsService.subscribe('quote', onPrice))
    unsubs.push(wsService.subscribe('bar', onPrice))

    // Open (or reuse) the socket. wsService is a singleton with auto-reconnect,
    // so repeated connect() calls are safe.
    try {
      wsService.connect()
    } catch {
      /* connect never throws in practice, but be defensive */
    }

    // Poll the singleton's connected getter so this hook reflects reconnects /
    // disconnects even when no ibkr_state event fires.
    const poll = setInterval(() => syncConnected(wsService.connected), 2_000)
    // Immediate sync for the case where the socket is already open.
    syncConnected(wsService.connected)

    return () => {
      clearInterval(poll)
      unsubs.forEach((u) => {
        try {
          u()
        } catch {
          /* ignore */
        }
      })
      // Intentionally do NOT call wsService.disconnect(): the socket is shared
      // across pages and managed as a singleton.
    }
  }, [])

  return { connected }
}

export default useLiveStream
