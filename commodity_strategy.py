"""
Dedicated MCX Commodity Options Strategy Engine (100% Isolated from Indian Equities)
===================================================================================
Designed specifically for MCX Commodities (Crude Oil, Natural Gas, Gold, Silver, Copper, Zinc).
Leaves the winning Indian Equity & Index strategy (09:25 - 15:05 IST) 100% untouched.

7 Core MCX Protections:
1. No Penny Options (< Rs.25) & Minimum 2-Day Expiry Buffer (No 0DTE/1DTE theta traps)
2. Live Liquidity & Spread Guard (Skips frozen/illiquid contracts)
3. Commodity Family Deduplication (Max 1 trade per commodity family, max 2 concurrent MCX trades)
4. Strong Trend Filter (ADX >= 22) + 15m & 1H Higher-Timeframe Alignment + VWAP Lock
5. Anti-Exhaustion "Bus Stop" Entry (Blocks chasing moves > 0.45% away from 20 EMA)
6. Commodity Breathing Stop (-16% SL with strict 1R capital cap) & +8% Moving Escalator Trail
7. US Session Prime-Time Focus (80%+ A+ only during 15:05-18:30; 70%+ during 18:30-22:45 NYMEX/COMEX)
"""

import datetime
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


def get_mcx_min_confluence(now_hm_ist: str) -> int:
    """
    Rule 7: US Session Prime-Time Focus.
    - 18:30 - 22:45 IST (NYMEX/COMEX active): 70% high-conviction threshold
    - 15:05 - 18:30 IST (thin pre-US afternoon) or morning: 80% A+ only
    """
    if "18:30" <= now_hm_ist <= "22:45":
        return 70
    return 80


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
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Evaluates all MCX-specific technical, trend, chop, family, and anti-exhaustion rules.
    Returns: (approved: bool, reason_tag: str, meta: dict)
    """
    from indicators import IndicatorEngine

    # Rule 3: Max 2 concurrent MCX positions & Max 1 per Commodity Family
    mcx_active = [p for p in active_positions if is_mcx_commodity(getattr(p, "asset_key", ""))]
    if len(mcx_active) >= 2:
        return False, f"{asset_key}(MCX_MAX_2)", {}

    target_fam = get_commodity_family(asset_key)
    active_fams = {get_commodity_family(getattr(p, "asset_key", "") or getattr(p, "symbol", "")) for p in mcx_active}
    if target_fam in active_fams:
        return False, f"{asset_key}(FAM_{target_fam}_OPEN)", {}

    # Rule 7: US Session Prime-Time Confluence Gate
    req_conf = get_mcx_min_confluence(now_hm_ist)
    if confluence_score < req_conf:
        return False, f"{asset_key}(MCX_CONF {int(confluence_score)}<{req_conf})", {}

    if df_bars is None or len(df_bars) < 30:
        return False, f"{asset_key}(MCX_BARS<30)", {}

    b_close = float(df_bars["close"].iloc[-1])
    b_open = float(df_bars["open"].iloc[-1])
    ema9 = float(df_bars["close"].ewm(span=9, adjust=False).mean().iloc[-1])
    ema20 = float(df_bars["close"].ewm(span=20, adjust=False).mean().iloc[-1])
    adx_5m, _, _ = IndicatorEngine.calculate_adx(df_bars)
    rsi_5m = IndicatorEngine.calculate_rsi(df_bars["close"])

    # Rule 4A: Strict Chop Filter (ADX >= 22 — rejects flat sideways ranges like ADX 13.7!)
    if adx_5m < 22.0:
        return False, f"{asset_key}(MCX_CHOP ADX={adx_5m:.1f}<22)", {}

    # Rule 4B: 15m + 1H Higher-Timeframe Alignment + 5m Supertrend (7, 2.5)
    st_5m = IndicatorEngine.calculate_supertrend(df_bars, period=7, multiplier=2.5)
    tf_15m_bull = bool(rs_trends.get("tf_15m_bull", False))
    tf_15m_bear = bool(rs_trends.get("tf_15m_bear", False))
    tf_1h_bull = bool(rs_trends.get("tf_1h_bull", True))
    tf_1h_bear = bool(rs_trends.get("tf_1h_bear", True))

    # Rule 5: Anti-Exhaustion "Bus Stop" Distance from 20 EMA (never chase stretched moves!)
    dist_ema20_pct = abs(opp_spot - ema20) / max(1.0, ema20) * 100.0
    if dist_ema20_pct > 0.45:
        return False, f"{asset_key}(MCX_STRETCHED {dist_ema20_pct:.2f}%>0.45%)", {}

    pb_info = IndicatorEngine.detect_pullback_breakout(df_bars, opp_action)
    is_pullback = bool(pb_info.get("triggered", False))
    # Also allow clean 9-EMA retest bounce/rejection when 5m candle confirms in trend direction
    prev_low = float(df_bars["low"].iloc[-2])
    prev_high = float(df_bars["high"].iloc[-2])

    if opp_action == "BUY_CE":
        if not (tf_15m_bull and tf_1h_bull):
            return False, f"{asset_key}(MCX_HTF!=BULL)", {}
        if st_5m.get("ready") and st_5m.get("direction") != "BULL":
            return False, f"{asset_key}(MCX_5mST!=BULL)", {}
        if real_vwap > 0 and opp_spot < real_vwap:
            return False, f"{asset_key}(MCX_<VWAP)", {}
        if not (ema9 > ema20 and b_close > ema9):
            return False, f"{asset_key}(MCX_EMA9<=20)", {}
        if b_close <= b_open:
            return False, f"{asset_key}(MCX_RED_BAR)", {}
        if rsi_5m > 74.0:
            return False, f"{asset_key}(MCX_OB RSI={rsi_5m:.0f})", {}
        near_bus_stop = is_pullback or (prev_low <= ema9 * 1.0015 and b_close > ema9)
        if not near_bus_stop:
            return False, f"{asset_key}(MCX_WAIT_BUS_STOP)", {}
    elif opp_action == "BUY_PE":
        if not (tf_15m_bear and tf_1h_bear):
            return False, f"{asset_key}(MCX_HTF!=BEAR)", {}
        if st_5m.get("ready") and st_5m.get("direction") != "BEAR":
            return False, f"{asset_key}(MCX_5mST!=BEAR)", {}
        if real_vwap > 0 and opp_spot > real_vwap:
            return False, f"{asset_key}(MCX_>VWAP)", {}
        if not (ema9 < ema20 and b_close < ema9):
            return False, f"{asset_key}(MCX_EMA9>=20)", {}
        if b_close >= b_open:
            return False, f"{asset_key}(MCX_GREEN_BAR)", {}
        if rsi_5m < 26.0:
            return False, f"{asset_key}(MCX_OS RSI={rsi_5m:.0f})", {}
        near_bus_stop = is_pullback or (prev_high >= ema9 * 0.9985 and b_close < ema9)
        if not near_bus_stop:
            return False, f"{asset_key}(MCX_WAIT_BUS_STOP)", {}
    else:
        return False, f"{asset_key}(MCX_NO_DIR)", {}

    tags = ["MCX_PRIME_ENGINE", f"ADX_{adx_5m:.0f}", "HTF_15m_1H_LOCK", "BUS_STOP_OK"]
    if is_pullback:
        tags.append(pb_info.get("tag", "EMA_PULLBACK"))
    return True, "OK", {
        "tags": tags,
        "pullback_sl": float(pb_info.get("pullback_sl", 0.0) or 0.0),
        "adx": adx_5m,
        "dist_ema20_pct": dist_ema20_pct,
    }


def validate_commodity_contract(contract: Any, client: Any = None) -> Tuple[bool, str]:
    """
    Rule 1 & Rule 2:
    - Ban Penny Options (< Rs.25 premium)
    - Ban Expiring Options (must have >= 2 calendar days to expiry)
    - Verify live non-zero Dhan quote & reject excessive spread (> 2.5%) when depth is available
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

    # Optional live bid-ask spread check via Dhan quote_data
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
