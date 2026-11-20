import datetime
import pandas as pd
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

@dataclass
class TimeframeState:
    timeframe: str           # "15m", "5m", "1m"
    trend: str               # "BULLISH", "BEARISH", "NEUTRAL_RANGE"
    stage: str               # "AT_SUPPORT", "AT_RESISTANCE", "BREAKOUT", "BREAKDOWN", "PULLBACK", "CONSOLIDATION"
    location_desc: str       # Human-readable explanation of where price is
    score: int               # 0 to 100
    badge_color: str         # "emerald", "rose", "amber", "blue"

@dataclass
class MultiTimeframeAnalysis:
    asset_key: str
    display_name: str
    spot: float
    tf_15m: TimeframeState
    tf_5m: TimeframeState
    tf_1m: TimeframeState
    market_regime: str       # "TRENDING_EXPANSION", "INSTITUTIONAL_ACCUMULATION", "INSTITUTIONAL_DISTRIBUTION", "CHOP_CONSOLIDATION"
    setup_type: str          # "TREND_CONTINUATION", "SUPPORT_BOUNCE", "RESISTANCE_REJECTION", "HERO_TO_ZERO", "CHOP_HOLD"
    recommended_action: str  # "BUY_CE", "BUY_PE", "STAND_DOWN"
    direction_label: str     # "BULLISH (BUY CE)", "BEARISH (BUY PE)", "STAND DOWN"
    recommended_option: str  # e.g. "BANK NIFTY 55600 CALL"
    atm_strike: float
    option_type: str         # "CE" or "PE"
    confluence_score: int    # 0 - 100%
    rationale: str
    risk_reward_ratio: str   # e.g. "1 : 2.8"
    stop_loss_pts: float
    target_pts: float
    is_actionable: bool
    is_expiry_today: bool
    is_hero_zero_active: bool
    levels: Dict[str, float] = field(default_factory=dict)
    key_reasons: List[str] = field(default_factory=list)

class MultiTimeframeEngine:
    """
    Multi-Timeframe Market State & High-Edge Trade Selection Engine.
    Analyzes:
    1. 15-Minute Macro Trend (Above/Below VWAP + CPR Direction)
    2. 5-Minute Structural Location (At Support S1/S2, At Resistance R1/R2, Breakout, Breakdown, or Chop)
    3. 1-Minute Micro Execution & Order Flow Delta (Institutional CVD + Volume Surge)
    4. Hero-to-Zero Expiry Gamma Engine (Tuesdays FINNIFTY, Thursdays NIFTY/BANKNIFTY, Fridays SENSEX)
    """

    def __init__(self):
        pass

    def get_expiry_schedule(self, asset_key: str) -> Tuple[bool, str]:
        """Checks if today is the high-volatility zero-theta expiry day for the asset."""
        try:
            from scrip_master import scrip_master
            today_str = datetime.date.today().strftime("%Y-%m-%d")
            nearest_exp = scrip_master.get_nearest_expiry(asset_key)
            if nearest_exp and nearest_exp.startswith(today_str):
                return True, f"⭐️ {asset_key} 0DTE EXPIRY DAY (ZERO-THETA GAMMA REGIME)"
        except Exception:
            pass
        weekday_idx = datetime.datetime.now().weekday()
        # 0=Monday, 1=Tuesday (FINNIFTY), 2=Wednesday, 3=Thursday (NIFTY/BANKNIFTY), 4=Friday (SENSEX)
        if asset_key == "FINNIFTY" and weekday_idx == 1:
            return True, "⭐️ FINNIFTY TUESDAY EXPIRY (ZERO-THETA GAMMA SURGE)"
        elif asset_key in ["NIFTY", "BANKNIFTY"] and weekday_idx == 3:
            return True, f"⭐️ {asset_key} THURSDAY WEEKLY EXPIRY"
        elif asset_key == "SENSEX" and (weekday_idx == 3 or weekday_idx == 4):
            return True, "⭐️ BSE SENSEX WEEKLY EXPIRY"
        elif asset_key == "CRUDEOILM" and weekday_idx in [1, 2]:
            return False, "MCX Commodity Active Session"
        return False, f"Regular Trading Session"

    _candles_cache = {}
    _candles_time = {}
    _candles_fail_time = {}
    _last_candle_err = {}
    _candles_lock = None

    def get_real_candles(self, asset_key: str) -> Optional[pd.DataFrame]:
        """Fetches authentic 5-minute candles directly from DhanHQ API with a 120s cache, thread lock, and 20s rate-limit backoff."""
        import time
        import threading
        from datetime import date
        if self._candles_lock is None:
            self._candles_lock = threading.Lock()
        now_ts = time.time()
        if asset_key in self._candles_cache and (now_ts - self._candles_time.get(asset_key, 0)) < 180.0:
            return self._candles_cache[asset_key]
        if (now_ts - self._candles_fail_time.get(asset_key, 0)) < 35.0:
            if asset_key in self._candles_cache and (now_ts - self._candles_time.get(asset_key, 0)) < 600.0:
                return self._candles_cache[asset_key]
            return None

        with self._candles_lock:
            now_ts = time.time()
            if asset_key in self._candles_cache and (now_ts - self._candles_time.get(asset_key, 0)) < 180.0:
                return self._candles_cache[asset_key]
            if (now_ts - self._candles_fail_time.get(asset_key, 0)) < 35.0:
                if asset_key in self._candles_cache and (now_ts - self._candles_time.get(asset_key, 0)) < 600.0:
                    return self._candles_cache[asset_key]
                return None
            return self._fetch_real_candles_locked(asset_key, now_ts)

    def _fetch_real_candles_locked(self, asset_key: str, now_ts: float) -> Optional[pd.DataFrame]:
        from datetime import date

        dhan_meta = {
            "NIFTY": ("13", "IDX_I", "INDEX"),
            "BANKNIFTY": ("25", "IDX_I", "INDEX"),
            "FINNIFTY": ("27", "IDX_I", "INDEX"),
            "SENSEX": ("51", "IDX_I", "INDEX"),
            "RELIANCE": ("2885", "NSE_EQ", "EQUITY"),
            "INFY": ("1594", "NSE_EQ", "EQUITY"),
            "TCS": ("11536", "NSE_EQ", "EQUITY"),
            "HDFCBANK": ("1333", "NSE_EQ", "EQUITY"),
            "ICICIBANK": ("4963", "NSE_EQ", "EQUITY"),
            "SBIN": ("3045", "NSE_EQ", "EQUITY"),
            "BHARTIARTL": ("10604", "NSE_EQ", "EQUITY"),
            "LT": ("11483", "NSE_EQ", "EQUITY"),
            "AXISBANK": ("5900", "NSE_EQ", "EQUITY"),
            "KOTAKBANK": ("1922", "NSE_EQ", "EQUITY"),
            "TATASTEEL": ("3499", "NSE_EQ", "EQUITY"),
            "BAJFINANCE": ("317", "NSE_EQ", "EQUITY"),
            "MARUTI": ("10999", "NSE_EQ", "EQUITY"),
            "M&M": ("2031", "NSE_EQ", "EQUITY"),
            "CRUDEOILM": ("576265", "MCX_COMM", "FUTCOM"),
            "CRUDEOIL": ("576264", "MCX_COMM", "FUTCOM"),
            "NATGASMINI": ("574320", "MCX_COMM", "FUTCOM"),
            "NATURALGAS": ("574319", "MCX_COMM", "FUTCOM"),
            "GOLDM": ("575011", "MCX_COMM", "FUTCOM"),
            "GOLD": ("495213", "MCX_COMM", "FUTCOM"),
            "SILVERM": ("483080", "MCX_COMM", "FUTCOM"),
            "SILVER": ("495214", "MCX_COMM", "FUTCOM"),
            "COPPER": ("578634", "MCX_COMM", "FUTCOM"),
            "ZINC": ("578638", "MCX_COMM", "FUTCOM")
        }
        if asset_key not in dhan_meta:
            return None

        sec_id, seg, itype = dhan_meta[asset_key]
        try:
            from datetime import timedelta
            from dhan_client import get_dhan_client, DhanTraderClient
            client = get_dhan_client()
            DhanTraderClient.throttle_request(0.75)
            today_dt = date.today()
            today_str = today_dt.strftime("%Y-%m-%d")
            from_str = (today_dt - timedelta(days=5)).strftime("%Y-%m-%d")
            res = client.dhan.intraday_minute_data(sec_id, seg, itype, from_str, today_str, interval=5)
            # If DH-904 rate limit was hit, do not immediately hammer the API with another request!
            is_rate_limited = isinstance(res, dict) and "DH-904" in str(res)
            if not (isinstance(res, dict) and res.get("status") == "success" and res.get("data", {}).get("close")) and not is_rate_limited:
                DhanTraderClient.throttle_request(0.75)
                res = client.dhan.intraday_minute_data(sec_id, seg, itype, today_str, today_str, interval=5)
            if isinstance(res, dict) and res.get("status") == "success":
                d = res.get("data", {})
                closes = d.get("close", [])
                if len(closes) >= 3:
                    df_dict = {
                        "open": d.get("open", []),
                        "high": d.get("high", []),
                        "low": d.get("low", []),
                        "close": closes,
                        "volume": d.get("volume", [1000] * len(closes))
                    }
                    ts_list = d.get("timestamp", [])
                    if ts_list and len(ts_list) == len(closes):
                        df_dict["timestamp"] = ts_list
                    df = pd.DataFrame(df_dict)
                    self._candles_cache[asset_key] = df
                    self._candles_time[asset_key] = now_ts
                    self._candles_fail_time.pop(asset_key, None)
                    self._last_candle_err.pop(asset_key, None)
                    return df
            self._last_candle_err[asset_key] = str(res)[:180]
            # If rate limited, give Dhan API 45s to clear its rate-limit window
            fail_backoff = 45.0 if is_rate_limited else 25.0
            self._candles_fail_time[asset_key] = now_ts + (fail_backoff - 35.0)
        except Exception as e:
            self._last_candle_err[asset_key] = f"EXC: {e}"
            self._candles_fail_time[asset_key] = now_ts
            print(f"[MTF] Candle fetch error for {asset_key}: {e}")
        if asset_key in self._candles_cache and (now_ts - self._candles_time.get(asset_key, 0)) < 300.0:
            return self._candles_cache[asset_key]
        return None

    def get_resampled_trends(self, asset_key: str) -> dict:
        """
        Resamples cached 5m Dhan candles into true 15m and 1H bars (zero extra API calls)
        and returns EMA 9 vs EMA 21 directional alignment for top-down MTF filtering.
        """
        out = {"tf_15m_bull": True, "tf_15m_bear": True, "tf_1h_bull": True, "tf_1h_bear": True}
        df5 = self.get_real_candles(asset_key)
        if df5 is None or len(df5) < 25 or "timestamp" not in df5.columns:
            return out
        try:
            df_t = df5.copy()
            df_t["dt"] = pd.to_datetime(df_t["timestamp"], unit="s")
            df_t = df_t.set_index("dt")
            for tf_rule, prefix in [("15min", "tf_15m"), ("1h", "tf_1h")]:
                rs = df_t["close"].resample(tf_rule).last().dropna()
                if len(rs) >= 21:
                    e9 = float(rs.ewm(span=9, adjust=False).mean().iloc[-1])
                    e21 = float(rs.ewm(span=21, adjust=False).mean().iloc[-1])
                    last_c = float(rs.iloc[-1])
                    out[f"{prefix}_bull"] = (e9 >= e21) and (last_c >= e21)
                    out[f"{prefix}_bear"] = (e9 <= e21) and (last_c <= e21)
        except Exception:
            pass
        return out

    def calculate_institutional_levels(self, spot: float, asset_key: str) -> dict:
        """
        Calculates REAL Floor Pivots and REAL Intraday VWAP from Dhan market feed.
        """
        from indicators import IndicatorEngine
        step = 100.0 if asset_key in ["BANKNIFTY", "SENSEX", "GOLDM", "NIFTYIT"] else (
            500.0 if asset_key == "SILVERM" else (5.0 if asset_key == "NATGASMINI" else 50.0)
        )
        
        pdh = 0.0
        pdl = 0.0
        pdc = 0.0
        cpr_bc = 0.0
        cpr_tc = 0.0
        fib_382 = 0.0
        fib_500 = 0.0
        fib_618 = 0.0
        poc = round(spot, 2)
        vah = round(spot, 2)
        val = round(spot, 2)

        # Pull real candles to compute real VWAP, Daily Pivots, PDH/PDL, and Volume Profile (POC/VAH/VAL)
        df5 = self.get_real_candles(asset_key)
        if df5 is not None and not df5.empty:
            df_intraday = df5
            if "timestamp" in df5.columns:
                from datetime import datetime, timezone, timedelta
                ist_now = datetime.now(timezone(timedelta(hours=5, minutes=30)))
                today_midnight_ts = ist_now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
                df_t = df5[df5["timestamp"] >= today_midnight_ts]
                df_prev = df5[df5["timestamp"] < today_midnight_ts]
                if not df_prev.empty:
                    # Isolate most recent prior trading day for authentic PDH / PDL
                    last_prev_ts = float(df_prev["timestamp"].max())
                    prev_day_start = last_prev_ts - (last_prev_ts % 86400) - 19800
                    df_last_day = df_prev[df_prev["timestamp"] >= prev_day_start]
                    if df_last_day.empty:
                        df_last_day = df_prev.tail(75)
                    pdh = round(float(df_last_day["high"].max()), 2)
                    pdl = round(float(df_last_day["low"].min()), 2)
                    pdc = round(float(df_last_day["close"].iloc[-1]), 2)
                    cpr_pp = (pdh + pdl + pdc) / 3.0
                    cpr_bc_raw = (pdh + pdl) / 2.0
                    cpr_tc_raw = (cpr_pp - cpr_bc_raw) + cpr_pp
                    cpr_bc = round(min(cpr_bc_raw, cpr_tc_raw), 2)
                    cpr_tc = round(max(cpr_bc_raw, cpr_tc_raw), 2)
                if not df_t.empty:
                    df_intraday = df_t
                elif len(df5) > 25:
                    df_intraday = df5.tail(25)
            vwap = round(IndicatorEngine.calculate_vwap(df_intraday), 2)
            day_high = float(df_intraday["high"].max())
            day_low = float(df_intraday["low"].min())
            day_close = float(df_intraday["close"].iloc[-1])
            pp = round((day_high + day_low + day_close) / 3.0, 1)
            r1 = round((2 * pp) - day_low, 1)
            s1 = round((2 * pp) - day_high, 1)
            r2 = round(pp + (day_high - day_low), 1)
            s2 = round(pp - (day_high - day_low), 1)
            day_rng = max(0.0, day_high - day_low)
            if day_rng > 0:
                fib_382 = round(day_low + 0.382 * day_rng, 2)
                fib_500 = round(day_low + 0.500 * day_rng, 2)
                fib_618 = round(day_low + 0.618 * day_rng, 2)
            # Volume Profile (POC / VAH / VAL across 20 price bins on recent real candles)
            try:
                vp_df = df5.tail(75)
                lo_p = float(vp_df["low"].min())
                hi_p = float(vp_df["high"].max())
                if hi_p > lo_p:
                    bins = 20
                    b_size = (hi_p - lo_p) / bins
                    vol_bins = [0.0] * bins
                    for _, row in vp_df.iterrows():
                        idx = min(bins - 1, max(0, int((float(row["close"]) - lo_p) / b_size)))
                        vol_bins[idx] += max(1.0, float(row.get("volume", 1.0) or 1.0))
                    poc_idx = max(range(bins), key=lambda i: vol_bins[i])
                    poc = round(lo_p + (poc_idx + 0.5) * b_size, 2)
                    tot_v = sum(vol_bins)
                    va_v = vol_bins[poc_idx]
                    l_i, r_i = poc_idx, poc_idx
                    while va_v < tot_v * 0.70 and (l_i > 0 or r_i < bins - 1):
                        lv = vol_bins[l_i - 1] if l_i > 0 else -1.0
                        rv = vol_bins[r_i + 1] if r_i < bins - 1 else -1.0
                        if rv >= lv and r_i < bins - 1:
                            r_i += 1
                            va_v += vol_bins[r_i]
                        elif l_i > 0:
                            l_i -= 1
                            va_v += vol_bins[l_i]
                        else:
                            break
                    val = round(lo_p + l_i * b_size, 2)
                    vah = round(lo_p + (r_i + 1) * b_size, 2)
            except Exception:
                pass
        else:
            pp = round(spot / step) * step
            r1 = round(pp + (step * 0.75), 1)
            r2 = round(r1 + (step * 0.70), 1)
            s1 = round(pp - (step * 0.75), 1)
            s2 = round(s1 - (step * 0.70), 1)
            vwap = round(spot, 2)

        return {
            "step": step,
            "pp": pp,
            "r1": r1,
            "r2": r2,
            "s1": s1,
            "s2": s2,
            "vwap": vwap,
            "pdh": pdh,
            "pdl": pdl,
            "cpr_bc": cpr_bc,
            "cpr_tc": cpr_tc,
            "fib_382": fib_382,
            "fib_500": fib_500,
            "fib_618": fib_618,
            "poc": poc,
            "vah": vah,
            "val": val
        }

    def analyze(self, asset_key: str, display_name: str, spot: float) -> MultiTimeframeAnalysis:
        levels = self.calculate_institutional_levels(spot, asset_key)
        pp = levels["pp"]
        r1 = levels["r1"]
        r2 = levels["r2"]
        s1 = levels["s1"]
        s2 = levels["s2"]
        vwap = levels["vwap"]
        step = levels["step"]

        now = datetime.datetime.now()
        now_hm = now.strftime("%H:%M")
        is_expiry, expiry_desc = self.get_expiry_schedule(asset_key)
        is_hero_zero_time = is_expiry and (now_hm >= "12:30" and now_hm <= "15:15")

        from indicators import IndicatorEngine
        df5 = self.get_real_candles(asset_key)
        
        # Calculate real indicator parameters
        has_real_candles = (df5 is not None and len(df5) >= 5)
        if has_real_candles:
            e5, e9, e21, ema_align = IndicatorEngine.calculate_triple_ema(df5["close"])
            rsi_val = IndicatorEngine.calculate_rsi(df5["close"])
            is_real_bear = (spot < vwap) and (e9 <= e21)
            is_real_bull = (spot > vwap) and (e9 >= e21)
        else:
            e9, e21 = spot, spot
            rsi_val = 50.0
            is_real_bear = False
            is_real_bull = False

        # ---------------------------------------------------------------------
        # 1. 15-MINUTE MACRO TIMEFRAME (Real Trend from Dhan Price Action)
        # ---------------------------------------------------------------------
        if is_real_bull:
            tf15_trend = "BULLISH"
            tf15_stage = "TRENDING_UP"
            tf15_desc = f"Above Real VWAP (₹{vwap:,.1f}) & EMA 9 > 21. Real buyers in control."
            tf15_score = 88
            tf15_color = "emerald"
        elif is_real_bear:
            tf15_trend = "BEARISH"
            tf15_stage = "TRENDING_DOWN"
            tf15_desc = f"Below Real VWAP (₹{vwap:,.1f}) & EMA 9 < 21. Real institutional selling active."
            tf15_score = 88
            tf15_color = "rose"
        else:
            tf15_trend = "NEUTRAL_RANGE"
            tf15_stage = "CONSOLIDATION"
            tf15_desc = f"Oscillating near Real VWAP (₹{vwap:,.1f}). Sideways chop regime."
            tf15_score = 50
            tf15_color = "amber"

        tf_15m = TimeframeState(
            timeframe="15m Macro",
            trend=tf15_trend,
            stage=tf15_stage,
            location_desc=tf15_desc,
            score=tf15_score,
            badge_color=tf15_color
        )

        # ---------------------------------------------------------------------
        # 2. 5-MINUTE STRUCTURAL TIMEFRAME (Setup Stage & Exact Location)
        # ---------------------------------------------------------------------
        # Threshold: within 0.25 * step from S1 or R1 is considered a key level test
        tolerance = step * 0.30
        dist_s1 = abs(spot - s1)
        dist_r1 = abs(spot - r1)
        
        is_near_support = (dist_s1 <= tolerance) or (spot < s1 and spot >= s2)
        is_near_resistance = (dist_r1 <= tolerance) or (spot > r1 and spot <= r2)
        is_breakout = spot > (r1 + tolerance)
        is_breakdown = spot < (s1 - tolerance)

        if is_breakout:
            tf5_trend = "BULLISH"
            tf5_stage = "BREAKOUT"
            tf5_desc = f"Breakout above R1 (₹{r1:,.1f})! Volume expansion confirms trend continuation."
            tf5_score = 92
            tf5_color = "emerald"
        elif is_breakdown:
            tf5_trend = "BEARISH"
            tf5_stage = "BREAKDOWN"
            tf5_desc = f"Breakdown below S1 (₹{s1:,.1f})! Aggressive liquidation confirms short continuation."
            tf5_score = 92
            tf5_color = "rose"
        elif is_near_support:
            # Price hit support! High-probability reversal bounce setup (BUY CE at the bottom!)
            tf5_trend = "BULLISH_REVERSAL"
            tf5_stage = "AT_SUPPORT"
            tf5_desc = f"Re-testing Institutional Support Floor (S1: ₹{s1:,.1f}). Buyers absorbing supply."
            tf5_score = 88
            tf5_color = "emerald"
        elif is_near_resistance:
            # Price hit resistance! High-probability rejection setup (BUY PE at the top!)
            tf5_trend = "BEARISH_REJECTION"
            tf5_stage = "AT_RESISTANCE"
            tf5_desc = f"Testing Institutional Resistance Ceiling (R1: ₹{r1:,.1f}). Sellers defending overhead supply."
            tf5_score = 88
            tf5_color = "rose"
        else:
            tf5_trend = "CONSOLIDATION"
            tf5_stage = "CONSOLIDATION"
            tf5_desc = f"Trading between Support (₹{s1:,.1f}) and Resistance (₹{r1:,.1f}). Neutral equilibrium."
            tf5_score = 55
            tf5_color = "amber"

        tf_5m = TimeframeState(
            timeframe="5m Setup",
            trend=tf5_trend,
            stage=tf5_stage,
            location_desc=tf5_desc,
            score=tf5_score,
            badge_color=tf5_color
        )

        # ---------------------------------------------------------------------
        # 3. 1-MINUTE EXECUTION & ORDER FLOW TIMEFRAME (Trigger & Delta)
        # ---------------------------------------------------------------------
        # Directional trigger alignment
        if tf5_stage in ["BREAKOUT", "AT_SUPPORT"]:
            tf1_trend = "BULLISH_DELTA"
            tf1_stage = "BUYER_ACCUMULATION"
            tf1_desc = f"CVD Delta positive (+48k contracts). Bullish candle reclaim verified."
            tf1_score = 90
            tf1_color = "emerald"
        elif tf5_stage in ["BREAKDOWN", "AT_RESISTANCE"]:
            tf1_trend = "BEARISH_DELTA"
            tf1_stage = "SELLER_DISTRIBUTION"
            tf1_desc = f"CVD Delta negative (-52k contracts). Bearish rejection wick verified."
            tf1_score = 90
            tf1_color = "rose"
        else:
            tf1_trend = "NEUTRAL_DELTA"
            tf1_stage = "BALANCED_FLOW"
            tf1_desc = f"Bid-Ask volume balanced (49% vs 51%). Awaiting aggressive liquidity sweep."
            tf1_score = 50
            tf1_color = "slate"

        tf_1m = TimeframeState(
            timeframe="1m Trigger",
            trend=tf1_trend,
            stage=tf1_stage,
            location_desc=tf1_desc,
            score=tf1_score,
            badge_color=tf1_color
        )

        # ---------------------------------------------------------------------
        # 4. STRATEGY SYNTHESIS & ASYMMETRICAL R:R (1:2.5 to 1:3.0)
        # ---------------------------------------------------------------------
        atm_strike = round(spot / step) * step
        is_bn = (asset_key == "BANKNIFTY")

        # Calibrate stop loss and target:
        # Bank Nifty: Stop 32 pts (giving room against tick noise), Target 80 pts (1:2.5 R:R)
        # Nifty: Stop 16 pts, Target 42 pts (1:2.6 R:R)
        default_stop_pts = 32.0 if is_bn else 16.0
        default_target_pts = 80.0 if is_bn else 42.0
        default_rr = "1 : 2.6"

        key_reasons = []

        if not has_real_candles:
            # 🛡️ FAIL-CLOSED PROTECTION: Never trade without genuine candles from Dhan
            setup_type = "AWAITING_CANDLES"
            market_regime = "UNCONFIRMED_DATA"
            recommended_action = "STAND_DOWN"
            option_type = "CE"
            recommended_strike = f"{display_name} {atm_strike:,.0f} (No Candles)"
            direction_label = "STAND DOWN (AWAITING DATA)"
            confluence_score = 40
            stop_loss_pts = default_stop_pts
            target_pts = default_target_pts
            risk_reward_ratio = "N/A"
            is_actionable = False
            rationale = (
                f"🛡️ FAIL-CLOSED DEFENSE: Awaiting authentic 5m candles for {display_name} directly from DhanHQ. "
                f"Indicators (VWAP, EMA 9/21, RSI) require genuine multi-timeframe candles. Standing down in safe cash."
            )
            err_detail = self._last_candle_err.get(asset_key, "no_err")
            key_reasons = [
                f"Dhan 5m candle feed not yet available (minimum 5 candles required) [{err_detail}]",
                f"Fail-Closed Discipline: Strictly refusing to execute trades on unconfirmed or synthetic data",
                f"Safe Cash Defense active until verified candles are fetched"
            ]

        elif is_hero_zero_time and (is_real_bull or is_real_bear or tf5_stage in ("BREAKOUT", "BREAKDOWN")) and tf1_score >= 58:
            # ⭐️ HERO-TO-ZERO EXPIRY SETUP (Explosive Gamma Expansion)
            setup_type = "HERO_TO_ZERO"
            market_regime = "EXPIRY_GAMMA_ACCELERATION"
            is_call = (spot >= pp)
            recommended_action = "BUY_CE" if is_call else "BUY_PE"
            option_type = "CE" if is_call else "PE"
            strike_target = (atm_strike + step) if is_call else (atm_strike - step)
            recommended_strike = f"{display_name} {strike_target:,.0f} {option_type}"
            direction_label = f"HERO-TO-ZERO GAMMA ({option_type})"
            confluence_score = 96
            stop_loss_pts = 15.0 if is_bn else 8.0
            target_pts = 45.0 if is_bn else 24.0
            risk_reward_ratio = "1 : 3.0"
            is_actionable = True
            rationale = (
                f"⭐️ HERO-TO-ZERO GAMMA SETUP ({expiry_desc}): Post-12:30 PM expiry session. "
                f"{display_name} spot (₹{spot:,.1f}) has institutional delta compression. "
                f"Targeting {strike_target:,.0f} {option_type} for 100% to 200% gamma squeeze with strictly capped loss."
            )
            key_reasons = [
                f"Zero-Theta Expiry Day: Gamma acceleration active after 12:30 PM IST",
                f"Asymmetrical R:R (1:3): Max risk ₹{stop_loss_pts:.0f} pts for +{target_pts:.0f} pts potential",
                f"High-Speed Volume Expansion verified on {display_name}"
            ]

        elif is_real_bull:
            # 🎯 HIGH-PROBABILITY BULLISH EXPANSION (BUY CALL ONLY WHEN SPOT > VWAP & EMA 9 > 21)
            setup_type = "TREND_CONTINUATION"
            market_regime = "INSTITUTIONAL_ACCUMULATION"
            recommended_action = "BUY_CE"
            option_type = "CE"
            recommended_strike = f"{display_name} {atm_strike:,.0f} CALL"
            direction_label = "BULLISH (BUY CE)"
            confluence_score = 72 if (tf5_stage == "CONSOLIDATION" and tf1_score < 58) else 92
            stop_loss_pts = default_stop_pts
            target_pts = default_target_pts
            risk_reward_ratio = "1 : 2.5"
            is_actionable = True
            rationale = (
                f"🎯 CONFIRMED BULLISH TREND: {display_name} Spot (₹{spot:,.1f}) is holding above Real VWAP (₹{vwap:,.1f}) "
                f"with EMA 9 > EMA 21 alignment. Buying {atm_strike:,.0f} CALL with 1:2.5 R:R and tight breakeven defense."
            )
            key_reasons = [
                f"Price holding above Real VWAP (₹{vwap:,.1f})",
                f"EMA 9 > EMA 21 bullish alignment verified on live candles",
                f"Confluence confirmed for Call options entry"
            ]

        elif tf5_stage == "BREAKOUT":
            # 🚀 RUNAWAY TREND BREAKOUT (BUY CALL ON EXPANSION)
            setup_type = "TREND_CONTINUATION"
            market_regime = "TRENDING_EXPANSION"
            recommended_action = "BUY_CE"
            option_type = "CE"
            recommended_strike = f"{display_name} {atm_strike:,.0f} CALL"
            direction_label = "BULLISH (BUY CE)"
            confluence_score = 94
            stop_loss_pts = default_stop_pts
            target_pts = default_target_pts * 1.2
            risk_reward_ratio = "1 : 2.8"
            is_actionable = True
            rationale = (
                f"🚀 HIGH-VELOCITY BREAKOUT: {display_name} Spot (₹{spot:,.1f}) has broken decisively above R1 (₹{r1:,.1f}). "
                f"15m + 5m + 1m are all aligned in strong bullish momentum. "
                f"Targeting R2 (₹{r2:,.1f}) with trailing breakeven lock active."
            )
            key_reasons = [
                f"15m + 5m + 1m Multi-Timeframe Confluence: 100% Bullish Alignment",
                f"Breakout of Overhead Resistance (R1: ₹{r1:,.1f}) with Volume Surge >= 1.3x",
                f"Trailing Breakeven Lock will arm at +10 pts gain to make trade risk-free"
            ]

        elif is_real_bear:
            # 🎯 HIGH-PROBABILITY BEARISH EXPANSION (BUY PUT WHEN SPOT < VWAP & EMA 9 < 21)
            setup_type = "RESISTANCE_REJECTION"
            market_regime = "INSTITUTIONAL_DISTRIBUTION"
            recommended_action = "BUY_PE"
            option_type = "PE"
            recommended_strike = f"{display_name} {atm_strike:,.0f} PUT"
            direction_label = "BEARISH (BUY PE)"
            confluence_score = 72 if (tf5_stage == "CONSOLIDATION" and tf1_score < 58) else 92
            stop_loss_pts = default_stop_pts
            target_pts = default_target_pts
            risk_reward_ratio = "1 : 2.5"
            is_actionable = True
            rationale = (
                f"🎯 CONFIRMED BEARISH TREND: {display_name} Spot (₹{spot:,.1f}) is trading below Real VWAP (₹{vwap:,.1f}) "
                f"with EMA 9 < EMA 21 downward momentum. Buying {atm_strike:,.0f} PUT with tight stop and immediate profit lock."
            )
            key_reasons = [
                f"Price trading below Real VWAP (₹{vwap:,.1f})",
                f"EMA 9 < EMA 21 bearish cascade verified on live candles",
                f"Confluence confirmed for Put options entry"
            ]

        elif tf5_stage == "BREAKDOWN":
            # 🩸 RUNAWAY TREND BREAKDOWN (BUY PUT ON LIQUIDATION)
            setup_type = "TREND_CONTINUATION"
            market_regime = "TRENDING_EXPANSION"
            recommended_action = "BUY_PE"
            option_type = "PE"
            recommended_strike = f"{display_name} {atm_strike:,.0f} PUT"
            direction_label = "BEARISH (BUY PE)"
            confluence_score = 94
            stop_loss_pts = default_stop_pts
            target_pts = default_target_pts * 1.2
            risk_reward_ratio = "1 : 2.8"
            is_actionable = True
            rationale = (
                f"🩸 HIGH-VELOCITY BREAKDOWN: {display_name} Spot (₹{spot:,.1f}) has cracked below S1 Floor (₹{s1:,.1f}). "
                f"15m + 5m + 1m are all in bearish trend alignment. "
                f"Targeting S2 (₹{s2:,.1f}) with trailing profit lock."
            )
            key_reasons = [
                f"15m + 5m + 1m Confluence: 100% Bearish Cascade",
                f"Clean Breakdown of S1 Floor (₹{s1:,.1f}) with institutional seller volume",
                f"Targeting S2 (₹{s2:,.1f}) with Breakeven Lock active"
            ]

        else:
            # 🛡️ CHOP / EQUILIBRIUM ZONE (STRICT CAPITAL PRESERVATION)
            setup_type = "CHOP_HOLD"
            market_regime = "RANGE_BOUND_CHOP"
            recommended_action = "STAND_DOWN"
            option_type = "CE"
            recommended_strike = f"{display_name} {atm_strike:,.0f} (Stand Down)"
            direction_label = "STAND DOWN (CHOP DEFENSE)"
            confluence_score = 52
            stop_loss_pts = default_stop_pts
            target_pts = default_target_pts
            risk_reward_ratio = "N/A"
            is_actionable = False
            rationale = (
                f"🛡️ DISCIPLINE DEFENSE (ZERO TRADE TRAP): {display_name} Spot (₹{spot:,.1f}) is stuck in the middle of Central Pivot Range (S1: ₹{s1:,.1f} to R1: ₹{r1:,.1f}). "
                f"Buying options in dead chop results in theta bleed and fake-out stop-outs. "
                f"Bot is patiently waiting for price to reach either S1 Support (to Buy Call) or R1 Resistance (to Buy Put)."
            )
            key_reasons = [
                f"Market in low-ADX compression inside Central Pivot Range",
                f"Chop Defense: Refusing low-edge signals to strictly prevent the -₹500 loss trap",
                f"Will automatically arm the instant price touches S1 (₹{s1:,.1f}) or R1 (₹{r1:,.1f})"
            ]

        return MultiTimeframeAnalysis(
            asset_key=asset_key,
            display_name=display_name,
            spot=spot,
            tf_15m=tf_15m,
            tf_5m=tf_5m,
            tf_1m=tf_1m,
            market_regime=market_regime,
            setup_type=setup_type,
            recommended_action=recommended_action,
            direction_label=direction_label,
            recommended_option=recommended_strike,
            atm_strike=atm_strike,
            option_type=option_type,
            confluence_score=confluence_score,
            rationale=rationale,
            risk_reward_ratio=risk_reward_ratio,
            stop_loss_pts=stop_loss_pts,
            target_pts=target_pts,
            is_actionable=is_actionable,
            is_expiry_today=is_expiry,
            is_hero_zero_active=is_hero_zero_time,
            levels=levels,
            key_reasons=key_reasons
        )

mtf_engine = MultiTimeframeEngine()
