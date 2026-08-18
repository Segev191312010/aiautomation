import React, { StrictMode } from 'react'
import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DEFAULT_NOTIFICATION_PREFS } from '@/types'
import type { NotificationPrefs } from '@/types'

const testState = vi.hoisted(() => {
  const hookStore = <T extends object>(state: T) => {
    const hook = (selector: (value: T) => unknown) => selector(state)
    return Object.assign(hook, { getState: () => state })
  }
  const handlers = new Map<string, (event: Record<string, unknown>) => void>()
  const toast = {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  }
  const alertState = {
    notificationPrefs: {
      sound_enabled: true,
      sound: 'chime' as const,
      volume: 0.6,
      muted: false,
      browser_push: false,
      in_app: true,
    } as NotificationPrefs,
    pushFired: vi.fn(),
  }
  const marketState = {
    setQuotes: vi.fn(),
    applyRealtimeBar: vi.fn(),
    chartResolution: { barSize: '1 min' },
  }
  const accountState = {
    pushActivity: vi.fn(),
    setTrades: vi.fn(),
    setPositions: vi.fn(),
    setAccount: vi.fn(),
  }
  const botState = {
    setBotRunning: vi.fn(),
    setIBKR: vi.fn(),
    setCycleStats: vi.fn(),
  }
  const simState = {
    setPlayback: vi.fn(),
    pushReplayBar: vi.fn(),
  }
  return {
    handlers,
    toast,
    alertState,
    connect: vi.fn(),
    disconnect: vi.fn(),
    useMarketStore: hookStore(marketState),
    useAccountStore: hookStore(accountState),
    useBotStore: hookStore(botState),
    useSimStore: hookStore(simState),
    useAlertStore: hookStore(alertState),
  }
})

vi.mock('@/services/ws', () => ({
  wsService: {
    connect: testState.connect,
    disconnect: testState.disconnect,
    subscribe: vi.fn((eventType: string, handler: (event: Record<string, unknown>) => void) => {
      testState.handlers.set(eventType, handler)
      return () => {
        if (testState.handlers.get(eventType) === handler) testState.handlers.delete(eventType)
      }
    }),
  },
}))

vi.mock('@/store', () => ({
  useMarketStore: testState.useMarketStore,
  useAccountStore: testState.useAccountStore,
  useBotStore: testState.useBotStore,
  useSimStore: testState.useSimStore,
  useAlertStore: testState.useAlertStore,
}))

vi.mock('@/services/api', () => ({
  fetchTrades: vi.fn(async () => []),
  fetchPositions: vi.fn(async () => []),
  fetchAccountSummary: vi.fn(async () => ({})),
}))

vi.mock('@/components/ui/ToastProvider', () => ({
  useToast: () => testState.toast,
}))

import { useWebSocket } from '@/hooks/useWebSocket'

function wrapper({ children }: { children: React.ReactNode }) {
  return <StrictMode>{children}</StrictMode>
}

function alertEvent(overrides: Record<string, unknown> = {}) {
  return {
    type: 'alert_fired',
    alert_id: 'alert-1',
    name: 'Price crossed',
    symbol: 'AAPL',
    condition_summary: 'PRICE > 200',
    price: 201.25,
    timestamp: '2026-08-18T12:00:00Z',
    ...overrides,
  }
}

describe('useWebSocket alert delivery', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    testState.handlers.clear()
    testState.alertState.notificationPrefs = { ...DEFAULT_NOTIFICATION_PREFS }
  })

  it('reconnects after the StrictMode effect cleanup', () => {
    const { unmount } = renderHook(() => useWebSocket(), { wrapper })

    expect(testState.connect.mock.calls.length).toBe(testState.disconnect.mock.calls.length + 1)
    expect(testState.handlers.has('alert_fired')).toBe(true)

    unmount()
    expect(testState.connect).toHaveBeenCalledTimes(testState.disconnect.mock.calls.length)
  })

  it('respects in-app preference and never creates a native notification', () => {
    renderHook(() => useWebSocket())
    const handler = testState.handlers.get('alert_fired')
    expect(handler).toBeDefined()

    testState.alertState.notificationPrefs = {
      ...DEFAULT_NOTIFICATION_PREFS,
      in_app: false,
    }
    act(() => handler?.(alertEvent()))
    expect(testState.alertState.pushFired).toHaveBeenCalledOnce()
    expect(testState.toast.info).not.toHaveBeenCalled()

    testState.alertState.notificationPrefs = {
      ...DEFAULT_NOTIFICATION_PREFS,
      in_app: true,
    }
    act(() => handler?.(alertEvent({ alert_id: 'alert-2' })))
    expect(testState.toast.info).toHaveBeenCalledOnce()
  })

  it('drops malformed alert events before they reach notification state', () => {
    renderHook(() => useWebSocket())
    const handler = testState.handlers.get('alert_fired')

    act(() => handler?.(alertEvent({ price: '201.25' })))

    expect(testState.alertState.pushFired).not.toHaveBeenCalled()
    expect(testState.toast.info).not.toHaveBeenCalled()
  })
})
