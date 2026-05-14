/**
 * useAlerts — global alerts evaluator.
 *
 * Runs at the Layout level. Every few seconds it re-reads the latest quotes
 * from the market store and asks `mockBackend.evaluateAlerts` to flip the
 * triggered state of any alert whose threshold was just crossed.
 *
 * Newly-triggered alerts:
 *  • Show a desktop notification (if permission was granted)
 *  • Push a transient toast via window.dispatchEvent('tradebot-alert')
 *  • Log to console
 */
import { useEffect } from 'react'
import { useMarketStore } from '@/store'
import { evaluateAlerts, type Alert } from '@/services/mockBackend'

const TICK_MS = 4_000

export function useAlerts(): void {
  useEffect(() => {
    // Request notification permission once (no-op if already decided)
    if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
      try { Notification.requestPermission().catch(() => {}) } catch { /* */ }
    }

    const onTrigger = (a: Alert) => {
      const title = `🔔 ${a.symbol} alert`
      const body  = a.message ?? `${a.condition} ${a.value}`
      try {
        if (typeof Notification !== 'undefined' && Notification.permission === 'granted') {
          new Notification(title, { body })
        }
      } catch { /* */ }
      window.dispatchEvent(new CustomEvent('tradebot-alert', { detail: { title, body, alert: a } }))
      console.info('[Alert]', title, body)
    }

    const tick = () => {
      const quotes = useMarketStore.getState().quotes
      if (Object.keys(quotes).length === 0) return
      evaluateAlerts(quotes, onTrigger)
    }

    tick() // run once on mount
    const id = setInterval(tick, TICK_MS)
    return () => clearInterval(id)
  }, [])
}
