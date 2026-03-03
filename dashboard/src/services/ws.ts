/**
 * WebSocket service — singleton client with auto-reconnect.
 *
 * Usage:
 *   wsService.subscribe('bot', handler)
 *   wsService.connect()
 *   wsService.send({ action: 'ping' })
 *   wsService.disconnect()
 */
import type { WsEvent, WsEventType } from '@/types'

type Handler = (event: WsEvent) => void

class WebSocketService {
  private ws:          WebSocket | null = null
  private url:         string = ''
  private handlers:    Map<WsEventType | '*', Set<Handler>> = new Map()
  private reconnTimer: ReturnType<typeof setTimeout> | null = null
  private pingInterval: ReturnType<typeof setInterval> | null = null
  private stopped      = false

  connect(path = '/ws'): void {
    this.stopped = false
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    this.url = `${proto}://${window.location.host}${path}`
    this._connect()
  }

  disconnect(): void {
    this.stopped = true
    this._clearTimers()
    if (this.ws) {
      this.ws.close()
      this.ws = null
    }
  }

  send(data: Record<string, unknown>): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data))
    }
  }

  ping(): void {
    this.send({ action: 'ping' })
  }

  subscribe(type: WsEventType | '*', handler: Handler): () => void {
    if (!this.handlers.has(type)) this.handlers.set(type, new Set())
    this.handlers.get(type)!.add(handler)
    return () => this.handlers.get(type)?.delete(handler)
  }

  get connected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN
  }

  // ── Private ─────────────────────────────────────────────────────────────

  private _connect(): void {
    try {
      this.ws = new WebSocket(this.url)
    } catch {
      this._scheduleReconnect()
      return
    }

    this.ws.onopen = () => {
      console.info('[WS] connected to', this.url)
      this._startPing()
      this._emit({ type: 'ibkr_state', connected: true } as WsEvent)
    }

    this.ws.onmessage = (ev: MessageEvent) => {
      try {
        const event = JSON.parse(ev.data as string) as WsEvent
        this._dispatch(event)
      } catch {
        /* ignore malformed */
      }
    }

    this.ws.onerror = () => {
      // onclose fires right after; handle reconnect there
    }

    this.ws.onclose = () => {
      console.warn('[WS] disconnected')
      this._clearTimers()
      if (!this.stopped) this._scheduleReconnect()
    }
  }

  private _dispatch(event: WsEvent): void {
    const specific = this.handlers.get(event.type)
    if (specific) specific.forEach((h) => h(event))
    const wildcard = this.handlers.get('*')
    if (wildcard) wildcard.forEach((h) => h(event))
  }

  private _emit(event: WsEvent): void {
    this._dispatch(event)
  }

  private _startPing(): void {
    this._clearPing()
    this.pingInterval = setInterval(() => this.ping(), 25_000)
  }

  private _clearPing(): void {
    if (this.pingInterval) { clearInterval(this.pingInterval); this.pingInterval = null }
  }

  private _scheduleReconnect(delay = 3_000): void {
    this._clearReconn()
    this.reconnTimer = setTimeout(() => this._connect(), delay)
  }

  private _clearReconn(): void {
    if (this.reconnTimer) { clearTimeout(this.reconnTimer); this.reconnTimer = null }
  }

  private _clearTimers(): void {
    this._clearPing()
    this._clearReconn()
  }
}

export const wsService = new WebSocketService()

// ── Market-data WebSocket (/ws/market-data) ───────────────────────────────────
//
// Lightweight second WS that handles the per-symbol quote push loop.
// Usage:
//   const unsub = wsMdService.subscribe('AAPL', (msg) => { ... })
//   // msg: { type:'quote', symbol:'AAPL', price:220.45, time:1234567890 }

export interface QuoteMsg {
  type:    'quote'
  symbol:  string
  price:   number
  time?:   number
  source?: 'ibkr' | 'yahoo'
  market_state?: 'open' | 'extended' | 'closed' | 'unknown'
  stale_s?: number
}

interface MarketHeartbeatMsg {
  type: 'heartbeat'
  time?: number
}

interface MarketPongMsg {
  type: 'pong'
  time?: number
}

type MarketWsMsg = QuoteMsg | MarketHeartbeatMsg | MarketPongMsg

type QuoteHandler = (msg: QuoteMsg) => void

const WS_STALE_WARN_MS = 10_000
const WS_STALE_CRITICAL_MS = 30_000

class MarketDataWsService {
  private ws:          WebSocket | null = null
  private url:         string = ''
  private handlers:    Map<string, Set<QuoteHandler>> = new Map()
  private subscribed:  Set<string> = new Set()
  private stopped      = false
  private reconnTimer: ReturnType<typeof setTimeout> | null = null
  private pingTimer:   ReturnType<typeof setInterval> | null = null
  private staleTimer:  ReturnType<typeof setInterval> | null = null
  private lastMsgMs    = 0

  connect(): void {
    if (
      this.ws?.readyState === WebSocket.OPEN ||
      this.ws?.readyState === WebSocket.CONNECTING
    ) return
    this.stopped = false
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    this.url = `${proto}://${window.location.host}/ws/market-data`
    this._connect()
  }

  disconnect(): void {
    this.stopped = true
    this._clearTimers()
    this.ws?.close()
    this.ws = null
    this.lastMsgMs = 0
  }

  subscribe(symbol: string, handler: QuoteHandler): () => void {
    const normalized = symbol.trim().toUpperCase()
    if (!normalized) return () => {}
    // Lazy connect
    this.connect()
    if (!this.handlers.has(normalized)) this.handlers.set(normalized, new Set())
    this.handlers.get(normalized)!.add(handler)
    if (!this.subscribed.has(normalized)) {
      this.subscribed.add(normalized)
      this._send({ action: 'subscribe', symbols: [normalized] })
    }
    return () => {
      this.handlers.get(normalized)?.delete(handler)
      if (!this.handlers.get(normalized)?.size) {
        this.subscribed.delete(normalized)
        this._send({ action: 'unsubscribe', symbols: [normalized] })
      }
    }
  }

  getStaleAgeMs(): number {
    if (!this.lastMsgMs) return Infinity
    return Date.now() - this.lastMsgMs
  }

  private _send(data: Record<string, unknown>): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data))
    }
  }

  private _connect(): void {
    try {
      this.ws = new WebSocket(this.url)
    } catch {
      this._scheduleReconnect()
      return
    }

    this.ws.onopen = () => {
      this.lastMsgMs = Date.now()
      if (this.subscribed.size > 0) {
        this._send({ action: 'subscribe', symbols: [...this.subscribed] })
      }
      this._startPing()
      this._startStaleWatchdog()
    }

    this.ws.onmessage = (ev: MessageEvent) => {
      try {
        const msg = JSON.parse(ev.data as string) as MarketWsMsg
        this.lastMsgMs = Date.now()
        if (msg.type === 'quote') {
          this.handlers.get(msg.symbol.toUpperCase())?.forEach((h) => h(msg))
        }
      } catch { /* ignore malformed */ }
    }

    this.ws.onerror = () => { /* onclose fires after */ }

    this.ws.onclose = () => {
      this._clearTimers()
      if (!this.stopped) this._scheduleReconnect()
    }
  }

  private _startPing(): void {
    if (this.pingTimer) clearInterval(this.pingTimer)
    this.pingTimer = setInterval(() => {
      this._send({ action: 'ping' })
    }, 25_000)
  }

  private _startStaleWatchdog(): void {
    if (this.staleTimer) clearInterval(this.staleTimer)
    this.staleTimer = setInterval(() => {
      const ws = this.ws
      if (!ws || ws.readyState !== WebSocket.OPEN) return
      if (this.subscribed.size === 0) return
      const staleAge = Date.now() - this.lastMsgMs
      // Keep the connection warm aggressively when updates are delayed.
      if (staleAge > WS_STALE_WARN_MS) {
        this._send({ action: 'ping' })
      }
      // Recover half-open sockets that remain "OPEN" but stop delivering messages.
      if (staleAge > WS_STALE_CRITICAL_MS) {
        ws.close()
      }
    }, 5_000)
  }

  private _scheduleReconnect(delay = 3_000): void {
    if (this.reconnTimer) clearTimeout(this.reconnTimer)
    this.reconnTimer = setTimeout(() => this._connect(), delay)
  }

  private _clearTimers(): void {
    if (this.pingTimer)   { clearInterval(this.pingTimer);   this.pingTimer   = null }
    if (this.reconnTimer) { clearTimeout(this.reconnTimer);  this.reconnTimer = null }
    if (this.staleTimer)  { clearInterval(this.staleTimer);  this.staleTimer  = null }
  }
}

export const wsMdService = new MarketDataWsService()
