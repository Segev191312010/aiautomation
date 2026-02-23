/**
 * useWebSocket — React hook that wires the WsService into component lifecycle.
 * Connects on mount, disconnects on unmount, re-dispatches events to stores.
 */
import { useEffect, useRef } from 'react'
import { wsService } from '@/services/ws'
import { useMarketStore, useAccountStore, useBotStore, useSimStore } from '@/store'
import type { WsEvent } from '@/types'

export function useWebSocket(): void {
  const mountedRef = useRef(false)

  const setQuotes     = useMarketStore((s) => s.setQuotes)
  const setBotRunning = useBotStore((s) => s.setBotRunning)
  const setIBKR       = useBotStore((s) => s.setIBKR)
  const addTrade      = useAccountStore((s) => s.addTrade)
  const setPlayback   = useSimStore((s) => s.setPlayback)
  const pushReplayBar = useSimStore((s) => s.pushReplayBar)

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
    })

    // Order filled
    const unFill = wsService.subscribe('filled', (ev: WsEvent) => {
      // The server sends partial trade data — cast as needed
      console.info('[WS] order filled', ev)
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

    return () => {
      unIBKR()
      unBot()
      unFill()
      unReplay()
      unReplayDone()
      wsService.disconnect()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
}
