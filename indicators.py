import numpy as np
import pandas as pd
from typing import Dict, Tuple, List, Optional

class IndicatorEngine:
    """
    Institutional Technical & Quantitative Indicator Engine:
    - Triple EMA Ribbon (5 EMA, 9 EMA, 21 EMA)
    - RSI (14) Momentum Zones
    - VWAP (Volume Weighted Average Price)
    - ADX (14) & +DI/-DI
    - ATR (14) Volatility
    - Bollinger Bands (20, 2)
    - Central Pivot Range (CPR: TC, PP, BC) & Classical Pivots
    - Fair Value Gap (FVG: Institutional Liquidity Imbalance)
    - Candlestick Pattern Recognition (Hammer, Engulfing, Pinbar)
    """
    @staticmethod
    def calculate_triple_ema(series: pd.Series) -> Tuple[float, float, float, str]:
        """
        Returns (EMA5, EMA9, EMA21, Trend_Alignment)
        """
        if len(series) < 3:
            val = float(series.iloc[-1]) if not series.empty else 0.0
            return val, val, val, "INSUFFICIENT_DATA"

        ema5_s = series.ewm(span=min(5, len(series)), adjust=False).mean()
        ema9_s = series.ewm(span=min(9, len(series)), adjust=False).mean()
        ema21_s = series.ewm(span=min(21, max(9, len(series))), adjust=False).mean()

        e5 = float(ema5_s.iloc[-1])
        e9 = float(ema9_s.iloc[-1])
        e21 = float(ema21_s.iloc[-1])

        if e5 > e9 > e21:
            alignment = "STRONG_BULLISH_RIBBON (5 > 9 > 21 EMA)"
        elif e5 < e9 < e21:
            alignment = "STRONG_BEARISH_RIBBON (5 < 9 < 21 EMA)"
        elif e5 > e9 and e9 < e21:
            alignment = "DEVELOPING_BULLISH_CROSS (5 crossed 9 EMA)"
        elif e5 < e9 and e9 > e21:
            alignment = "DEVELOPING_BEARISH_CROSS (5 crossed below 9 EMA)"
        else:
            alignment = "NEUTRAL_CONSOLIDATION"

        return round(e5, 2), round(e9, 2), round(e21, 2), alignment

    @staticmethod
    def calculate_emas(series: pd.Series) -> Tuple[float, float, float]:
        """Legacy helper: Returns (EMA9, EMA21, EMA_Slope)"""
        if len(series) < 3:
            val = float(series.iloc[-1]) if not series.empty else 0.0
            return val, val, 0.0
        ema9_s = series.ewm(span=min(9, len(series)), adjust=False).mean()
        ema21_s = series.ewm(span=min(21, max(9, len(series))), adjust=False).mean()
        ema9 = float(ema9_s.iloc[-1])
        ema21 = float(ema21_s.iloc[-1])
        slope = float(ema9_s.iloc[-1] - ema9_s.iloc[-2]) if len(ema9_s) >= 2 else 0.0
        return ema9, ema21, slope

    @staticmethod
    def calculate_cpr(prev_high: float, prev_low: float, prev_close: float) -> dict:
        """
        Central Pivot Range (CPR):
        - Central Pivot: PP = (High + Low + Close) / 3
        - Bottom Central: BC = (High + Low) / 2
        - Top Central: TC = (PP - BC) + PP
        - Width = abs(TC - BC)
        """
        pp = (prev_high + prev_low + prev_close) / 3.0
        bc = (prev_high + prev_low) / 2.0
        tc = (pp - bc) + pp

        cpr_top = max(tc, bc)
        cpr_bottom = min(tc, bc)
        width = cpr_top - cpr_bottom
        width_pct = (width / max(1.0, pp)) * 100.0

        if width_pct < 0.20:
            classification = "NARROW CPR (High Probability Trending Breakout Day)"
        elif width_pct > 0.45:
            classification = "WIDE CPR (Range-Bound / Sideways Consolidation Day)"
        else:
            classification = "AVERAGE CPR (Normal Intraday Swings)"

        return {
            "PP": round(pp, 2),
            "TC": round(cpr_top, 2),
            "BC": round(cpr_bottom, 2),
            "width": round(width, 2),
            "width_pct": round(width_pct, 3),
            "classification": classification
        }

    @staticmethod
    def detect_fair_value_gap(df: pd.DataFrame) -> dict:
        """
        Fair Value Gap (FVG / ICT Imbalance):
        Bullish FVG: Candle 1 High < Candle 3 Low (Unfilled buy-side imbalance)
        Bearish FVG: Candle 1 Low > Candle 3 High (Unfilled sell-side imbalance)
        """
        if len(df) < 3:
            return {"type": "NONE", "active": False, "description": "Insufficient candle history."}

        c1 = df.iloc[-3]
        c2 = df.iloc[-2]
        c3 = df.iloc[-1]

        # Bullish FVG
        if c3["low"] > c1["high"]:
            gap_size = round(c3["low"] - c1["high"], 2)
            return {
                "type": "BULLISH_FVG",
                "active": True,
                "gap_top": round(c3["low"], 2),
                "gap_bottom": round(c1["high"], 2),
                "gap_size": gap_size,
                "description": f"Bullish Liquidity Imbalance of {gap_size} pts between ₹{c1['high']:.1f} and ₹{c3['low']:.1f}. Pullbacks to this zone offer institutional support."
            }

        # Bearish FVG
        if c3["high"] < c1["low"]:
            gap_size = round(c1["low"] - c3["high"], 2)
            return {
                "type": "BEARISH_FVG",
                "active": True,
                "gap_top": round(c1["low"], 2),
                "gap_bottom": round(c3["high"], 2),
                "gap_size": gap_size,
                "description": f"Bearish Liquidity Imbalance of {gap_size} pts between ₹{c3['high']:.1f} and ₹{c1['low']:.1f}. Bounces into this zone face heavy institutional selling."
            }

        return {
            "type": "BALANCED",
            "active": False,
            "description": "Market price action is balanced; no unfilled liquidity imbalances on 5m chart."
        }

    @staticmethod
    def detect_candlestick_patterns(df: pd.DataFrame) -> dict:
        """
        Identifies key high-probability Japanese candlestick price action patterns.
        """
        if len(df) < 2:
            return {"pattern": "NEUTRAL_CANDLE", "bias": "NEUTRAL", "desc": "Monitoring price action..."}

        curr = df.iloc[-1]
        prev = df.iloc[-2]

        body = abs(curr["close"] - curr["open"])
        total_range = curr["high"] - curr["low"] if curr["high"] != curr["low"] else 1.0
        upper_wick = curr["high"] - max(curr["close"], curr["open"])
        lower_wick = min(curr["close"], curr["open"]) - curr["low"]

        # 1. Hammer / Bullish Pinbar (long lower wick >= 2x body)
        if lower_wick >= 2.0 * body and upper_wick <= 0.2 * total_range:
            return {
                "pattern": "BULLISH_HAMMER_PINBAR",
                "bias": "BULLISH",
                "desc": "Bullish Hammer / Pinbar detected. Heavy rejection of lower prices by buyers."
            }

        # 2. Shooting Star / Bearish Pinbar (long upper wick >= 2x body)
        if upper_wick >= 2.0 * body and lower_wick <= 0.2 * total_range:
            return {
                "pattern": "BEARISH_SHOOTING_STAR",
                "bias": "BEARISH",
                "desc": "Shooting Star / Bearish Pinbar detected. Buyers were strongly rejected at the highs."
            }

        # 3. Bullish Engulfing
        if prev["close"] < prev["open"] and curr["close"] > curr["open"]:
            if curr["close"] >= prev["open"] and curr["open"] <= prev["close"]:
                return {
                    "pattern": "BULLISH_ENGULFING",
                    "bias": "BULLISH",
                    "desc": "Bullish Engulfing pattern. Current green candle completely engulfs previous red candle."
                }

        # 4. Bearish Engulfing
        if prev["close"] > prev["open"] and curr["close"] < curr["open"]:
            if curr["open"] >= prev["close"] and curr["close"] <= prev["open"]:
                return {
                    "pattern": "BEARISH_ENGULFING",
                    "bias": "BEARISH",
                    "desc": "Bearish Engulfing pattern. Aggressive selling overwhelmed previous buyers."
                }

        # 5. Doji / Indecision
        if body <= 0.15 * total_range:
            return {
                "pattern": "INDECISION_DOJI",
                "bias": "NEUTRAL",
                "desc": "Doji / Indecision candle. Buyers and sellers in equilibrium; wait for breakout candle."
            }

        return {
            "pattern": "CONTINUATION_BODY",
            "bias": "BULLISH" if curr["close"] > curr["open"] else "BEARISH",
            "desc": "Healthy trending candle with balanced wicks."
        }

    @staticmethod
    def calculate_rsi(series: pd.Series, period: int = 14) -> float:
        if len(series) < period + 1:
            return 50.0
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(window=period, min_periods=period).mean()
        avg_loss = loss.rolling(window=period, min_periods=period).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        val = rsi.iloc[-1]
        return 50.0 if np.isnan(val) else float(val)

    @staticmethod
    def calculate_vwap(df: pd.DataFrame) -> float:
        if df.empty or "volume" not in df.columns:
            return float(df["close"].iloc[-1])
        tp = (df["high"] + df["low"] + df["close"]) / 3.0
        pv = tp * df["volume"]
        cum_pv = pv.sum()
        cum_vol = df["volume"].sum()
        if cum_vol == 0:
            return float(df["close"].iloc[-1])
        return float(cum_pv / cum_vol)

    @staticmethod
    def calculate_atr(df: pd.DataFrame, period: int = 14) -> float:
        if len(df) < period + 1:
            return 25.0
        high = df["high"]
        low = df["low"]
        close = df["close"].shift(1)
        tr1 = high - low
        tr2 = (high - close).abs()
        tr3 = (low - close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean().iloc[-1]
        return 25.0 if np.isnan(atr) else float(atr)

    @staticmethod
    def calculate_bollinger_bands(series: pd.Series, period: int = 20, num_std: float = 2.0) -> Tuple[float, float, float]:
        if series.empty:
            return 0.0, 0.0, 0.0
        c = float(series.iloc[-1])
        if len(series) < 5:
            return c - 40.0, c, c + 40.0
        w = min(period, len(series))
        sma = series.rolling(window=w).mean()
        std = series.rolling(window=w).std()
        std_val = float(std.iloc[-1]) if not np.isnan(std.iloc[-1]) else 20.0
        sma_val = float(sma.iloc[-1]) if not np.isnan(sma.iloc[-1]) else c
        upper = sma_val + (std_val * num_std)
        lower = sma_val - (std_val * num_std)
        return float(lower), float(sma_val), float(upper)

    @staticmethod
    def calculate_adx(df: pd.DataFrame, period: int = 14) -> Tuple[float, float, float]:
        if len(df) < 5:
            return 22.0, 25.0, 20.0
        try:
            from ta.trend import ADXIndicator
            w = min(period, max(5, len(df)))
            adx_ind = ADXIndicator(high=df["high"], low=df["low"], close=df["close"], window=w)
            adx_val = adx_ind.adx().iloc[-1]
            pos_di = adx_ind.adx_pos().iloc[-1]
            neg_di = adx_ind.adx_neg().iloc[-1]
            if not np.isnan(adx_val) and not np.isnan(pos_di) and not np.isnan(neg_di):
                return float(adx_val), float(pos_di), float(neg_di)
        except Exception:
            pass
        return 22.0, 25.0, 20.0

    @staticmethod
    def calculate_pivot_points(prev_high: float, prev_low: float, prev_close: float) -> Dict[str, float]:
        pp = (prev_high + prev_low + prev_close) / 3.0
        r1 = (2 * pp) - prev_low
        s1 = (2 * pp) - prev_high
        r2 = pp + (prev_high - prev_low)
        s2 = pp - (prev_high - prev_low)
        return {
            "PP": round(pp, 2),
            "R1": round(r1, 2),
            "S1": round(s1, 2),
            "R2": round(r2, 2),
            "S2": round(s2, 2),
            "central_pivot": round(pp, 2),
            "r1": round(r1, 2),
            "s1": round(s1, 2),
            "r2": round(r2, 2),
            "s2": round(s2, 2)
        }
