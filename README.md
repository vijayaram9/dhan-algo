# NIFTY SMART EDGE Version 1.0 (DhanHQ Algo Trading System)

An institutional, selective, multi-strategy intraday options buying engine for **NIFTY 50** options using Python and the official Dhan API (`dhanhq`).

---

## 1. System Configuration & Capital Constraints

| Parameter | Configuration |
| :--- | :--- |
| **Underlying** | NIFTY 50 (Signals analyzed on spot/futures, executed on options) |
| **Broker** | Dhan (DhanHQ Python v2.3.0 Client) |
| **Account Capital** | **₹25,000** |
| **Risk per Trade** | Preferred: ₹125–₹250 (0.5–1%) &bull; Absolute Ceiling: **₹500 (2%)** |
| **Max Daily Loss** | **₹500** (Engine stops trading immediately upon breach) |
| **Max Daily Trades** | **3 trades / day** (Mandatory stop on 2 consecutive losses) |
| **Lot Size** | **65 units** (NIFTY October 2025 contract revision) |
| **Position Policy** | Max 1 open position at a time &bull; Overnight holding strictly prohibited |
| **Operating Mode** | `PAPER` (Simulated execution) / `LIVE` (Broker execution) |

---

## 2. Multi-Strategy Architecture & Market Regimes

A central **Market Regime Detector** evaluates completed 5-minute candles and permits one strategy at a time:

1. **Strategy A – Trend Pullback (Priority 1 - Directional Market):**
   - 15m Close above 15m VWAP, EMA 9 > EMA 21 on both 15m & 5m.
   - 5m RSI(14) in 55–70 zone, ADX(14) > 20 and rising.
   - Pullback to 5m EMA 9, EMA 21, or VWAP.
   - Volume confirmation $\ge 1.2\times$ 20-candle average.
2. **Strategy B – Opening Range Breakout (Priority 2 - 09:30–10:00 & 13:30–14:30):**
   - 15-min range defined from 09:15–09:30 AM candles.
   - Breakout with volume $\ge 1.5\times$ 20-candle average, candle body $\ge 50\%$, within 0.5 ATR.
   - Max 2 breakout attempts per session.
3. **Strategy C – Mean Reversion (Priority 3 - Sideways / Range Market):**
   - Activated only when ADX < 18 and market is ranging.
   - Price touches Bollinger Band (20, 2) and closes back inside near daily pivot levels.
   - RSI < 30 (CE) or RSI > 70 (PE). Target: VWAP or Bollinger Middle Band.
4. **Gray Zone / Uncertain (ADX 18–20 or conflicting signals):**
   - Strict `NO_TRADE`. Capital preservation active. System remains in cash.

---

## 3. Two-Layer Exit Protection

- **Layer 1: Underlying Structural Stop:**
  - CE: Stop below pullback swing low or breakout retest low on NIFTY Spot.
  - PE: Stop above pullback swing high or breakout retest high on NIFTY Spot.
  - Target 1: 1.5R &bull; Target 2: 2.0R &bull; Trailing SL: 1 ATR underlying movement after 1R.
- **Layer 2: Hard Option-Premium Stop:**
  - Independent emergency exit triggered at **-20% below option entry premium**.
  - Whichever trigger occurs first executes the exit immediately.
- **Time-Based Exit:**
  - Max holding duration: **45–60 minutes** (Theta preservation).
  - Force square-off: **15:15 IST** (no new entries permitted after 14:45 IST).

---

## 4. Web Dashboard & Terminal Control

- **Web Dashboard URL:** `http://localhost:5000` (or `http://192.168.1.20:5000` from your phone/tablet on same Wi-Fi).
- **TradingView Integration:** Live TradingView charts with direct link to `tv.dhan.co`.
- **Institutional Order Flow:** Cumulative Volume Delta (CVD), Put-Call Ratio (PCR), and Order Book Imbalance.
- **Signal Audit Table:** Live checklist verifying all mandatory conditions on completed candles.
- **Mobile Push Alerts:** Free mobile lockscreen notifications via `ntfy.sh/sai-nifty-trades-9422`.
- **Trading Journal:** Real-time SQLite + CSV logging with exact SEBI/NSE taxes, Dhan brokerage, and net P&L.

---

## 5. Quick Launch

- **Web Dashboard:** Double-click [`run_flask_dashboard.bat`](file:///d:/Automation%20Projects/dhan-nifty-algo/run_flask_dashboard.bat)
- **Console Terminal:** Double-click [`run_trader.bat`](file:///d:/Automation%20Projects/dhan-nifty-algo/run_trader.bat)
- **View Journal:** Double-click [`view_journal.bat`](file:///d:/Automation%20Projects/dhan-nifty-algo/view_journal.bat)
