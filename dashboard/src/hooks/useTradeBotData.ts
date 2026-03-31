import { useEffect, useCallback } from 'react'
import { useAccountStore, useBotStore, useSimStore } from '@/store'
import {
  fetchAccountSummary,
  fetchPositions,
  fetchOrders,
  fetchTrades,
  fetchSimAccount,
  fetchSimPositions,
} from '@/services/api'

const POLL_INTERVAL_MS = 10_000

/**
 * Thin adapter over account/sim/bot stores — manages 10s polling + cleanup.
 * Stores own data + loading state.
 */
export function useTradeBotData() {
  const simMode = useBotStore((s) => s.simMode)
  const setAccount = useAccountStore((s) => s.setAccount)
  const setPositions = useAccountStore((s) => s.setPositions)
  const setOrders = useAccountStore((s) => s.setOrders)
  const addTrades = useAccountStore((s) => s.addTrade)
  const setSimAccount = useSimStore((s) => s.setSimAccount)
  const setSimPositions = useSimStore((s) => s.setSimPositions)

  const load = useCallback(async () => {
    try {
      if (simMode) {
        const [account, positions] = await Promise.all([
          fetchSimAccount(),
          fetchSimPositions(),
        ])
        setSimAccount(account)
        setSimPositions(positions)
      } else {
        const [account, positions, orders, trades] = await Promise.all([
          fetchAccountSummary(),
          fetchPositions(),
          fetchOrders(),
          fetchTrades(50),
        ])
        setAccount(account)
        setPositions(positions)
        setOrders(orders)
        for (const t of trades) addTrades(t)
      }
    } catch { /* backend offline */ }
  }, [simMode, setAccount, setPositions, setOrders, addTrades, setSimAccount, setSimPositions])

  useEffect(() => {
    load()
    const t = setInterval(load, POLL_INTERVAL_MS)
    return () => clearInterval(t)
  }, [load])

  return {
    simMode,
    refresh: load,
  }
}
