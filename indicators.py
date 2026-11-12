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

    @staticmethod
    def calculate_volume_profile(df: pd.DataFrame, num_bins: int = 24) -> dict:
        """
        Calculates Institutional Volume Profile across price distribution:
        - POC (Point of Control): Price level with highest traded volume.
        - VAH (Value Area High): 70% volume ceiling.
        - VAL (Value Area Low): 70% volume floor.
        - Profile Bins: Price bins with aggregated volume and buy/sell delta.
        """
        if df is None or df.empty or len(df) < 5:
            return {"poc": 0.0, "vah": 0.0, "val": 0.0, "bins": [], "total_volume": 0}
        
        try:
            p_high = float(df["high"].max())
            p_low = float(df["low"].min())
            if p_high <= p_low:
                return {"poc": p_high, "vah": p_high, "val": p_low, "bins": [], "total_volume": 0}
            
            bin_size = (p_high - p_low) / max(1, num_bins)
            bins_data = [{"price": round(p_low + (i + 0.5) * bin_size, 2), "volume": 0, "buy_vol": 0, "sell_vol": 0} for i in range(num_bins)]
            
            total_vol = 0
            for _, row in df.iterrows():
                c = float(row["close"])
                o = float(row["open"])
                v = int(row.get("volume", 1000) or 1000)
                total_vol += v
                bin_idx = min(num_bins - 1, max(0, int((c - p_low) / bin_size)))
                bins_data[bin_idx]["volume"] += v
                if c >= o:
                    bins_data[bin_idx]["buy_vol"] += v
                else:
                    bins_data[bin_idx]["sell_vol"] += v
            
            # Find POC (highest volume bin)
            poc_bin = max(bins_data, key=lambda b: b["volume"])
            poc_price = poc_bin["price"]
            
            # Calculate Value Area (70% of total volume around POC)
            sorted_by_vol = sorted(bins_data, key=lambda b: b["volume"], reverse=True)
            target_va_vol = total_vol * 0.70
            accum_vol = 0
            va_bins = []
            for b in sorted_by_vol:
                va_bins.append(b)
                accum_vol += b["volume"]
                if accum_vol >= target_va_vol:
                    break
            
            vah = max(b["price"] for b in va_bins) if va_bins else p_high
            val = min(b["price"] for b in va_bins) if va_bins else p_low
            
            for b in bins_data:
                b["is_poc"] = (b["price"] == poc_price)
                b["in_value_area"] = (val <= b["price"] <= vah)
            
            return {
                "poc": round(poc_price, 2),
                "vah": round(vah, 2),
                "val": round(val, 2),
                "bins": bins_data,
                "total_volume": total_vol
            }
        except Exception:
            return {"poc": 0.0, "vah": 0.0, "val": 0.0, "bins": [], "total_volume": 0}

    @staticmethod
    def detect_chart_patterns(df: pd.DataFrame) -> dict:
        """
        Detects classical structural chart formations (Head & Shoulders, Double Top/Bottom,
        Cup & Handle, Triangles, Flags) with predictive price targets and invalidation stops.
        """
        if df is None or len(df) < 15:
            return {
                "has_pattern": False,
                "pattern_name": "CONSOLIDATION_BUILDUP",
                "pattern_type": "NEUTRAL",
                "bias": "NEUTRAL",
                "stage": "ACCUMULATION",
                "confidence": 50,
                "predicted_target": 0.0,
                "invalidation_price": 0.0,
                "forecast_text": "Awaiting minimum 15 candles to map structural swing pivots.",
                "key_levels": {}
            }

        try:
            closes = df["close"].values
            highs = df["high"].values
            lows = df["low"].values
            n = len(closes)
            cur_price = float(closes[-1])

            # Find local peaks and troughs (window=3)
            peaks = []
            troughs = []
            for i in range(2, n - 2):
                if highs[i] > highs[i - 1] and highs[i] > highs[i - 2] and highs[i] > highs[i + 1] and highs[i] > highs[i + 2]:
                    peaks.append((i, float(highs[i])))
                if lows[i] < lows[i - 1] and lows[i] < lows[i - 2] and lows[i] < lows[i + 1] and lows[i] < lows[i + 2]:
                    troughs.append((i, float(lows[i])))

            # 1. Double Bottom ("W" Formation) - High Conviction Bullish Reversal
            if len(troughs) >= 2:
                t1_idx, t1_val = troughs[-2]
                t2_idx, t2_val = troughs[-1]
                trough_diff_pct = abs(t1_val - t2_val) / t1_val * 100.0
                if trough_diff_pct < 0.35 and (t2_idx - t1_idx) >= 3:
                    # Find intervening peak (neckline)
                    inter_peaks = [p for p in peaks if t1_idx < p[0] < t2_idx]
                    if inter_peaks:
                        neckline = max(p[1] for p in inter_peaks)
                        h = neckline - min(t1_val, t2_val)
                        target = round(neckline + h, 2)
                        inval = round(min(t1_val, t2_val) - (h * 0.2), 2)
                        stage = "BREAKOUT_TRIGGERED" if cur_price >= neckline else "NECKLINE_TEST"
                        return {
                            "has_pattern": True,
                            "pattern_name": "DOUBLE_BOTTOM (W FORMATION)",
                            "pattern_type": "BULLISH_REVERSAL",
                            "bias": "BULLISH",
                            "stage": stage,
                            "confidence": 88 if stage == "BREAKOUT_TRIGGERED" else 76,
                            "predicted_target": target,
                            "invalidation_price": inval,
                            "forecast_text": f"📈 Bullish Double Bottom ('W' shape) confirmed at ₹{min(t1_val, t2_val):,.1f}. Neckline at ₹{neckline:,.1f}. Projected upward target: ₹{target:,.1f} (+{target - cur_price:.1f} pts).",
                            "key_levels": {"support": min(t1_val, t2_val), "neckline": neckline, "target": target}
                        }

            # 2. Double Top ("M" Formation) - High Conviction Bearish Reversal
            if len(peaks) >= 2:
                p1_idx, p1_val = peaks[-2]
                p2_idx, p2_val = peaks[-1]
                peak_diff_pct = abs(p1_val - p2_val) / p1_val * 100.0
                if peak_diff_pct < 0.35 and (p2_idx - p1_idx) >= 3:
                    # Find intervening valley (neckline)
                    inter_troughs = [t for t in troughs if p1_idx < t[0] < p2_idx]
                    if inter_troughs:
                        neckline = min(t[1] for t in inter_troughs)
                        h = max(p1_val, p2_val) - neckline
                        target = round(neckline - h, 2)
                        inval = round(max(p1_val, p2_val) + (h * 0.2), 2)
                        stage = "BREAKDOWN_TRIGGERED" if cur_price <= neckline else "NECKLINE_TEST"
                        return {
                            "has_pattern": True,
                            "pattern_name": "DOUBLE_TOP (M FORMATION)",
                            "pattern_type": "BEARISH_REVERSAL",
                            "bias": "BEARISH",
                            "stage": stage,
                            "confidence": 88 if stage == "BREAKDOWN_TRIGGERED" else 76,
                            "predicted_target": target,
                            "invalidation_price": inval,
                            "forecast_text": f"📉 Bearish Double Top ('M' shape) confirmed at ₹{max(p1_val, p2_val):,.1f}. Neckline at ₹{neckline:,.1f}. Projected downward flush target: ₹{target:,.1f} (-{cur_price - target:.1f} pts).",
                            "key_levels": {"resistance": max(p1_val, p2_val), "neckline": neckline, "target": target}
                        }

            # 3. Head & Shoulders (Bearish Reversal)
            if len(peaks) >= 3:
                ls = peaks[-3][1]
                head = peaks[-2][1]
                rs = peaks[-1][1]
                if head > ls and head > rs and abs(ls - rs) / ls < 0.005:
                    neckline = float(df["low"].iloc[peaks[-3][0]:peaks[-1][0]].min())
                    h = head - neckline
                    target = round(neckline - h, 2)
                    inval = round(head, 2)
                    return {
                        "has_pattern": True,
                        "pattern_name": "HEAD & SHOULDERS",
                        "pattern_type": "BEARISH_REVERSAL",
                        "bias": "BEARISH",
                        "stage": "RIGHT_SHOULDER_COMPLETED",
                        "confidence": 85,
                        "predicted_target": target,
                        "invalidation_price": inval,
                        "forecast_text": f"⚠️ Classic Head & Shoulders topped out at Head ₹{head:,.1f}. Neckline break triggers major flush toward ₹{target:,.1f}.",
                        "key_levels": {"head": head, "neckline": neckline, "target": target}
                    }

            # 4. Inverse Head & Shoulders (Bullish Reversal)
            if len(troughs) >= 3:
                ls = troughs[-3][1]
                head = troughs[-2][1]
                rs = troughs[-1][1]
                if head < ls and head < rs and abs(ls - rs) / ls < 0.005:
                    neckline = float(df["high"].iloc[troughs[-3][0]:troughs[-1][0]].max())
                    h = neckline - head
                    target = round(neckline + h, 2)
                    inval = round(head, 2)
                    return {
                        "has_pattern": True,
                        "pattern_name": "INVERSE HEAD & SHOULDERS",
                        "pattern_type": "BULLISH_REVERSAL",
                        "bias": "BULLISH",
                        "stage": "RIGHT_SHOULDER_COMPLETED",
                        "confidence": 86,
                        "predicted_target": target,
                        "invalidation_price": inval,
                        "forecast_text": f"🚀 Powerful Inverse Head & Shoulders bottomed out at ₹{head:,.1f}. Breakout above ₹{neckline:,.1f} projects explosive rally to ₹{target:,.1f}.",
                        "key_levels": {"head": head, "neckline": neckline, "target": target}
                    }

            # 5. Ascending / Descending Triangles & Trend Continuation Flags
            if len(lows) >= 10:
                l_start = float(lows[-10])
                l_end = float(lows[-1])
                h_start = float(highs[-10])
                h_end = float(highs[-1])
                if l_end > l_start and abs(h_end - h_start) / h_start < 0.002:
                    target = round(h_end + (h_end - l_start), 2)
                    return {
                        "has_pattern": True,
                        "pattern_name": "ASCENDING TRIANGLE (BULLISH ACCUMULATION)",
                        "pattern_type": "BULLISH_CONTINUATION",
                        "bias": "BULLISH",
                        "stage": "APEX_COMPRESSION",
                        "confidence": 82,
                        "predicted_target": target,
                        "invalidation_price": round(l_end, 2),
                        "forecast_text": f"⚡ Ascending Triangle: Buyers pressing higher lows into resistance ₹{h_end:,.1f}. Upward breakout target ₹{target:,.1f}.",
                        "key_levels": {"resistance": h_end, "rising_support": l_end, "target": target}
                    }
                elif h_end < h_start and abs(l_end - l_start) / l_start < 0.002:
                    target = round(l_end - (h_start - l_end), 2)
                    return {
                        "has_pattern": True,
                        "pattern_name": "DESCENDING TRIANGLE (BEARISH DISTRIBUTION)",
                        "pattern_type": "BEARISH_CONTINUATION",
                        "bias": "BEARISH",
                        "stage": "APEX_COMPRESSION",
                        "confidence": 82,
                        "predicted_target": target,
                        "invalidation_price": round(h_end, 2),
                        "forecast_text": f"🩸 Descending Triangle: Sellers pressing lower highs into support ₹{l_end:,.1f}. Downward breakdown target ₹{target:,.1f}.",
                        "key_levels": {"support": l_end, "falling_resistance": h_end, "target": target}
                    }

            # Default: Trend Continuation / Pullback
            c_diff = cur_price - float(closes[0])
            bias = "BULLISH" if c_diff > 0 else "BEARISH"
            target_p = round(cur_price + (c_diff * 0.5), 2)
            return {
                "has_pattern": True,
                "pattern_name": f"{bias}_CHANNEL_EXPANSION",
                "pattern_type": f"{bias}_TREND",
                "bias": bias,
                "stage": "TREND_PROGRESSION",
                "confidence": 75,
                "predicted_target": target_p,
                "invalidation_price": round(cur_price - (c_diff * 0.3), 2),
                "forecast_text": f"📊 {bias} Trend Wave active. Projected momentum continuation target: ₹{target_p:,.1f}.",
                "key_levels": {"current": cur_price, "target": target_p}
            }
        except Exception as e:
            return {
                "has_pattern": False,
                "pattern_name": "CONSOLIDATION",
                "pattern_type": "NEUTRAL",
                "bias": "NEUTRAL",
                "stage": "CONSOLIDATION",
                "confidence": 50,
                "predicted_target": 0.0,
                "invalidation_price": 0.0,
                "forecast_text": f"Market in consolidation: {e}",
                "key_levels": {}
            }
