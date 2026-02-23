# TradeBot — IBKR Automated Trading

A full-stack automated trading app that connects to Interactive Brokers via IB Gateway, evaluates technical indicator rules, and executes orders automatically.

---

## Architecture

```
trading/
├── backend/           # Python FastAPI server
│   ├── main.py        # App entry point — API routes + WebSocket
│   ├── ibkr_client.py # IBKR connection (ib_insync)
│   ├── market_data.py # Historical + real-time price data
│   ├── indicators.py  # RSI, SMA, EMA, MACD, BBANDS, ATR, STOCH
│   ├── rule_engine.py # Condition evaluator
│   ├── order_executor.py # Order placement + trade logging
│   ├── bot_runner.py  # Async loop (runs every N seconds)
│   ├── database.py    # SQLite (rules + trade log)
│   ├── models.py      # Pydantic data models
│   ├── config.py      # Settings from .env
│   ├── requirements.txt
│   └── .env.example
└── frontend/          # Vanilla JS single-page app
    ├── trading.html
    ├── trading.css
    └── trading.js
```

---

## Step 1 — Install IB Gateway

IB Gateway is a lightweight process that bridges your IBKR account to the API.

1. Download IB Gateway from:
   https://www.interactivebrokers.com/en/trading/ibgateway-latest.php

2. Run the installer and follow the prompts.

3. Launch IB Gateway and log in with your IBKR credentials.
   - Select **Paper Trading** on the login screen to use a simulated account (recommended for testing).

4. Enable the API inside IB Gateway:
   - Go to **Configuration → Settings → API → Settings**
   - Check **Enable ActiveX and Socket Clients**
   - Set **Socket port** to `4002` (paper) or `4001` (live)
   - Add `127.0.0.1` to **Trusted IP Addresses**
   - Click **Apply** and **OK**

5. Leave IB Gateway running while the bot is active.

**Port reference:**

| Mode        | TWS   | IB Gateway |
|-------------|-------|------------|
| Live        | 7497  | 4001       |
| Paper       | 7496  | 4002       |

---

## Step 2 — Install Python dependencies

```bash
cd trading/backend
pip install -r requirements.txt
```

Requires Python 3.11+. Using a virtualenv is recommended:

```bash
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

---

## Step 3 — Configure

```bash
cp .env.example .env
```

Edit `.env`:

```env
IBKR_HOST=127.0.0.1
IBKR_PORT=4002          # Use 4002 for IB Gateway paper
IBKR_CLIENT_ID=1
IS_PAPER=true           # Keep true until you trust your rules
BOT_INTERVAL_SECONDS=60 # Evaluate rules every 60 seconds
```

---

## Step 4 — Run the server

```bash
cd trading/backend
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

---

## Step 5 — Open the web app

Navigate to: **http://localhost:8000/trading**

---

## Using the App

### Dashboard
- Toggle the **Automated Trading** switch to start/stop the bot loop.
- If IBKR is not connected, press **Connect to IB Gateway**.
- View account balance, open positions, recent signals, and trade log.

### Rules
- Lists all automation rules.
- Toggle the switch on each rule to enable/disable it.
- Three starter rules are pre-loaded (disabled by default):
  - **RSI Oversold Bounce** — Buy 100 AAPL when RSI(14) crosses below 30
  - **Golden Cross** — Buy 50 AAPL when SMA(50) crosses above SMA(200)
  - **RSI Overbought Exit** — Sell 100 AAPL when RSI(14) crosses above 70

### Rule Builder
- Click **+ New Rule** to create your own.
- Add as many conditions as needed, combined with AND/OR logic.
- Condition format: `INDICATOR(param) OPERATOR VALUE`
  - Example: `RSI(14) crosses_below 30`
  - Example: `SMA(50) crosses_above SMA_200`
  - Example: `PRICE > 200`

### Market
- Enter any stock symbol and press **Go** to fetch 60 days of price history.
- A candlestick-style chart is rendered, plus the last 10 bars in a table.

### Trades
- Full log of every order placed by the bot, including status (PENDING, FILLED, CANCELLED, ERROR).

---

## Rule Condition Reference

| Indicator | Param key | Example params |
|-----------|-----------|----------------|
| RSI       | length    | `{"length": 14}` |
| SMA       | length    | `{"length": 200}` |
| EMA       | length    | `{"length": 50}` |
| MACD      | fast, slow, signal | `{"fast": 12, "slow": 26, "signal": 9}` |
| BBANDS    | length, std, band | `{"length": 20, "std": 2, "band": "upper"}` |
| ATR       | length    | `{"length": 14}` |
| STOCH     | k, d, smooth_k | `{"k": 14, "d": 3, "smooth_k": 3}` |
| PRICE     | (none)    | `{}` |

**Operators:** `crosses_above`, `crosses_below`, `>`, `<`, `>=`, `<=`, `==`

**Value examples:**
- `30` — numeric threshold
- `PRICE` — current closing price
- `SMA_200` — 200-period SMA of the same symbol

---

## Safety Notes

- **Always test on paper trading first.**
- Keep `IS_PAPER=true` in `.env` while experimenting with new rules.
- The cooldown period on each rule prevents it from firing too frequently.
- Rules are created with `enabled=false` by default — you must explicitly enable them.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| "IBKR not connected" | Make sure IB Gateway is running and logged in. Check port in `.env` matches IB Gateway settings. |
| "No bars returned" | Market may be closed. Use `duration=60 D` and `bar_size=1D` for reliable data. |
| Orders not filling | Check IB Gateway has API enabled with Trusted IP `127.0.0.1`. Ensure the symbol is qualified (check logs). |
| Bot not evaluating | Check that at least one rule is enabled and the bot is started via the Dashboard toggle. |
