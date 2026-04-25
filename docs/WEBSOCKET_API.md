# WebSocket API Documentation

## Overview

The platform uses WebSocket connections for real-time communication between the backend and frontend. This enables live market data streaming, bot status updates, trade notifications, and more.

## Connection Endpoints

### General WebSocket
```
ws://localhost:8000/ws
```

### Market Data Stream
```
ws://localhost:8000/ws/market
```

### Bot Status Stream
```
ws://localhost:8000/ws/bot
```

## Authentication

WebSocket connections require JWT authentication via the `Sec-WebSocket-Protocol` header:

```javascript
const token = localStorage.getItem('token');
const ws = new WebSocket('ws://localhost:8000/ws', [`jwt-${token}`]);
```

The token must be prefixed with `jwt-` and will be validated on connection.

## Message Format

All messages are JSON with the following structure:

```json
{
  "type": "message_type",
  "timestamp": "2024-01-15T10:30:00Z",
  "data": { ... }
}
```

## Message Types

### 1. Market Data Messages

#### Price Bar Update
```json
{
  "type": "bar",
  "timestamp": "2024-01-15T10:30:00Z",
  "data": {
    "symbol": "AAPL",
    "time": "2024-01-15T10:30:00Z",
    "open": 185.50,
    "high": 186.20,
    "low": 185.30,
    "close": 186.00,
    "volume": 1500000
  }
}
```

#### Tick Update
```json
{
  "type": "tick",
  "timestamp": "2024-01-15T10:30:00.123Z",
  "data": {
    "symbol": "AAPL",
    "price": 186.05,
    "size": 100,
    "bid": 186.00,
    "ask": 186.10
  }
}
```

#### Real-time Bar (5-second)
```json
{
  "type": "rt_bar",
  "timestamp": "2024-01-15T10:30:05Z",
  "data": {
    "symbol": "AAPL",
    "time": "2024-01-15T10:30:05Z",
    "open": 186.00,
    "high": 186.15,
    "low": 185.95,
    "close": 186.10,
    "volume": 50000,
    "wap": 186.05
  }
}
```

### 2. Trading Messages

#### Order Submitted
```json
{
  "type": "order_submitted",
  "timestamp": "2024-01-15T10:30:00Z",
  "data": {
    "order_id": "order-123",
    "symbol": "AAPL",
    "action": "BUY",
    "quantity": 100,
    "order_type": "LIMIT",
    "limit_price": 186.00,
    "status": "PENDING"
  }
}
```

#### Order Filled
```json
{
  "type": "order_filled",
  "timestamp": "2024-01-15T10:30:05Z",
  "data": {
    "order_id": "order-123",
    "symbol": "AAPL",
    "action": "BUY",
    "quantity": 100,
    "fill_price": 186.00,
    "commission": 1.00,
    "total_cost": 18601.00,
    "status": "FILLED"
  }
}
```

#### Position Update
```json
{
  "type": "position_update",
  "timestamp": "2024-01-15T10:30:05Z",
  "data": {
    "symbol": "AAPL",
    "qty": 100,
    "avg_cost": 186.00,
    "market_price": 186.50,
    "market_value": 18650.00,
    "unrealized_pnl": 50.00,
    "unrealized_pnl_pct": 0.27
  }
}
```

### 3. Bot Status Messages

#### Bot Started
```json
{
  "type": "bot_started",
  "timestamp": "2024-01-15T09:30:00Z",
  "data": {
    "interval_seconds": 60,
    "mode": "SIMULATION"
  }
}
```

#### Bot Stopped
```json
{
  "type": "bot_stopped",
  "timestamp": "2024-01-15T16:00:00Z",
  "data": {
    "reason": "manual",
    "cycles_completed": 390
  }
}
```

#### Bot Cycle Complete
```json
{
  "type": "bot_cycle",
  "timestamp": "2024-01-15T10:30:00Z",
  "data": {
    "cycle_number": 100,
    "symbols_evaluated": 50,
    "rules_triggered": 3,
    "orders_placed": 2,
    "duration_ms": 1500,
    "status": "success"
  }
}
```

#### Bot Health Update
```json
{
  "type": "bot_health",
  "timestamp": "2024-01-15T10:30:00Z",
  "data": {
    "status": "healthy",
    "is_running": true,
    "last_run": "2024-01-15T10:29:00Z",
    "next_run": "2024-01-15T10:30:00Z",
    "cycles_today": 100,
    "errors_last_hour": 0,
    "avg_cycle_time_ms": 1200
  }
}
```

### 4. Signal Messages

#### Rule Triggered
```json
{
  "type": "signal",
  "timestamp": "2024-01-15T10:30:00Z",
  "data": {
    "rule_id": "rule-123",
    "rule_name": "AAPL Golden Cross",
    "symbol": "AAPL",
    "signal": "BUY",
    "confidence": 0.85,
    "indicators": {
      "SMA_50": 185.50,
      "SMA_200": 180.00,
      "RSI": 55
    }
  }
}
```

#### AI Signal
```json
{
  "type": "ai_signal",
  "timestamp": "2024-01-15T10:30:00Z",
  "data": {
    "symbol": "AAPL",
    "signal": "BUY",
    "confidence": 0.92,
    "reasoning": "Strong momentum, RSI not overbought, volume above average",
    "suggested_size": 100,
    "risk_score": 0.3
  }
}
```

### 5. Alert Messages

#### Price Alert
```json
{
  "type": "price_alert",
  "timestamp": "2024-01-15T10:30:00Z",
  "data": {
    "alert_id": "alert-456",
    "symbol": "AAPL",
    "condition": "above",
    "trigger_price": 185.00,
    "current_price": 185.50
  }
}
```

#### Risk Alert
```json
{
  "type": "risk_alert",
  "timestamp": "2024-01-15T10:30:00Z",
  "data": {
    "alert_type": "drawdown",
    "severity": "warning",
    "message": "Portfolio drawdown at 8%, approaching 10% limit",
    "current_drawdown": 0.08,
    "limit": 0.10
  }
}
```

### 6. System Messages

#### Connection Acknowledged
```json
{
  "type": "connected",
  "timestamp": "2024-01-15T10:30:00Z",
  "data": {
    "client_id": "client-123",
    "server_time": "2024-01-15T10:30:00Z",
    "version": "1.0.0"
  }
}
```

#### Error
```json
{
  "type": "error",
  "timestamp": "2024-01-15T10:30:00Z",
  "data": {
    "code": "AUTH_FAILED",
    "message": "Invalid or expired token"
  }
}
```

#### Ping/Pong
```json
// Client sends
{
  "type": "ping",
  "timestamp": "2024-01-15T10:30:00Z"
}

// Server responds
{
  "type": "pong",
  "timestamp": "2024-01-15T10:30:00Z"
}
```

## Client Implementation Examples

### JavaScript/TypeScript

```typescript
class TradingWebSocket {
  private ws: WebSocket | null = null;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;
  private reconnectDelay = 1000;

  constructor(private url: string, private token: string) {}

  connect(): void {
    this.ws = new WebSocket(this.url, [`jwt-${this.token}`]);
    
    this.ws.onopen = () => {
      console.log('WebSocket connected');
      this.reconnectAttempts = 0;
    };
    
    this.ws.onmessage = (event) => {
      const message = JSON.parse(event.data);
      this.handleMessage(message);
    };
    
    this.ws.onclose = () => {
      console.log('WebSocket closed');
      this.attemptReconnect();
    };
    
    this.ws.onerror = (error) => {
      console.error('WebSocket error:', error);
    };
  }

  private handleMessage(message: any): void {
    switch (message.type) {
      case 'bar':
        this.onBarUpdate?.(message.data);
        break;
      case 'order_filled':
        this.onOrderFilled?.(message.data);
        break;
      case 'bot_health':
        this.onBotHealth?.(message.data);
        break;
      case 'signal':
        this.onSignal?.(message.data);
        break;
      case 'error':
        console.error('Server error:', message.data);
        break;
    }
  }

  private attemptReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error('Max reconnection attempts reached');
      return;
    }
    
    setTimeout(() => {
      this.reconnectAttempts++;
      this.connect();
    }, this.reconnectDelay * Math.pow(2, this.reconnectAttempts));
  }

  subscribe(symbols: string[]): void {
    this.send({
      type: 'subscribe',
      data: { symbols }
    });
  }

  unsubscribe(symbols: string[]): void {
    this.send({
      type: 'unsubscribe',
      data: { symbols }
    });
  }

  ping(): void {
    this.send({
      type: 'ping',
      timestamp: new Date().toISOString()
    });
  }

  private send(message: any): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message));
    }
  }

  disconnect(): void {
    this.ws?.close();
  }

  // Callbacks
  onBarUpdate?: (data: any) => void;
  onOrderFilled?: (data: any) => void;
  onBotHealth?: (data: any) => void;
  onSignal?: (data: any) => void;
}

// Usage
const ws = new TradingWebSocket('ws://localhost:8000/ws', token);
ws.onBarUpdate = (data) => console.log('Bar:', data);
ws.onOrderFilled = (data) => console.log('Fill:', data);
ws.connect();
ws.subscribe(['AAPL', 'MSFT']);
```

### Python

```python
import asyncio
import json
import websockets

class TradingWebSocket:
    def __init__(self, url: str, token: str):
        self.url = url
        self.token = token
        self.ws = None
        self.subscriptions = set()
    
    async def connect(self):
        headers = {"Sec-WebSocket-Protocol": f"jwt-{self.token}"}
        self.ws = await websockets.connect(
            self.url,
            extra_headers=headers
        )
        asyncio.create_task(self._receive_loop())
    
    async def _receive_loop(self):
        async for message in self.ws:
            data = json.loads(message)
            await self._handle_message(data)
    
    async def _handle_message(self, message: dict):
        msg_type = message.get("type")
        
        if msg_type == "bar":
            await self._on_bar(message["data"])
        elif msg_type == "order_filled":
            await self._on_fill(message["data"])
        elif msg_type == "signal":
            await self._on_signal(message["data"])
        elif msg_type == "error":
            print(f"Error: {message['data']}")
    
    async def subscribe(self, symbols: list[str]):
        await self.ws.send(json.dumps({
            "type": "subscribe",
            "data": {"symbols": symbols}
        }))
        self.subscriptions.update(symbols)
    
    async def unsubscribe(self, symbols: list[str]):
        await self.ws.send(json.dumps({
            "type": "unsubscribe",
            "data": {"symbols": symbols}
        }))
        self.subscriptions.difference_update(symbols)
    
    async def ping(self):
        await self.ws.send(json.dumps({
            "type": "ping",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }))
    
    async def close(self):
        await self.ws.close()
    
    # Override these methods
    async def _on_bar(self, data): pass
    async def _on_fill(self, data): pass
    async def _on_signal(self, data): pass

# Usage
async def main():
    ws = TradingWebSocket("ws://localhost:8000/ws", token)
    await ws.connect()
    await ws.subscribe(["AAPL", "MSFT"])
    
    # Keep running
    await asyncio.sleep(3600)
    await ws.close()

asyncio.run(main())
```

## Connection Management

### Heartbeat

The server sends periodic ping frames. Clients should respond with pong frames automatically (handled by most WebSocket libraries).

### Reconnection Strategy

Recommended reconnection strategy:

1. **Immediate reconnect**: First attempt immediately
2. **Exponential backoff**: 1s, 2s, 4s, 8s, 16s
3. **Max delay**: Cap at 30 seconds
4. **Max attempts**: 5 attempts, then manual intervention
5. **Full reconnect**: Re-subscribe to all previous subscriptions

### Error Handling

Common error codes:

| Code | Description | Action |
|------|-------------|--------|
| `AUTH_FAILED` | Invalid or expired token | Re-authenticate |
| `RATE_LIMITED` | Too many messages | Reduce message rate |
| `INVALID_SYMBOL` | Symbol not found | Check symbol format |
| `SUBSCRIPTION_LIMIT` | Too many subscriptions | Reduce subscriptions |
| `INTERNAL_ERROR` | Server error | Retry with backoff |

## Performance Considerations

### Message Rate Limits

- Maximum 100 messages/second per client
- Maximum 1000 subscriptions per connection
- Bar updates throttled to 1/second per symbol

### Bandwidth Optimization

- Use binary frames for large payloads (optional)
- Subscribe only to needed symbols
- Unsubscribe from inactive symbols
- Batch multiple subscriptions in one message

### Latency

Expected latencies:
- Market data: < 100ms
- Order updates: < 50ms
- Bot status: < 200ms

## Security

### Authentication
- JWT tokens validated on connection
- Tokens expire after 24 hours
- Re-authentication required for new connections

### Authorization
- User can only see their own data
- Admin users can see all data
- Symbol access restricted by subscription level

### Encryption
- Use WSS (WebSocket Secure) in production
- TLS 1.2+ required
- Certificate pinning recommended for mobile
