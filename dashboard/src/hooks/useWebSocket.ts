/**
 * useWebSocket — React hook that wires the WsService into component lifecycle.
 * Connects on mount, disconnects on unmount, re-dispatches events to stores.
 */
import { useEffect, useRef } from 'react'
import { wsService } from '@/services/ws'
import { useMarketStore, useAccountStore, useBotStore, useSimStore, useAlertStore } from '@/store'
import { fetchTrades, fetchPositions } from '@/services/api'
import type { AlertFiredEvent, WsEvent } from '@/types'
import { useToast } from '@/components/ui/ToastProvider'

export function useWebSocket(): void {
  const mountedRef = useRef(false)

  const setQuotes      = useMarketStore((s) => s.setQuotes)
  const applyLiveQuote = useMarketStore((s) => s.applyLiveQuote)
  const setBotRunning  = useBotStore((s) => s.setBotRunning)
  const setIBKR        = useBotStore((s) => s.setIBKR)
  const addTrade       = useAccountStore((s) => s.addTrade)
  const setPlayback    = useSimStore((s) => s.setPlayback)
  const pushReplayBar  = useSimStore((s) => s.pushReplayBar)

  // Stable ref so the one-time useEffect closure always reads the latest toast API
  const toast = useToast()
  const toastRef = useRef(toast)
  toastRef.current = toast

  useEffect(() => {
    if (mountedRef.current) return
    mountedRef.current = true

    wsService.connect('/ws')

    // IBKR connection state
    const unIBKR = wsService.subscribe('ibkr_state', (ev: WsEvent) => {
      setIBKR(!!(ev['connected']))
    })

    // Bot cycle updates
    const unBot = wsService.subscribe('bot', (ev: WsEvent) => {
      setBotRunning(!!(ev['status'] === 'running' && ev['rules_enabled']))
      const botStore = useBotStore.getState()
      botStore.setCycleStats({
        rulesEnabled:   Number(ev['rules_enabled']   ?? 0),
        rulesChecked:   Number(ev['rules_checked']   ?? 0),
        symbolsScanned: Number(ev['symbols_scanned'] ?? 0),
        signals:        Number(ev['signals']         ?? 0),
        lastRun:        (ev['last_run'] as string) ?? null,
        nextRun:        (ev['next_run'] as string) ?? null,
      })
    })

    // Order filled — refresh trades and positions so the UI reflects the fill immediately
    const unFill = wsService.subscribe('filled', (ev: WsEvent) => {
      console.info('[WS] order filled', ev)
      const symbol    = String(ev['symbol'] ?? '').toUpperCase()
      const qty       = ev['qty']    != null ? String(ev['qty'])    : null
      const fillPrice = ev['price']  != null ? `@${Number(ev['price']).toFixed(2)}` : ''
      const side      = ev['action'] != null ? String(ev['action']) : 'Order'
      const label     = [side, qty ? qty + ' ' + symbol : symbol, fillPrice].filter(Boolean).join(' ')
      toastRef.current.success('Filled: ' + label)
      const { setTrades, setPositions } = useAccountStore.getState()
      fetchTrades().then(setTrades).catch(() => { /* best effort */ })
      fetchPositions().then(setPositions).catch(() => { /* best effort */ })
    })

    // Replay bar
    const unReplay = wsService.subscribe('replay_bar', (ev: WsEvent) => {
      const { symbol: _s, progress, current_index, total_bars, ...bar } = ev as Record<string, unknown>
      setPlayback({
        active:        true,
        symbol:        _s as string,
        speed:         1,
        current_index: current_index as number,
        total_bars:    total_bars as number,
        progress:      progress as number,
        start_ts:      undefined,
        current_ts:    bar['time'] as number | undefined,
        end_ts:        undefined,
      })
      pushReplayBar({
        time:   bar['time'] as number,
        open:   bar['open'] as number,
        high:   bar['high'] as number,
        low:    bar['low'] as number,
        close:  bar['close'] as number,
        volume: bar['volume'] as number,
      })
    })

    const unReplayDone = wsService.subscribe('replay_done', () => {
      setPlayback({
        active: false, symbol: '', speed: 1,
        current_index: 0, total_bars: 0, progress: 1,
      })
    })

    // Broker real-time bar updates (5s bars) -> feed through live quote patcher
    // so chart candles keep moving even in sparse-tick periods.
    const unBar = wsService.subscribe('bar', (ev: WsEvent) => {
      const symbol = String(ev['symbol'] ?? '').toUpperCase()
      const close  = Number(ev['close'])
      const time   = Number(ev['time'])
      if (!symbol || !Number.isFinite(close) || close <= 0 || !Number.isFinite(time) || time <= 0) {
        return
      }
      const store  = useMarketStore.getState()
      const series = store.bars[symbol] ?? store.compBars[symbol] ?? []
      let barSeconds = 5
      if (series.length >= 2) {
        const last  = series[series.length - 1]
        const prev  = series[series.length - 2]
        const delta = last.time - prev.time
        if (Number.isFinite(delta) && delta > 0) barSeconds = delta
      }
      applyLiveQuote(symbol, close, Math.floor(time), barSeconds, 'ibkr', 0)
    })

    const unAlertFired = wsService.subscribe('alert_fired', (ev: WsEvent) => {
      const alertStore = useAlertStore.getState()
      const event = ev as unknown as AlertFiredEvent
      alertStore.pushFired(event)

      // Browser push notification (capability + permission safe)
      if (typeof Notification !== 'undefined' && Notification.permission === 'granted') {
        new Notification(`Alert: ${event.name}`, {
          body: `${event.symbol} — ${event.condition_summary}\nPrice: $${event.price.toFixed(2)}`,
          icon: '/favicon.ico',
        })
      }
    })

    return () => {
      unIBKR()
      unBot()
      unFill()
      unReplay()
      unReplayDone()
      unBar()
      unAlertFired()
      wsService.disconnect()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
}
