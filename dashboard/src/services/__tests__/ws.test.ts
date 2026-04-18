import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

let sockets: MockWebSocket[] = []

class MockWebSocket {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3

  readyState = MockWebSocket.CONNECTING
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  onmessage: ((ev: MessageEvent) => void) | null = null
  sent: string[] = []
  closeCount = 0

  constructor(_url: string) {
    sockets.push(this)
    setTimeout(() => {
      this.readyState = MockWebSocket.OPEN
      this.onopen?.()
    }, 0)
  }

  send(data: string): void {
    this.sent.push(data)
  }

  close(): void {
    if (this.readyState === MockWebSocket.CLOSED) return
    this.closeCount += 1
    this.readyState = MockWebSocket.CLOSED
    this.onclose?.()
  }

  emit(msg: unknown): void {
    this.onmessage?.({ data: JSON.stringify(msg) } as MessageEvent)
  }
}

describe('MarketDataWsService', () => {
  beforeEach(() => {
    sockets = []
    vi.useFakeTimers()
    vi.resetModules()
    Object.defineProperty(globalThis, 'window', {
      configurable: true,
      value: {
        location: {
          protocol: 'http:',
          host: 'localhost:5173',
        },
      },
    })
    ;(globalThis as unknown as { WebSocket: typeof WebSocket }).WebSocket = MockWebSocket as unknown as typeof WebSocket
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('parses enriched quote payloads', async () => {
    const { wsMdService } = await import('@/services/ws')
    const received: Array<{ source?: string; market_state?: string; stale_s?: number }> = []

    const unsub = wsMdService.subscribe('aapl', (msg) => {
      received.push({ source: msg.source, market_state: msg.market_state, stale_s: msg.stale_s })
    })

    await vi.advanceTimersByTimeAsync(1)
    expect(sockets.length).toBeGreaterThan(0)

    sockets[0].emit({
      type: 'quote',
      symbol: 'AAPL',
      price: 201.2,
      time: 1_700_000_000,
      source: 'ibkr',
      market_state: 'open',
      stale_s: 0.2,
    })

    expect(received).toHaveLength(1)
    expect(received[0].source).toBe('ibkr')
    expect(received[0].market_state).toBe('open')
    expect(received[0].stale_s).toBe(0.2)

    unsub()
    wsMdService.disconnect()
  })

  it('reconnect watchdog closes stale open sockets', async () => {
    const { wsMdService } = await import('@/services/ws')
    wsMdService.subscribe('AAPL', () => {})

    await vi.advanceTimersByTimeAsync(1)
    expect(sockets.length).toBe(1)

    await vi.advanceTimersByTimeAsync(36_000)
    expect(sockets[0].closeCount).toBeGreaterThan(0)
  })

  it('connected getter tracks socket readyState transitions', async () => {
    const { wsMdService } = await import('@/services/ws')

    expect(wsMdService.connected).toBe(false)

    wsMdService.subscribe('AAPL', () => {})
    expect(wsMdService.connected).toBe(false) // still CONNECTING at this instant

    await vi.advanceTimersByTimeAsync(1)
    expect(wsMdService.connected).toBe(true)

    sockets[0].close()
    expect(wsMdService.connected).toBe(false)

    wsMdService.disconnect()
  })
})
