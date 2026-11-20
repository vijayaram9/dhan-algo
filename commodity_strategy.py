"""
Dedicated 5-Video MCX Commodity Options Strategy Engine (100% Isolated from Indian Equities)
============================================================================================
Built directly from the 5 proven intraday commodity trading frameworks:
  - Videos 1 & 5 (Siddharth Bhanushali): 15-minute 66 SMA trend slope + Stochastic(14, 2) [75 / 35]
      * Rising 66 SMA + Stochastic crosses UP above 35  -> BUY CALL
      * Falling 66 SMA + Stochastic crosses DOWN below 75 -> BUY PUT
      * Flat 66 SMA -> Stay in Cash (Zero Chop!)
  - Video 4 (5:45 Ka Formula): 17:45-18:00 IST 15m candle High / Low / 50% Midpoint breakout
      * After 18:00 IST, confirmed 5m close beyond 5:45 High/Low aligned with 66 SMA
      * Strict discipline: Max 2 trades per commodity family per evening + 15m mandatory cooldown
  - Video 2 (Groww Millionaire Trader): 1.2x 15m ATR volatility-adjusted breathing stop
  - Video 3 (Ravindra Rokde): Institutional volume influx (>= 1.35x 20-bar avg) on breakouts

Leaves the winning Indian Equity & Index strategy (09:25 - 15:05 IST) 100% untouched.
"""

import datetime
import time
from typing import Dict, Any, Tuple, Optional, List, Set
import pandas as pd
import numpy as np

MCX_COMMODITY_KEYS: Set[str] = {
    "CRUDEOILM", "CRUDEOIL",
    "NATGASMINI", "NATURALGAS",
    "GOLDM", "GOLD",
    "SILVERM", "SILVER",
    "COPPER", "ZINC",
}

COMMODITY_FAMILY_MAP: Dict[str, str] = {
    "CRUDEOILM": "CRUDE",
    "CRUDEOIL": "CRUDE",
    "NATGASMINI": "NATGAS",
    "NATURALGAS": "NATGAS",
    "NATURALGASM": "NATGAS",
    "GOLDM": "GOLD",
    "GOLD": "GOLD",
    "SILVERM": "SILVER",
    "SILVER": "SILVER",
    "COPPER": "COPPER",
    "ZINC": "ZINC",
}

# Preferred liquid MCX contracts order (most liquid first)
MCX_LIQUIDITY_RANK: Dict[str, int] = {
    "CRUDEOIL": 1,
    "CRUDEOILM": 2,
    "NATURALGAS": 3,
    "GOLDM": 4,
    "GOLD": 5,
    "SILVERM": 6,
    "SILVER": 7,
    "COPPER": 8,
    "ZINC": 9,
    "NATGASMINI": 10,
}

# Persistent in-memory cooldown & evening trade-count tracker (by Commodity Family)
_MCX_FAMILY_COOLDOWN_UNTIL: Dict[str, float] = {}
_MCX_FAMILY_TRADES_TODAY: Dict[str, Tuple[str, int]] = {}


def get_commodity_family(asset_key_or_symbol: str) -> str:
    s = str(asset_key_or_symbol or "").upper().split()[0]
    if s in COMMODITY_FAMILY_MAP:
        return COMMODITY_FAMILY_MAP[s]
    for prefix, fam in COMMODITY_FAMILY_MAP.items():
        if s.startswith(prefix):
            return fam
    return s


def is_mcx_commodity(asset_key: str) -> bool:
    return str(asset_key or "").upper() in MCX_COMMODITY_KEYS


def record_mcx_exit(asset_key_or_symbol: str, cooldown_minutes: int = 15):
    """
    Video 4 Anti-Machine-Gun Rule:
    After ANY MCX exit (win or loss), enforce a mandatory 15-minute cooldown on that
    commodity family and increment its evening trade counter (max 2 per commodity/evening).
    """
    fam = get_commodity_family(asset_key_or_symbol)
    _MCX_FAMILY_COOLDOWN_UNTIL[fam] = time.time() + (cooldown_minutes * 60.0)
    today_s = datetime.date.today().strftime("%Y-%m-%d")
    prev_d, prev_cnt = _MCX_FAMILY_TRADES_TODAY.get(fam, (today_s, 0))
    if prev_d != today_s:
        prev_cnt = 0
    _MCX_FAMILY_TRADES_TODAY[fam] = (today_s, prev_cnt + 1)


def _resample_to_15m(df_5m: pd.DataFrame) -> pd.DataFrame:
    """Resamples 5m OHLCV DataFrame into clean 15-minute candles."""
    n = len(df_5m)
    grp = np.arange(n) // 3
    agg_dict = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in df_5m.columns:
        agg_dict["volume"] = "sum"
    if "timestamp" in df_5m.columns:
        agg_dict["timestamp"] = "first"
    return df_5m.groupby(grp).agg(agg_dict).reset_index(drop=True)


def _compute_stochastic_k(df: pd.DataFrame, length: int = 14, smooth_k: int = 2) -> pd.Series:
    """Siddharth Bhanushali Stochastic %K (length=14, smooth=2, no %D line)."""
    low_min = df["low"].rolling(window=length, min_periods=length).min()
    high_max = df["high"].rolling(window=length, min_periods=length).max()
    denom = (high_max - low_min).replace(0, np.nan)
    raw_k = ((df["close"] - low_min) / denom) * 100.0
    if smooth_k > 1:
        return raw_k.rolling(window=smooth_k, min_periods=1).mean()
    return raw_k


def _compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    """Groww Millionaire Trader ATR (Average True Range) for volatility-adjusted stops."""
    if df is None or len(df) < period + 2:
        return 0.0
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    prev_close = df["close"].astype(float).shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr_val = float(tr.rolling(window=period, min_periods=period).mean().iloc[-1])
    return round(atr_val, 2) if np.isfinite(atr_val) else 0.0


def _get_545_candle_levels(df_5m: pd.DataFrame) -> Optional[Dict[str, float]]:
    """
    Video 4 (5:45 Ka Formula):
    Finds today's 17:45 - 18:00 IST 15-minute candle from 5m bars and returns
    its 3 magical lines: High, Low, and 50% Midpoint.
    """
    if df_5m is None or "timestamp" not in df_5m.columns or len(df_5m) < 10:
        return None
    try:
        tz_ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        now_ist = datetime.datetime.now(tz_ist)
        t_start = now_ist.replace(hour=17, minute=45, second=0, microsecond=0).timestamp()
        t_end = now_ist.replace(hour=18, minute=0, second=0, microsecond=0).timestamp()
        sub = df_5m[(df_5m["timestamp"] >= t_start) & (df_5m["timestamp"] < t_end)]
        if sub.empty:
            return None
        hi = float(sub["high"].max())
        lo = float(sub["low"].min())
        if hi <= 0 or lo <= 0 or hi <= lo:
            return None
        return {"high": hi, "low": lo, "mid": round((hi + lo) / 2.0, 2)}
    except Exception:
        return None


def evaluate_commodity_entry(
    asset_key: str,
    opp_action: str,
    opp_spot: float,
    confluence_score: float,
    df_bars: pd.DataFrame,
    real_vwap: float,
    rs_trends: Dict[str, Any],
    now_hm_ist: str,
    active_positions: List[Any],
    closed_trades_today: Optional[List[Any]] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Evaluates all 5 YouTube Video MCX Commodity rules.
    Returns: (approved: bool, reason_tag: str, meta: dict)
    """
    # 1. Anti-Machine-Gun: Max 2 concurrent MCX positions & Max 1 per Commodity Family
    mcx_active = [p for p in active_positions if is_mcx_commodity(getattr(p, "asset_key", ""))]
    if len(mcx_active) >= 2:
        return False, f"{asset_key}(MCX_MAX_2_OPEN)", {}

    fam = get_commodity_family(asset_key)
    active_fams = {get_commodity_family(getattr(p, "asset_key", "") or getattr(p, "symbol", "")) for p in mcx_active}
    if fam in active_fams:
        return False, f"{asset_key}(FAM_{fam}_OPEN)", {}

    # 2. Mandatory 15-minute Family Cooldown after ANY exit (win or loss)
    cd_until = _MCX_FAMILY_COOLDOWN_UNTIL.get(fam, 0.0)
    if time.time() < cd_until:
        rem_m = int(max(1, (cd_until - time.time()) // 60 + 1))
        return False, f"{asset_key}(MCX_COOLDOWN {rem_m}m)", {}

    # 3. Video 4 Discipline: Max 2 trades per commodity family per evening
    today_s = datetime.date.today().strftime("%Y-%m-%d")
    d_rec, cnt_mem = _MCX_FAMILY_TRADES_TODAY.get(fam, (today_s, 0))
    fam_count = cnt_mem if d_rec == today_s else 0
    if closed_trades_today:
        cnt_hist = sum(
            1 for tr in closed_trades_today
            if get_commodity_family(tr.get("symbol", "")) == fam
        )
        fam_count = max(fam_count, cnt_hist)
    if fam_count >= 2:
        return False, f"{asset_key}(MCX_MAX_2_DONE)", {}

    if df_bars is None or len(df_bars) < 60:
        return False, f"{asset_key}(MCX_BARS<60)", {}

    # Build 15-minute candles for Siddharth Bhanushali 66 SMA + Stochastic(14,2) + ATR(14)
    df_15m = _resample_to_15m(df_bars)
    if len(df_15m) < 25:
        return False, f"{asset_key}(MCX_15m<25)", {}

    sma_len = min(66, len(df_15m) - 5)
    sma66 = df_15m["close"].rolling(window=sma_len, min_periods=sma_len).mean()
    sma_now = float(sma66.iloc[-1])
    sma_prev = float(sma66.iloc[-4]) if len(sma66) >= 4 else sma_now
    if not (np.isfinite(sma_now) and np.isfinite(sma_prev) and sma_prev > 0):
        return False, f"{asset_key}(MCX_SMA_NAN)", {}

    slope_pct = ((sma_now - sma_prev) / sma_prev) * 100.0
    is_sma_rising = (slope_pct >= 0.03) and (opp_spot > sma_now)
    is_sma_falling = (slope_pct <= -0.03) and (opp_spot < sma_now)

    # Reject flat sideways 66 SMA chop!
    if not (is_sma_rising or is_sma_falling):
        return False, f"{asset_key}(66SMA_FLAT {slope_pct:+.2f}%)", {}

    # Siddharth Bhanushali Trend Lock: Only CALLs on rising 66 SMA, Only PUTs on falling 66 SMA
    if opp_action == "BUY_CE" and not is_sma_rising:
        return False, f"{asset_key}(66SMA_FALLING)", {}
    if opp_action == "BUY_PE" and not is_sma_falling:
        return False, f"{asset_key}(66SMA_RISING)", {}

    # 5m current candle confirmation (must match direction)
    b_close = float(df_bars["close"].iloc[-1])
    b_open = float(df_bars["open"].iloc[-1])
    comp_close = float(df_bars["close"].iloc[-2])
    if opp_action == "BUY_CE" and b_close <= b_open:
        return False, f"{asset_key}(MCX_RED_5m)", {}
    if opp_action == "BUY_PE" and b_close >= b_open:
        return False, f"{asset_key}(MCX_GREEN_5m)", {}

    # Compute Stochastic %K (14, 2) on 15m chart (Videos 1 & 5)
    stoch_k = _compute_stochastic_k(df_15m, length=14, smooth_k=2)
    curr_k = float(stoch_k.iloc[-1]) if np.isfinite(stoch_k.iloc[-1]) else 50.0
    prev_k = float(stoch_k.iloc[-2]) if len(stoch_k) >= 2 and np.isfinite(stoch_k.iloc[-2]) else 50.0
    min_k_3 = float(stoch_k.tail(4).min()) if len(stoch_k) >= 4 else prev_k
    max_k_3 = float(stoch_k.tail(4).max()) if len(stoch_k) >= 4 else prev_k

    # Setup A: Siddharth Bhanushali 66 SMA + Stochastic(14,2) [75 / 35] Pullback Trigger
    siddharth_triggered = False
    if opp_action == "BUY_CE":
        # Dipped below 35 (oversold pullback) and crossing/turning UP above 35
        if min_k_3 <= 35.0 and curr_k > prev_k and curr_k >= 35.0:
            siddharth_triggered = True
    elif opp_action == "BUY_PE":
        # Bounced above 75 (overbought pullback) and crossing/turning DOWN below 75
        if max_k_3 >= 75.0 and curr_k < prev_k and curr_k <= 75.0:
            siddharth_triggered = True

    # Setup B: Video 4 (5:45 PM Formula) + Video 3 (Ravindra Rokde Volume Influx >= 1.35x)
    formula_545_triggered = False
    vol_ratio = 1.0
    if "volume" in df_bars.columns and len(df_bars) >= 22:
        avg_v = float(df_bars["volume"].iloc[-22:-2].mean() or 0.0)
        cur_v = float(max(df_bars["volume"].iloc[-1], df_bars["volume"].iloc[-2]))
        vol_ratio = (cur_v / avg_v) if avg_v > 0 else 1.0

    lvl_545 = _get_545_candle_levels(df_bars)
    if lvl_545 and now_hm_ist >= "18:00" and vol_ratio >= 1.35:
        if opp_action == "BUY_CE" and comp_close > lvl_545["high"] and opp_spot > lvl_545["high"]:
            # Don't chase if stretched > 1.2% past the 5:45 high
            if ((opp_spot - lvl_545["high"]) / lvl_545["high"]) <= 0.012:
                formula_545_triggered = True
        elif opp_action == "BUY_PE" and comp_close < lvl_545["low"] and opp_spot < lvl_545["low"]:
            if ((lvl_545["low"] - opp_spot) / lvl_545["low"]) <= 0.012:
                formula_545_triggered = True

    if not (siddharth_triggered or formula_545_triggered):
        if opp_action == "BUY_CE":
            return False, f"{asset_key}(WAIT_STOCH<35 K={curr_k:.0f})", {}
        return False, f"{asset_key}(WAIT_STOCH>75 K={curr_k:.0f})", {}

    # Video 2 (Groww Millionaire Trader): 1.2x 15m ATR Volatility-Adjusted Structural Stop
    atr_15m = _compute_atr(df_15m, period=14)
    if opp_action == "BUY_CE":
        swing_lo = float(df_15m["low"].tail(4).min())
        atr_sl_spot = round(swing_lo - (1.2 * atr_15m), 2) if atr_15m > 0 else round(opp_spot * 0.992, 2)
    else:
        swing_hi = float(df_15m["high"].tail(4).max())
        atr_sl_spot = round(swing_hi + (1.2 * atr_15m), 2) if atr_15m > 0 else round(opp_spot * 1.008, 2)

    tags = ["MCX_5VIDEO_ENGINE", f"66SMA_{'UP' if is_sma_rising else 'DN'}"]
    if siddharth_triggered:
        tags.append(f"SIDDHARTH_STOCH_K{curr_k:.0f}")
    if formula_545_triggered:
        tags.append(f"545_BREAKOUT_VOL{vol_ratio:.1f}x")
    tags.append(f"ATR15m_{atr_15m:.1f}")

    return True, "OK", {
        "tags": tags,
        "atr_sl_spot": atr_sl_spot,
        "atr_15m": atr_15m,
        "stoch_k": curr_k,
    }


def validate_commodity_contract(contract: Any, client: Any = None) -> Tuple[bool, str]:
    """
    Contract Quality & Liquidity Shield:
    - Ban Penny Options (< Rs.25 premium)
    - Ban Expiring Options (< 2 calendar days to expiry)
    - Verify live Dhan order-book spread (<= 2.5%) & volume (>= 50)
    """
    prem = float(getattr(contract, "estimated_premium", 0.0) or 0.0)
    if prem < 25.0:
        return False, f"MCX Penny Option Blocked (Rs.{prem:.2f} < Rs.25)"

    exp_str = str(getattr(contract, "expiry_date", "") or "")[:10]
    if exp_str:
        try:
            exp_dt = datetime.datetime.strptime(exp_str, "%Y-%m-%d").date()
            days_left = (exp_dt - datetime.date.today()).days
            if days_left < 2:
                return False, f"MCX Expiring Contract Blocked ({days_left}d left: {exp_str})"
        except Exception:
            pass

    if client is not None and getattr(client, "dhan", None) is not None:
        try:
            sid = int(getattr(contract, "security_id", 0) or 0)
            if sid > 0:
                res = client.dhan.quote_data({"MCX_COMM": [sid]})
                if isinstance(res, dict) and res.get("status") == "success":
                    d = res.get("data", {})
                    if "data" in d and isinstance(d["data"], dict):
                        d = d["data"]
                    item = d.get("MCX_COMM", {}).get(str(sid), {})
                    depth = item.get("depth", {}) if isinstance(item, dict) else {}
                    buys = depth.get("buy", []) if isinstance(depth, dict) else []
                    sells = depth.get("sell", []) if isinstance(depth, dict) else []
                    best_bid = float(buys[0].get("price", 0.0)) if buys else 0.0
                    best_ask = float(sells[0].get("price", 0.0)) if sells else 0.0
                    if best_bid > 0 and best_ask > best_bid:
                        spread_pct = ((best_ask - best_bid) / best_ask) * 100.0
                        if spread_pct > 2.5:
                            return False, f"MCX Wide Spread Blocked ({spread_pct:.1f}% > 2.5%)"
                    vol = int(item.get("volume", 0) or 0) if isinstance(item, dict) else 0
                    if isinstance(item, dict) and "volume" in item and vol < 50:
                        return False, f"MCX Illiquid Option Blocked (Vol={vol}<50)"
        except Exception:
            pass

    return True, "OK"
