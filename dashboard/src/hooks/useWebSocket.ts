/**
 * useWebSocket — React hook that wires the WsService into component lifecycle.
 * Connects on mount, disconnects on unmount, re-dispatches events to stores.
 */
import { useEffect, useRef } from 'react'
import { wsService } from '@/services/ws'
import { useMarketStore, useAccountStore, useBotStore, useSimStore, useAlertStore } from '@/store'
import { fetchTrades, fetchPositions, fetchAccountSummary } from '@/services/api'
import type { AlertFiredEvent, AnyAccount, Position, SimPosition, WsEvent } from '@/types'
import { useToast } from '@/components/ui/ToastProvider'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function isPositionLike(value: unknown): value is Position | SimPosition {
  if (!isRecord(value)) return false
  if (
    typeof value.symbol !== 'string'
    || !isFiniteNumber(value.qty)
    || !isFiniteNumber(value.avg_cost)
    || !isFiniteNumber(value.market_value)
    || !isFiniteNumber(value.unrealized_pnl)
  ) {
    return false
  }

  return (
    ('market_price' in value && isFiniteNumber(value.market_price) && isFiniteNumber(value.realized_pnl))
    || ('current_price' in value && isFiniteNumber(value.current_price) && isFiniteNumber(value.pnl_pct))
  )
}

function isAccountLike(value: unknown): value is AnyAccount {
  if (
    !isRecord(value)
    || !isFiniteNumber(value.cash)
    || !isFiniteNumber(value.unrealized_pnl)
    || !isFiniteNumber(value.realized_pnl)
  ) {
    return false
  }

  if ('net_liquidation' in value) {
    return (
      isFiniteNumber(value.net_liquidation)
      && isFiniteNumber(value.initial_cash)
      && isFiniteNumber(value.positions_value)
      && isFiniteNumber(value.total_return_pct)
      && value.is_sim === true
    )
  }

  return (
    isFiniteNumber(value.balance)
    && isFiniteNumber(value.margin_used)
    && typeof value.currency === 'string'
  )
}

function parsePositionsUpdate(ev: WsEvent) {
  const positions = Array.isArray(ev['positions'])
    ? ev['positions'].filter(isPositionLike)
    : null
  const account = isAccountLike(ev['account']) ? ev['account'] : null

  return { positions, account }
}

export function useWebSocket(): void {
  const mountedRef = useRef(false)

  const setQuotes      = useMarketStore((s) => s.setQuotes)
  const applyLiveQuote = useMarketStore((s) => s.applyLiveQuote)
  const setBotRunning  = useBotStore((s) => s.setBotRunning)
  const setIBKR        = useBotStore((s) => s.setIBKR)
  const setPlayback    = useSimStore((s) => s.setPlayback)
  const pushReplayBar  = useSimStore((s) => s.pushReplayBar)

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

    // Order filled — refresh everything and push to activity feed
    const unFill = wsService.subscribe('filled', (ev: WsEvent) => {
      const symbol    = String(ev['symbol'] ?? '').toUpperCase()
      const qty       = Number(ev['qty'] ?? 0)
      const fillPrice = ev['price'] != null ? Number(ev['price']) : undefined
      const side      = String(ev['action'] ?? 'BUY')
      const ruleName  = String(ev['rule_name'] ?? 'Manual')
      const slPrice   = ev['sl_price'] != null ? Number(ev['sl_price']) : undefined
      const tpPrice   = ev['tp_price'] != null ? Number(ev['tp_price']) : undefined
      const pctAcct   = ev['pct_of_account'] != null ? Number(ev['pct_of_account']) : undefined

      // Enriched toast
      const parts = [`${side} ${qty} ${symbol}`]
      if (fillPrice) parts[0] += ` @$${fillPrice.toFixed(2)}`
      parts.push(`Rule: ${ruleName}`)
      if (slPrice && tpPrice) parts.push(`SL: $${slPrice.toFixed(2)} / TP: $${tpPrice.toFixed(2)}`)
      if (pctAcct) parts.push(`${pctAcct.toFixed(1)}% of account`)
      toastRef.current.success(parts.join(' | '))

      // Push to activity feed
      const { pushActivity, setTrades, setPositions, setAccount } = useAccountStore.getState()
      pushActivity({
        id: String(ev['trade_id'] ?? crypto.randomUUID()),
        timestamp: new Date().toISOString(),
        type: 'fill',
        symbol,
        action: side as 'BUY' | 'SELL',
        qty,
        price: fillPrice,
        ruleName,
        slPrice,
        tpPrice,
        pctOfAccount: pctAcct,
        status: 'FILLED',
      })

      // Refresh all account data
      fetchTrades().then(setTrades).catch(() => {})
      fetchPositions().then(setPositions).catch(() => {})
      fetchAccountSummary().then(setAccount).catch(() => {})
    })

    // Signal fired (before order placed) — show as PENDING in feed
    const unSignal = wsService.subscribe('signal', (ev: WsEvent) => {
      const { pushActivity } = useAccountStore.getState()
      pushActivity({
        id: String(ev['trade_id'] ?? crypto.randomUUID()),
        timestamp: new Date().toISOString(),
        type: 'signal',
        symbol: String(ev['symbol'] ?? '').toUpperCase(),
        action: String(ev['action'] ?? 'BUY') as 'BUY' | 'SELL',
        qty: Number(ev['qty'] ?? 0),
        ruleName: String(ev['rule_name'] ?? ''),
        status: 'PENDING',
      })
      toastRef.current.info(`Signal: ${ev['action']} ${ev['symbol']} — ${ev['rule_name']}`)
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

    // Broker real-time bar updates
    const unBar = wsService.subscribe('bar', (ev: WsEvent) => {
      const symbol = String(ev['symbol'] ?? '').toUpperCase()
      const close  = Number(ev['close'])
      const time   = Number(ev['time'])
      if (!symbol || !Number.isFinite(close) || close <= 0 || !Number.isFinite(time) || time <= 0) return
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

    // Live position + account updates from backend heartbeat
    const unPositions = wsService.subscribe('positions_update', (ev: WsEvent) => {
      const { setPositions, setAccount } = useAccountStore.getState()
      const { positions, account } = parsePositionsUpdate(ev)
      if (positions) setPositions(positions)
      if (account) setAccount(account)
    })

    // Order modified (SL/TP changed)
    const unOrderModified = wsService.subscribe('order_modified', (ev: WsEvent) => {
      const sym = String(ev['symbol'] ?? '')
      const type = String(ev['order_type'] ?? 'Order')
      const price = Number(ev['new_price'] ?? 0)
      toastRef.current.success(`${type} for ${sym} modified to $${price.toFixed(2)}`)

      // Push to activity feed
      const { pushActivity } = useAccountStore.getState()
      pushActivity({
        id: crypto.randomUUID(),
        timestamp: new Date().toISOString(),
        type: 'signal',
        symbol: sym,
        action: 'SELL',
        qty: 0,
        ruleName: `${type} Modified`,
        slPrice: type === 'Stop Loss' ? price : undefined,
        tpPrice: type === 'Take Profit' ? price : undefined,
        status: 'FILLED',
      })
    })

    const unAlertFired = wsService.subscribe('alert_fired', (ev: WsEvent) => {
      const alertStore = useAlertStore.getState()
      const event = ev as unknown as AlertFiredEvent
      alertStore.pushFired(event)
      if (typeof Notification !== 'undefined' && Notification.permission === 'granted') {
        new Notification(`Alert: ${event.name}`, {
          body: `${event.symbol} — ${event.condition_summary}\nPrice: $${event.price.toFixed(2)}`,
          icon: '/favicon.ico',
        })
      }
    })

    return () => {
      unIBKR(); unBot(); unFill(); unSignal(); unReplay(); unReplayDone(); unBar(); unPositions(); unOrderModified(); unAlertFired()
      wsService.disconnect()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
}
