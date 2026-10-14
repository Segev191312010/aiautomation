import { useEffect, useState } from 'react'

import {
  ApiError,
  describeError,
  getJson,
  getManagerPlan,
  getManagerPlans,
  getManagerSpend,
  getManagerStatus,
  getManagerVerdicts,
} from '../api'
import type {
  BridgeOrder,
  LiveStatus,
  ManagerCurrentPlanView,
  ManagerPlanHistoryView,
  ManagerSpendView,
  ManagerStatusView,
  ManagerVerdictsView,
  ManualOrder,
  MarketContextView,
  ScreenerRow,
  ScreenerSnapshot,
  Signal,
} from '../types'

export const POLL_INTERVAL_MS = 5000

/** One endpoint's last good payload plus its latest error; data survives failed polls. */
export type Slice<T> = {
  data: T | null
  error: string | null
  receivedAt: number | null
}

export type LiveData = {
  status: Slice<LiveStatus>
  screener: Slice<ScreenerSnapshot>
  market: Slice<MarketContextView>
  signals: Slice<Signal[]>
  orders: Slice<BridgeOrder[]>
  manualOrders: Slice<ManualOrder[]>
  managerStatus: Slice<ManagerStatusView>
  managerPlan: Slice<ManagerCurrentPlanView>
  managerPlans: Slice<ManagerPlanHistoryView>
  managerVerdicts: Slice<ManagerVerdictsView>
  managerSpend: Slice<ManagerSpendView>
  paused: boolean
  lastPollAt: number | null
}

const EMPTY_SLICE = { data: null, error: null, receivedAt: null } as const

const INITIAL_STATE: LiveData = {
  status: EMPTY_SLICE,
  screener: EMPTY_SLICE,
  market: EMPTY_SLICE,
  signals: EMPTY_SLICE,
  orders: EMPTY_SLICE,
  manualOrders: EMPTY_SLICE,
  managerStatus: EMPTY_SLICE,
  managerPlan: EMPTY_SLICE,
  managerPlans: EMPTY_SLICE,
  managerVerdicts: EMPTY_SLICE,
  managerSpend: EMPTY_SLICE,
  paused: false,
  lastPollAt: null,
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isScreenerRow(value: unknown): value is ScreenerRow {
  return isObject(value) && typeof value.symbol === 'string' && value.symbol.trim() !== ''
}

function asStatus(value: unknown): LiveStatus {
  if (isObject(value) && isObject(value.market) && isObject(value.decisions_today)) {
    return value as LiveStatus
  }
  throw new Error('Unexpected status response')
}

function asScreener(value: unknown): ScreenerSnapshot {
  if (isObject(value) && Array.isArray(value.rows)) {
    return { ...(value as ScreenerSnapshot), rows: value.rows.filter(isScreenerRow) }
  }
  throw new Error('Unexpected screener response')
}

const REGIMES = new Set(['risk_on', 'neutral', 'risk_off'])

/** An unknown regime is treated as no context at all rather than guessed at. */
function asMarket(value: unknown): MarketContextView {
  if (isObject(value) && value.available === false) return { available: false }
  if (
    isObject(value) &&
    value.available === true &&
    typeof value.regime === 'string' &&
    REGIMES.has(value.regime) &&
    typeof value.summary === 'string'
  ) {
    return value as MarketContextView
  }
  if (isObject(value) && value.available === true) return { available: false }
  throw new Error('Unexpected market response')
}

function asList<T>(label: string): (value: unknown) => T[] {
  return (value) => {
    if (Array.isArray(value)) return value.filter(isObject) as T[]
    throw new Error(`Unexpected ${label} response`)
  }
}

function settle<T>(previous: Slice<T>, result: PromiseSettledResult<T>, now: number): Slice<T> {
  if (result.status === 'fulfilled') return { data: result.value, error: null, receivedAt: now }
  return { ...previous, error: describeError(result.reason) }
}

function isUnauthorized(result: PromiseSettledResult<unknown>): boolean {
  return result.status === 'rejected' && result.reason instanceof ApiError && result.reason.status === 401
}

/**
 * Poll the read-only live endpoints every 5 s while the tab is visible.
 * A 401 from any endpoint hands control back to the auth gate.
 */
export function useLiveData(token: string, signalsDate: string, onUnauthorized: () => void): LiveData {
  const [state, setState] = useState<LiveData>(INITIAL_STATE)

  useEffect(() => {
    let disposed = false
    let timer: ReturnType<typeof setTimeout> | undefined
    let controller: AbortController | null = null
    const signalsQuery = signalsDate ? `?date=${encodeURIComponent(signalsDate)}` : ''

    // A different day's feed must never be shown under the newly chosen date.
    setState((previous) => ({ ...previous, signals: EMPTY_SLICE }))

    const poll = async (): Promise<void> => {
      clearTimeout(timer)
      if (document.visibilityState === 'hidden') {
        setState((previous) => (previous.paused ? previous : { ...previous, paused: true }))
        return
      }
      controller?.abort()
      const current = new AbortController()
      controller = current
      const { signal } = current
      const [
        status,
        screener,
        market,
        signals,
        orders,
        manualOrders,
        managerStatus,
        managerPlan,
        managerPlans,
        managerVerdicts,
        managerSpend,
      ] = await Promise.allSettled([
        getJson('/api/live/status', token, signal).then(asStatus),
        getJson('/api/live/screener', token, signal).then(asScreener),
        getJson('/api/live/market', token, signal).then(asMarket),
        getJson(`/api/live/signals${signalsQuery}`, token, signal).then(asList<Signal>('signals')),
        getJson('/api/live/orders', token, signal).then(asList<BridgeOrder>('orders')),
        getJson('/api/live/manual-orders', token, signal).then(asList<ManualOrder>('manual orders')),
        getManagerStatus(token, signal),
        getManagerPlan(token, signal),
        getManagerPlans(token, signal),
        getManagerVerdicts(token, signal),
        getManagerSpend(token, signal),
      ])
      if (disposed || signal.aborted) return
      const results = [
        status,
        screener,
        market,
        signals,
        orders,
        manualOrders,
        managerStatus,
        managerPlan,
        managerPlans,
        managerVerdicts,
        managerSpend,
      ]
      if (results.some(isUnauthorized)) {
        onUnauthorized()
        return
      }
      const now = Date.now()
      setState((previous) => ({
        status: settle(previous.status, status, now),
        screener: settle(previous.screener, screener, now),
        market: settle(previous.market, market, now),
        signals: settle(previous.signals, signals, now),
        orders: settle(previous.orders, orders, now),
        manualOrders: settle(previous.manualOrders, manualOrders, now),
        managerStatus: settle(previous.managerStatus, managerStatus, now),
        managerPlan: settle(previous.managerPlan, managerPlan, now),
        managerPlans: settle(previous.managerPlans, managerPlans, now),
        managerVerdicts: settle(previous.managerVerdicts, managerVerdicts, now),
        managerSpend: settle(previous.managerSpend, managerSpend, now),
        paused: false,
        lastPollAt: now,
      }))
      timer = setTimeout(() => void poll(), POLL_INTERVAL_MS)
    }

    const onVisibilityChange = (): void => {
      if (document.visibilityState === 'hidden') {
        clearTimeout(timer)
        controller?.abort()
        setState((previous) => ({ ...previous, paused: true }))
      } else {
        void poll()
      }
    }

    document.addEventListener('visibilitychange', onVisibilityChange)
    void poll()
    return () => {
      disposed = true
      clearTimeout(timer)
      controller?.abort()
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [token, signalsDate, onUnauthorized])

  return state
}
