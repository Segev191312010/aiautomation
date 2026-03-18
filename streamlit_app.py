"""
Trading Platform — Streamlit Dashboard
Connects to the FastAPI backend at localhost:8000 for all data.
"""
import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime

API = "http://localhost:8000"

st.set_page_config(
    page_title="Trading Platform",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Sidebar ──────────────────────────────────────────────────────────────────

st.sidebar.title("Trading Platform")
page = st.sidebar.radio(
    "Navigate",
    ["Market Overview", "Chart", "Screener", "Backtester", "System Status"],
)

# ── Helpers ──────────────────────────────────────────────────────────────────

@st.cache_data(ttl=5)
def fetch_watchlist(symbols: str) -> list[dict]:
    r = requests.get(f"{API}/api/watchlist", params={"symbols": symbols}, timeout=10)
    r.raise_for_status()
    return r.json()

@st.cache_data(ttl=10)
def fetch_bars(symbol: str, period: str, interval: str) -> list[dict]:
    r = requests.get(
        f"{API}/api/yahoo/{symbol}/bars",
        params={"period": period, "interval": interval},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()

@st.cache_data(ttl=30)
def fetch_indicator(symbol: str, indicator: str, period: str, interval: str, length: int = 0) -> list[dict]:
    params = {"indicator": indicator, "period": period, "interval": interval}
    if length > 0:
        params["length"] = length
    r = requests.get(f"{API}/api/market/{symbol}/indicators", params=params, timeout=15)
    r.raise_for_status()
    return r.json()

def fetch_status() -> dict:
    r = requests.get(f"{API}/api/status", timeout=5)
    r.raise_for_status()
    return r.json()

def fetch_data_health() -> dict:
    r = requests.get(f"{API}/api/data/health", timeout=5)
    r.raise_for_status()
    return r.json()

def run_backtest(payload: dict) -> dict:
    r = requests.post(f"{API}/api/backtest/run", json=payload, timeout=60)
    r.raise_for_status()
    return r.json()

def run_screener(payload: dict) -> dict:
    r = requests.post(f"{API}/api/screener/scan", json=payload, timeout=30)
    r.raise_for_status()
    return r.json()

def fetch_universes() -> list[dict]:
    r = requests.get(f"{API}/api/screener/universes", timeout=5)
    r.raise_for_status()
    return r.json()


# ── Page: Market Overview ────────────────────────────────────────────────────

if page == "Market Overview":
    st.header("Market Overview")

    symbols_input = st.text_input(
        "Symbols (comma-separated)",
        value="AAPL,MSFT,GOOGL,TSLA,NVDA,SPY,QQQ,BTC-USD,ETH-USD",
    )

    if symbols_input.strip():
        try:
            quotes = fetch_watchlist(symbols_input.strip())
            if quotes:
                df = pd.DataFrame(quotes)
                cols_display = ["symbol", "price", "change", "change_pct", "year_high", "year_low", "market_cap", "avg_volume"]
                cols_available = [c for c in cols_display if c in df.columns]
                df_show = df[cols_available].copy()

                # Format
                if "price" in df_show.columns:
                    df_show["price"] = df_show["price"].apply(lambda x: f"${x:,.2f}" if pd.notna(x) else "—")
                if "change" in df_show.columns:
                    df_show["change"] = df_show["change"].apply(lambda x: f"{x:+.2f}" if pd.notna(x) else "—")
                if "change_pct" in df_show.columns:
                    df_show["change_pct"] = df_show["change_pct"].apply(lambda x: f"{x:+.2f}%" if pd.notna(x) else "—")
                if "market_cap" in df_show.columns:
                    def fmt_cap(x):
                        if pd.isna(x) or x is None: return "—"
                        if x >= 1e12: return f"${x/1e12:.1f}T"
                        if x >= 1e9: return f"${x/1e9:.1f}B"
                        if x >= 1e6: return f"${x/1e6:.0f}M"
                        return f"${x:,.0f}"
                    df_show["market_cap"] = df_show["market_cap"].apply(fmt_cap)
                if "avg_volume" in df_show.columns:
                    df_show["avg_volume"] = df_show["avg_volume"].apply(
                        lambda x: f"{x/1e6:.1f}M" if pd.notna(x) and x else "—"
                    )

                st.dataframe(df_show, use_container_width=True, hide_index=True)

                # Mini charts
                st.subheader("Quick Charts")
                chart_cols = st.columns(min(len(quotes), 4))
                for i, q in enumerate(quotes[:8]):
                    col = chart_cols[i % len(chart_cols)]
                    sym = q["symbol"]
                    with col:
                        try:
                            bars = fetch_bars(sym, "1mo", "1d")
                            if bars:
                                mini_df = pd.DataFrame(bars)
                                mini_df["time"] = pd.to_datetime(mini_df["time"], unit="s")
                                color = "green" if len(bars) > 1 and bars[-1]["close"] >= bars[0]["close"] else "red"
                                fig = go.Figure(go.Scatter(
                                    x=mini_df["time"], y=mini_df["close"],
                                    mode="lines", line=dict(color=color, width=2),
                                    fill="tozeroy", fillcolor=f"rgba({'0,200,0' if color == 'green' else '200,0,0'}, 0.1)",
                                ))
                                fig.update_layout(
                                    title=f"{sym} ${q['price']:.2f}",
                                    height=200, margin=dict(l=10, r=10, t=30, b=10),
                                    xaxis=dict(visible=False), yaxis=dict(visible=False),
                                    showlegend=False,
                                )
                                st.plotly_chart(fig, use_container_width=True)
                        except Exception:
                            st.caption(f"{sym}: chart unavailable")
            else:
                st.warning("No quotes returned")
        except Exception as e:
            st.error(f"Failed to fetch watchlist: {e}")


# ── Page: Chart ──────────────────────────────────────────────────────────────

elif page == "Chart":
    st.header("Interactive Chart")

    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        symbol = st.text_input("Symbol", value="AAPL").upper()
    with col2:
        period = st.selectbox("Period", ["5d", "1mo", "3mo", "6mo", "1y", "2y", "5y"], index=3)
    with col3:
        interval = st.selectbox("Interval", ["1m", "5m", "15m", "30m", "1h", "1d", "1wk"], index=5)

    # Indicators
    ind_col1, ind_col2 = st.columns(2)
    with ind_col1:
        show_sma = st.checkbox("SMA", value=True)
        sma_len = st.number_input("SMA Length", 5, 200, 20, key="sma") if show_sma else 20
    with ind_col2:
        show_rsi = st.checkbox("RSI")
        rsi_len = st.number_input("RSI Length", 5, 50, 14, key="rsi") if show_rsi else 14

    show_volume = st.checkbox("Volume", value=True)
    chart_type = st.radio("Chart Type", ["Candlestick", "Line", "OHLC"], horizontal=True)

    if symbol:
        try:
            bars = fetch_bars(symbol, period, interval)
            if not bars:
                st.warning(f"No data for {symbol}")
            else:
                df = pd.DataFrame(bars)
                df["time"] = pd.to_datetime(df["time"], unit="s")

                n_rows = 1 + (1 if show_volume else 0) + (1 if show_rsi else 0)
                row_heights = [0.6]
                specs = [[{"secondary_y": False}]]
                if show_volume:
                    row_heights.append(0.2)
                    specs.append([{"secondary_y": False}])
                if show_rsi:
                    row_heights.append(0.2)
                    specs.append([{"secondary_y": False}])

                fig = make_subplots(
                    rows=n_rows, cols=1,
                    shared_xaxes=True,
                    vertical_spacing=0.03,
                    row_heights=row_heights,
                    specs=specs,
                )

                # Main chart
                if chart_type == "Candlestick":
                    fig.add_trace(go.Candlestick(
                        x=df["time"], open=df["open"], high=df["high"],
                        low=df["low"], close=df["close"], name=symbol,
                    ), row=1, col=1)
                elif chart_type == "OHLC":
                    fig.add_trace(go.Ohlc(
                        x=df["time"], open=df["open"], high=df["high"],
                        low=df["low"], close=df["close"], name=symbol,
                    ), row=1, col=1)
                else:
                    fig.add_trace(go.Scatter(
                        x=df["time"], y=df["close"], mode="lines",
                        name=symbol, line=dict(color="#2196F3", width=2),
                    ), row=1, col=1)

                # SMA overlay
                if show_sma:
                    try:
                        sma_data = fetch_indicator(symbol, "SMA", period, interval, sma_len)
                        if sma_data:
                            sma_df = pd.DataFrame(sma_data)
                            sma_df["time"] = pd.to_datetime(sma_df["time"], unit="s")
                            fig.add_trace(go.Scatter(
                                x=sma_df["time"], y=sma_df["value"],
                                mode="lines", name=f"SMA({sma_len})",
                                line=dict(color="orange", width=1.5),
                            ), row=1, col=1)
                    except Exception:
                        pass

                # Volume
                current_row = 2
                if show_volume:
                    colors = ["green" if c >= o else "red" for o, c in zip(df["open"], df["close"])]
                    fig.add_trace(go.Bar(
                        x=df["time"], y=df["volume"], name="Volume",
                        marker_color=colors, opacity=0.5,
                    ), row=current_row, col=1)
                    current_row += 1

                # RSI
                if show_rsi:
                    try:
                        rsi_data = fetch_indicator(symbol, "RSI", period, interval, rsi_len)
                        if rsi_data:
                            rsi_df = pd.DataFrame(rsi_data)
                            rsi_df["time"] = pd.to_datetime(rsi_df["time"], unit="s")
                            fig.add_trace(go.Scatter(
                                x=rsi_df["time"], y=rsi_df["value"],
                                mode="lines", name=f"RSI({rsi_len})",
                                line=dict(color="#E040FB", width=1.5),
                            ), row=current_row, col=1)
                            fig.add_hline(y=70, line_dash="dash", line_color="red", opacity=0.5, row=current_row, col=1)
                            fig.add_hline(y=30, line_dash="dash", line_color="green", opacity=0.5, row=current_row, col=1)
                    except Exception:
                        pass

                fig.update_layout(
                    height=600 + (150 if show_volume else 0) + (150 if show_rsi else 0),
                    template="plotly_dark",
                    xaxis_rangeslider_visible=False,
                    showlegend=True,
                    legend=dict(orientation="h", yanchor="bottom", y=1.02),
                    margin=dict(l=50, r=20, t=40, b=20),
                )
                st.plotly_chart(fig, use_container_width=True)

                # Price info
                last = bars[-1]
                prev = bars[-2] if len(bars) > 1 else bars[0]
                chg = last["close"] - prev["close"]
                chg_pct = (chg / prev["close"] * 100) if prev["close"] else 0
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Last Price", f"${last['close']:.2f}", f"{chg:+.2f} ({chg_pct:+.2f}%)")
                m2.metric("High", f"${last['high']:.2f}")
                m3.metric("Low", f"${last['low']:.2f}")
                m4.metric("Volume", f"{last['volume']:,.0f}")

        except Exception as e:
            st.error(f"Chart error: {e}")


# ── Page: Screener ───────────────────────────────────────────────────────────

elif page == "Screener":
    st.header("Stock Screener")

    # Universe selection
    try:
        universes = fetch_universes()
        universe_names = [u["name"] for u in universes]
    except Exception:
        universes = []
        universe_names = ["SP500"]

    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        universe = st.selectbox("Universe", universe_names, index=0)
    with col2:
        scr_period = st.selectbox("Period", ["1mo", "3mo", "6mo", "1y"], index=1, key="scr_period")
    with col3:
        scr_interval = st.selectbox("Interval", ["1d", "1wk"], index=0, key="scr_interval")

    # Filters
    st.subheader("Filters")
    n_filters = st.number_input("Number of filters", 1, 10, 2)
    filters = []
    indicators = ["RSI", "SMA", "EMA", "PRICE", "VOLUME", "CHANGE_PCT", "MACD", "BBANDS", "ATR", "STOCH"]
    operators = [">", "<", ">=", "<=", "==", "crosses_above", "crosses_below"]

    for i in range(n_filters):
        fc1, fc2, fc3, fc4 = st.columns([2, 2, 1, 2])
        with fc1:
            ind = st.selectbox(f"Indicator {i+1}", indicators, key=f"ind_{i}")
        with fc2:
            op = st.selectbox(f"Operator {i+1}", operators, key=f"op_{i}")
        with fc3:
            length = st.number_input(f"Length {i+1}", 1, 200, 14, key=f"len_{i}")
        with fc4:
            val = st.number_input(f"Value {i+1}", -1000.0, 100000.0, 30.0, key=f"val_{i}")
        filters.append({
            "indicator": ind,
            "operator": op,
            "value": val,
            "length": length,
        })

    sort_by = st.selectbox("Sort by", ["CHANGE_PCT", "RSI", "VOLUME", "PRICE", "MARKET_CAP"], index=0)
    limit = st.slider("Max results", 10, 200, 50)

    if st.button("Run Scan", type="primary"):
        with st.spinner("Scanning..."):
            try:
                payload = {
                    "universe": universe,
                    "filters": filters,
                    "sort_by": sort_by,
                    "sort_dir": "desc",
                    "limit": limit,
                    "period": scr_period,
                    "interval": scr_interval,
                }
                result = run_screener(payload)
                matches = result.get("matches", [])
                st.success(f"Found {len(matches)} matches (scanned {result.get('scanned', '?')} symbols in {result.get('duration_ms', 0):.0f}ms)")

                if matches:
                    mdf = pd.DataFrame(matches)
                    st.dataframe(mdf, use_container_width=True, hide_index=True)
                else:
                    st.info("No stocks matched your filters")
            except Exception as e:
                st.error(f"Screener error: {e}")


# ── Page: Backtester ─────────────────────────────────────────────────────────

elif page == "Backtester":
    st.header("Backtesting Engine")

    col1, col2, col3 = st.columns(3)
    with col1:
        bt_symbol = st.text_input("Symbol", value="AAPL", key="bt_sym").upper()
    with col2:
        bt_period = st.selectbox("Period", ["3mo", "6mo", "1y", "2y", "5y"], index=3, key="bt_period")
    with col3:
        bt_interval = st.selectbox("Interval", ["1d", "1wk"], index=0, key="bt_interval")

    col4, col5, col6 = st.columns(3)
    with col4:
        capital = st.number_input("Initial Capital ($)", 1000, 10_000_000, 100_000, step=10000)
    with col5:
        pos_size = st.slider("Position Size %", 10, 100, 100)
    with col6:
        logic = st.radio("Condition Logic", ["AND", "OR"], horizontal=True)

    # Stop loss / take profit
    sl_col, tp_col = st.columns(2)
    with sl_col:
        stop_loss = st.number_input("Stop Loss %", 0.0, 50.0, 0.0, step=1.0)
    with tp_col:
        take_profit = st.number_input("Take Profit %", 0.0, 100.0, 0.0, step=1.0)

    # Entry conditions
    st.subheader("Entry Conditions")
    n_entry = st.number_input("Number of entry conditions", 1, 5, 1, key="n_entry")
    entry_conditions = []
    bt_indicators = ["RSI", "SMA", "EMA", "PRICE", "MACD", "BBANDS", "STOCH", "ATR"]
    bt_operators = [">", "<", ">=", "<=", "crosses_above", "crosses_below"]

    for i in range(n_entry):
        ec1, ec2, ec3, ec4 = st.columns([2, 2, 1, 2])
        with ec1:
            e_ind = st.selectbox(f"Entry Ind {i+1}", bt_indicators, key=f"eind_{i}")
        with ec2:
            e_op = st.selectbox(f"Entry Op {i+1}", bt_operators, index=1, key=f"eop_{i}")
        with ec3:
            e_len = st.number_input(f"Entry Len {i+1}", 1, 200, 14, key=f"elen_{i}")
        with ec4:
            e_val = st.number_input(f"Entry Val {i+1}", -1000.0, 100000.0, 30.0, key=f"eval_{i}")
        entry_conditions.append({
            "indicator": e_ind, "operator": e_op, "value": e_val, "length": e_len,
        })

    # Exit conditions
    st.subheader("Exit Conditions")
    n_exit = st.number_input("Number of exit conditions", 0, 5, 1, key="n_exit")
    exit_conditions = []
    for i in range(n_exit):
        xc1, xc2, xc3, xc4 = st.columns([2, 2, 1, 2])
        with xc1:
            x_ind = st.selectbox(f"Exit Ind {i+1}", bt_indicators, key=f"xind_{i}")
        with xc2:
            x_op = st.selectbox(f"Exit Op {i+1}", bt_operators, index=0, key=f"xop_{i}")
        with xc3:
            x_len = st.number_input(f"Exit Len {i+1}", 1, 200, 14, key=f"xlen_{i}")
        with xc4:
            x_val = st.number_input(f"Exit Val {i+1}", -1000.0, 100000.0, 70.0, key=f"xval_{i}")
        exit_conditions.append({
            "indicator": x_ind, "operator": x_op, "value": x_val, "length": x_len,
        })

    if st.button("Run Backtest", type="primary"):
        if not entry_conditions:
            st.error("At least one entry condition is required")
        else:
            with st.spinner(f"Running backtest on {bt_symbol}..."):
                try:
                    payload = {
                        "symbol": bt_symbol,
                        "period": bt_period,
                        "interval": bt_interval,
                        "initial_capital": capital,
                        "position_size_pct": pos_size,
                        "stop_loss_pct": stop_loss,
                        "take_profit_pct": take_profit,
                        "condition_logic": logic,
                        "entry_conditions": entry_conditions,
                        "exit_conditions": exit_conditions,
                    }
                    result = run_backtest(payload)

                    # ── Metrics ──
                    metrics = result.get("metrics", {})
                    st.subheader("Performance Metrics")
                    m_cols = st.columns(4)
                    metric_items = [
                        ("Total Return", f"{metrics.get('total_return_pct', 0):.2f}%"),
                        ("CAGR", f"{metrics.get('cagr_pct', 0):.2f}%"),
                        ("Sharpe Ratio", f"{metrics.get('sharpe_ratio', 0):.2f}"),
                        ("Max Drawdown", f"{metrics.get('max_drawdown_pct', 0):.2f}%"),
                        ("Win Rate", f"{metrics.get('win_rate_pct', 0):.1f}%"),
                        ("Profit Factor", f"{metrics.get('profit_factor', 0):.2f}"),
                        ("Total Trades", str(metrics.get("total_trades", 0))),
                        ("Sortino Ratio", f"{metrics.get('sortino_ratio', 0):.2f}"),
                        ("Calmar Ratio", f"{metrics.get('calmar_ratio', 0):.2f}"),
                        ("Avg Win", f"{metrics.get('avg_win_pct', 0):.2f}%"),
                        ("Avg Loss", f"{metrics.get('avg_loss_pct', 0):.2f}%"),
                        ("Expectancy", f"${metrics.get('expectancy', 0):.2f}"),
                    ]
                    for idx, (label, value) in enumerate(metric_items):
                        m_cols[idx % 4].metric(label, value)

                    # ── Equity Curve ──
                    equity = result.get("equity_curve", [])
                    buy_hold = result.get("buy_hold_curve", [])
                    if equity:
                        st.subheader("Equity Curve")
                        eq_df = pd.DataFrame(equity)
                        eq_df["time"] = pd.to_datetime(eq_df["time"], unit="s")
                        fig_eq = go.Figure()
                        fig_eq.add_trace(go.Scatter(
                            x=eq_df["time"], y=eq_df["equity"],
                            mode="lines", name="Strategy",
                            line=dict(color="#2196F3", width=2),
                        ))
                        if buy_hold:
                            bh_df = pd.DataFrame(buy_hold)
                            bh_df["time"] = pd.to_datetime(bh_df["time"], unit="s")
                            fig_eq.add_trace(go.Scatter(
                                x=bh_df["time"], y=bh_df["equity"],
                                mode="lines", name="Buy & Hold",
                                line=dict(color="gray", width=1.5, dash="dash"),
                            ))
                        fig_eq.update_layout(
                            height=400, template="plotly_dark",
                            yaxis_title="Portfolio Value ($)",
                            legend=dict(orientation="h", yanchor="bottom", y=1.02),
                            margin=dict(l=50, r=20, t=20, b=20),
                        )
                        st.plotly_chart(fig_eq, use_container_width=True)

                    # ── Trade Log ──
                    trades = result.get("trades", [])
                    if trades:
                        st.subheader(f"Trade Log ({len(trades)} trades)")
                        tdf = pd.DataFrame(trades)
                        display_cols = [c for c in ["entry_date", "exit_date", "side", "entry_price",
                                                     "exit_price", "pnl", "pnl_pct", "exit_reason"] if c in tdf.columns]
                        if display_cols:
                            st.dataframe(tdf[display_cols], use_container_width=True, hide_index=True)

                except Exception as e:
                    st.error(f"Backtest failed: {e}")


# ── Page: System Status ─────────────────────────────────────────────────────

elif page == "System Status":
    st.header("System Status")

    try:
        status = fetch_status()
        col1, col2, col3 = st.columns(3)
        col1.metric("IBKR Connected", "Yes" if status.get("ibkr_connected") else "No")
        col2.metric("Sim Mode", "On" if status.get("sim_mode") else "Off")
        col3.metric("Bot Running", "Yes" if status.get("bot_running") else "No")

        st.json(status)
    except Exception as e:
        st.error(f"Cannot reach backend: {e}")
        st.info("Make sure the backend is running: `cd backend && uvicorn main:app --port 8000`")

    st.subheader("Data Health")
    try:
        health = fetch_data_health()
        st.json(health)
    except Exception as e:
        st.warning(f"Data health unavailable: {e}")

    # Quick price test
    st.subheader("Quick Price Test")
    test_sym = st.text_input("Test symbol", value="AAPL", key="test_sym")
    if st.button("Fetch Price"):
        try:
            r = requests.get(f"{API}/api/market/{test_sym.upper()}/price", timeout=10)
            st.json(r.json())
        except Exception as e:
            st.error(str(e))
