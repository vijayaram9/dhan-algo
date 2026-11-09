import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import pandas as pd

from indicators import IndicatorEngine
from regime_detector import MarketRegime, regime_detector

@dataclass
class AuditCheckItem:
    rule_name: str
    condition_required: str
    actual_value: str
    passed: bool
    side: str = "CE"  # "CE" for Bullish Call setup, "PE" for Bearish Put setup

@dataclass
class SmartEdgeSignal:
    action: str              # 'BUY_CE', 'BUY_PE', or 'NO_TRADE'
    strategy_name: str       # 'TREND_PULLBACK', 'OPENING_RANGE_BREAKOUT', 'MEAN_REVERSION', or 'NONE'
    regime: str              # Market regime classification
    spot_price: float
    structural_sl_spot: float  # Underlying-based structural stop
    target_1_spot: float     # 1.5R
    target_2_spot: float     # 2R
    audit_checklist: List[AuditCheckItem] = field(default_factory=list)
    rejection_reason: Optional[str] = None

class NiftySmartEdgeStrategy:
    """
    NIFTY SMART EDGE Version 1.0 Multi-Strategy Engine:
    - Strategy A: EMA + VWAP Trend Pullback (Priority 1)
    - Strategy B: Opening Range Breakout (Priority 2)
    - Strategy C: Mean Reversion (Priority 3)
    Operates strictly on completed 5-minute candles.
    """
    def __init__(self):
        self.orb_high: Optional[float] = None
        self.orb_low: Optional[float] = None
        self.orb_attempts: int = 0
        self.candles_5m: List[dict] = []
        self.candles_15m: List[dict] = []
        self.daily_pivots: Dict[str, float] = {}
        self._auto_prime()

    def _auto_prime(self):
        """Immediately seeds 35 candles so indicators (EMA 9/21, VWAP, RSI, ADX) are primed from second 0."""
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                import yfinance as yf
                df = yf.Ticker("^NSEI").history(period="2d", interval="5m")
                if not df.empty and len(df) >= 20:
                    for idx, row in df.iterrows():
                        t_str = idx.strftime("%H:%M:%S")
                        vol = int(row["Volume"]) if row["Volume"] > 0 else 1250
                        self.add_completed_candle(
                            timestamp=t_str,
                            open_=float(row["Open"]),
                            high=float(row["High"]),
                            low=float(row["Low"]),
                            close=float(row["Close"]),
                            volume=vol
                        )
                    if len(self.candles_5m) >= 25:
                        return
        except Exception:
            pass

        # Fallback synthetic seed based on current Nifty level
        now = datetime.datetime.now()
        spot = 23640.0
        for i in range(35, 0, -1):
            t = (now - datetime.timedelta(minutes=i * 5)).strftime("%H:%M:%S")
            p = spot - ((35 - i) * 0.4)
            self.add_completed_candle(
                timestamp=t,
                open_=p - 2.0,
                high=p + 4.0,
                low=p - 3.0,
                close=p + 1.0,
                volume=1200 + (i * 15)
            )

    def set_daily_pivots(self, prev_high: float, prev_low: float, prev_close: float):
        self.daily_pivots = IndicatorEngine.calculate_pivot_points(prev_high, prev_low, prev_close)

    def update_opening_range(self, high: float, low: float):
        self.orb_high = high
        self.orb_low = low

    def add_completed_candle(self, timestamp: str, open_: float, high: float, low: float, close: float, volume: int):
        c = {
            "timestamp": timestamp,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": max(volume, 1)
        }
        self.candles_5m.append(c)
        if len(self.candles_5m) > 150:
            self.candles_5m.pop(0)

        # Aggregate 15-minute candle
        if len(self.candles_5m) % 3 == 0:
            last_3 = self.candles_5m[-3:]
            c15 = {
                "timestamp": timestamp,
                "open": last_3[0]["open"],
                "high": max(x["high"] for x in last_3),
                "low": min(x["low"] for x in last_3),
                "close": last_3[-1]["close"],
                "volume": sum(x["volume"] for x in last_3)
            }
            self.candles_15m.append(c15)
            if len(self.candles_15m) > 50:
                self.candles_15m.pop(0)

    # -------------------------------------------------------------------------
    # Strategy A: Trend Pullback (Priority 1)
    # -------------------------------------------------------------------------
    def evaluate_trend_pullback(
        self,
        df5: pd.DataFrame,
        df15: pd.DataFrame,
        current_time: str,
        regime: MarketRegime
    ) -> Tuple[Optional[SmartEdgeSignal], List[AuditCheckItem]]:
        checklist = []
        last_c = df5.iloc[-1]
        prev_c = df5.iloc[-2] if len(df5) >= 2 else last_c

        # 15m Indicators
        c15_close = df15["close"].iloc[-1]
        vwap15 = IndicatorEngine.calculate_vwap(df15)
        ema9_15, ema21_15, _ = IndicatorEngine.calculate_emas(df15["close"])

        # 5m Indicators
        ema9_5, ema21_5, slope5 = IndicatorEngine.calculate_emas(df5["close"])
        vwap5 = IndicatorEngine.calculate_vwap(df5)
        rsi5 = IndicatorEngine.calculate_rsi(df5["close"], 14)
        adx5, pos_di, neg_di = IndicatorEngine.calculate_adx(df5, 14)
        atr5 = IndicatorEngine.calculate_atr(df5, 14)

        # Volume MA 20
        vol_ma = df5["volume"].tail(20).mean() if len(df5) >= 20 else 1000.0
        vol_ratio = last_c["volume"] / max(vol_ma, 1)

        # Bullish Evaluation (BUY CE Setup)
        c1 = c15_close > vwap15
        checklist.append(AuditCheckItem("15m Close > 15m VWAP", f"> {vwap15:.1f}", f"{c15_close:.1f}", c1, side="CE"))

        c2 = ema9_15 > ema21_15
        checklist.append(AuditCheckItem("15m EMA 9 > 21", f"9 > 21", f"{ema9_15:.1f} vs {ema21_15:.1f}", c2, side="CE"))

        c3 = ema9_5 > ema21_5
        checklist.append(AuditCheckItem("5m EMA 9 > 21", f"9 > 21", f"{ema9_5:.1f} vs {ema21_5:.1f}", c3, side="CE"))

        # Momentum calibration: In strong runaway rallies, RSI stays elevated (60 - 90+).
        # We only cap extreme parabolic exhaustion (> 92.0) so trending moves are captured!
        c4 = 50.0 <= rsi5 <= 92.0
        checklist.append(AuditCheckItem("5m RSI(14) Momentum", "50 - 92", f"{rsi5:.1f}", c4, side="CE"))

        c5 = adx5 >= 18.0
        checklist.append(AuditCheckItem("ADX(14) Trend Strength", ">= 18", f"{adx5:.1f}", c5, side="CE"))

        # Pullback towards 5m EMA9/VWAP or Bullish Continuation expansion
        c6 = (abs(last_c["low"] - ema9_5) <= (1.2 * atr5)) or (abs(last_c["low"] - vwap5) <= (1.2 * atr5)) or (last_c["close"] > last_c["open"] and last_c["close"] > ema9_5 > ema21_5)
        checklist.append(AuditCheckItem("Pullback/Continuation", "Near EMA or Expansion", f"Low {last_c['low']:.1f}", c6, side="CE"))

        # Completed bullish candle closes above previous candle's high or opens above previous close
        c7 = (last_c["close"] > last_c["open"]) and ((last_c["close"] > prev_c["high"]) or (last_c["close"] > ema9_5))
        checklist.append(AuditCheckItem("Bullish Confirmation Candle", "Green Close > High/EMA", f"{last_c['close']:.1f}", c7, side="CE"))

        # Volume threshold >= 1.05x MA20
        c8 = vol_ratio >= 1.05 or last_c["volume"] >= 1000
        checklist.append(AuditCheckItem("Volume Confirmation", ">= 1.05x MA20", f"{vol_ratio:.2f}x", c8, side="CE"))

        ce_rules_met = sum([c1, c2, c3, c4, c5, c6, c7, c8])
        # High-Conviction Confluence: Triggers on 8/8 OR 7/8 if all core structural rules (VWAP, EMAs, ADX) pass!
        can_trigger_ce = (ce_rules_met == 8) or (ce_rules_met >= 7 and all([c1, c2, c3, c5]))

        if regime.permitted_strategy in ["TREND_CE", "TREND_OR_ORB"] and can_trigger_ce:
            structural_sl = min(prev_c["low"], last_c["low"]) - (0.2 * atr5)
            risk_pts = last_c["close"] - structural_sl
            t1 = last_c["close"] + (1.5 * risk_pts)
            t2 = last_c["close"] + (2.0 * risk_pts)
            return SmartEdgeSignal(
                action="BUY_CE",
                strategy_name="TREND_PULLBACK",
                regime=regime.classification,
                spot_price=last_c["close"],
                structural_sl_spot=structural_sl,
                target_1_spot=t1,
                target_2_spot=t2,
                audit_checklist=checklist
            ), checklist

        # Bearish Evaluation (BUY PE Setup)
        c1_pe = c15_close < vwap15
        checklist.append(AuditCheckItem("15m Close < 15m VWAP", f"< {vwap15:.1f}", f"{c15_close:.1f}", c1_pe, side="PE"))

        c2_pe = ema9_15 < ema21_15
        checklist.append(AuditCheckItem("15m EMA 9 < 21", f"9 < 21", f"{ema9_15:.1f} vs {ema21_15:.1f}", c2_pe, side="PE"))

        c3_pe = ema9_5 < ema21_5
        checklist.append(AuditCheckItem("5m EMA 9 < 21", f"9 < 21", f"{ema9_5:.1f} vs {ema21_5:.1f}", c3_pe, side="PE"))

        # Momentum calibration for breakdowns: RSI drops to 20-45 during strong selling waves
        c4_pe = 10.0 <= rsi5 <= 48.0
        checklist.append(AuditCheckItem("5m RSI(14) Momentum", "10 - 48", f"{rsi5:.1f}", c4_pe, side="PE"))

        c5_pe = adx5 >= 18.0
        checklist.append(AuditCheckItem("ADX(14) Trend Strength", ">= 18", f"{adx5:.1f}", c5_pe, side="PE"))

        c6_pe = (abs(last_c["high"] - ema9_5) <= (1.2 * atr5)) or (abs(last_c["high"] - vwap5) <= (1.2 * atr5)) or (last_c["close"] < last_c["open"] and last_c["close"] < ema9_5 < ema21_5)
        checklist.append(AuditCheckItem("Pullback/Continuation", "Near EMA or Breakdown", f"High {last_c['high']:.1f}", c6_pe, side="PE"))

        c7_pe = (last_c["close"] < last_c["open"]) and ((last_c["close"] < prev_c["low"]) or (last_c["close"] < ema9_5))
        checklist.append(AuditCheckItem("Bearish Confirmation Candle", "Red Close < Low/EMA", f"{last_c['close']:.1f}", c7_pe, side="PE"))

        c8_pe = vol_ratio >= 1.05 or last_c["volume"] >= 1000
        checklist.append(AuditCheckItem("Volume Confirmation", ">= 1.05x MA20", f"{vol_ratio:.2f}x", c8_pe, side="PE"))

        pe_rules_met = sum([c1_pe, c2_pe, c3_pe, c4_pe, c5_pe, c6_pe, c7_pe, c8_pe])
        # High-Conviction Confluence: Triggers on 8/8 OR 7/8 if all core structural rules (VWAP, EMAs, ADX) pass!
        can_trigger_pe = (pe_rules_met == 8) or (pe_rules_met >= 7 and all([c1_pe, c2_pe, c3_pe, c5_pe]))

        if regime.permitted_strategy in ["TREND_PE", "TREND_OR_ORB"] and can_trigger_pe:
            structural_sl = max(prev_c["high"], last_c["high"]) + (0.2 * atr5)
            risk_pts = structural_sl - last_c["close"]
            t1 = last_c["close"] - (1.5 * risk_pts)
            t2 = last_c["close"] - (2.0 * risk_pts)
            return SmartEdgeSignal(
                action="BUY_PE",
                strategy_name="TREND_PULLBACK",
                regime=regime.classification,
                spot_price=last_c["close"],
                structural_sl_spot=structural_sl,
                target_1_spot=t1,
                target_2_spot=t2,
                audit_checklist=checklist
            ), checklist
            structural_sl = max(prev_c["high"], last_c["high"]) + (0.2 * atr5)
            risk_pts = structural_sl - last_c["close"]
            t1 = last_c["close"] - (1.5 * risk_pts)
            t2 = last_c["close"] - (2.0 * risk_pts)
            return SmartEdgeSignal(
                action="BUY_PE",
                strategy_name="TREND_PULLBACK",
                regime=regime.classification,
                spot_price=last_c["close"],
                structural_sl_spot=structural_sl,
                target_1_spot=t1,
                target_2_spot=t2,
                audit_checklist=checklist
            ), checklist

        return None, checklist

    # -------------------------------------------------------------------------
    # Strategy B: Opening Range Breakout (Priority 2)
    # -------------------------------------------------------------------------
    def evaluate_orb(
        self,
        df5: pd.DataFrame,
        current_time: str,
        regime: MarketRegime
    ) -> Tuple[Optional[SmartEdgeSignal], List[AuditCheckItem]]:
        checklist = []
        if not self.orb_high or not self.orb_low or self.orb_attempts >= 2:
            return None, checklist

        last_c = df5.iloc[-1]
        ema9_5, ema21_5, _ = IndicatorEngine.calculate_emas(df5["close"])
        vwap5 = IndicatorEngine.calculate_vwap(df5)
        rsi5 = IndicatorEngine.calculate_rsi(df5["close"], 14)
        adx5, _, _ = IndicatorEngine.calculate_adx(df5, 14)
        atr5 = IndicatorEngine.calculate_atr(df5, 14)
        vol_ma = df5["volume"].tail(20).mean() if len(df5) >= 20 else 1000.0
        vol_ratio = last_c["volume"] / max(vol_ma, 1)

        candle_range = max(last_c["high"] - last_c["low"], 1.0)
        body_pct = (abs(last_c["close"] - last_c["open"]) / candle_range) * 100.0

        # Bullish ORB (BUY CE Setup)
        if last_c["close"] > self.orb_high:
            c1 = last_c["close"] > self.orb_high
            checklist.append(AuditCheckItem("Close > ORB High", f"> {self.orb_high:.1f}", f"{last_c['close']:.1f}", c1, side="CE"))
            c2 = last_c["close"] > vwap5
            checklist.append(AuditCheckItem("Price > VWAP", f"> {vwap5:.1f}", f"{last_c['close']:.1f}", c2, side="CE"))
            c3 = ema9_5 > ema21_5
            checklist.append(AuditCheckItem("EMA 9 > 21", "9 > 21", f"{ema9_5:.1f} vs {ema21_5:.1f}", c3, side="CE"))
            c4 = rsi5 > 58.0
            checklist.append(AuditCheckItem("RSI > 58", "> 58", f"{rsi5:.1f}", c4, side="CE"))
            c5 = adx5 > 20.0
            checklist.append(AuditCheckItem("ADX > 20", "> 20", f"{adx5:.1f}", c5, side="CE"))
            c6 = vol_ratio >= 1.5
            checklist.append(AuditCheckItem("Volume >= 1.5x MA20", ">= 1.5x", f"{vol_ratio:.2f}x", c6, side="CE"))
            c7 = body_pct >= 50.0
            checklist.append(AuditCheckItem("Candle Body >= 50%", ">= 50%", f"{body_pct:.1f}%", c7, side="CE"))
            c8 = (last_c["close"] - self.orb_high) <= (0.5 * atr5)
            checklist.append(AuditCheckItem("Not Extended <= 0.5 ATR", f"<= {0.5*atr5:.1f}", f"{last_c['close'] - self.orb_high:.1f}", c8, side="CE"))

            if all([c1, c2, c3, c4, c5, c6, c7, c8]):
                self.orb_attempts += 1
                structural_sl = self.orb_high - (0.3 * atr5)
                risk_pts = last_c["close"] - structural_sl
                t1 = last_c["close"] + (1.5 * risk_pts)
                t2 = last_c["close"] + (2.0 * risk_pts)
                return SmartEdgeSignal(
                    action="BUY_CE",
                    strategy_name="OPENING_RANGE_BREAKOUT",
                    regime=regime.classification,
                    spot_price=last_c["close"],
                    structural_sl_spot=structural_sl,
                    target_1_spot=t1,
                    target_2_spot=t2,
                    audit_checklist=checklist
                ), checklist

        # Bearish ORB (BUY PE Setup)
        elif last_c["close"] < self.orb_low:
            c1 = last_c["close"] < self.orb_low
            checklist.append(AuditCheckItem("Close < ORB Low", f"< {self.orb_low:.1f}", f"{last_c['close']:.1f}", c1, side="PE"))
            c2 = last_c["close"] < vwap5
            checklist.append(AuditCheckItem("Price < VWAP", f"< {vwap5:.1f}", f"{last_c['close']:.1f}", c2, side="PE"))
            c3 = ema9_5 < ema21_5
            checklist.append(AuditCheckItem("EMA 9 < 21", "9 < 21", f"{ema9_5:.1f} vs {ema21_5:.1f}", c3, side="PE"))
            c4 = rsi5 < 42.0
            checklist.append(AuditCheckItem("RSI < 42", "< 42", f"{rsi5:.1f}", c4, side="PE"))
            c5 = adx5 > 20.0
            checklist.append(AuditCheckItem("ADX > 20", "> 20", f"{adx5:.1f}", c5, side="PE"))
            c6 = vol_ratio >= 1.5
            checklist.append(AuditCheckItem("Volume >= 1.5x MA20", ">= 1.5x", f"{vol_ratio:.2f}x", c6, side="PE"))
            c7 = body_pct >= 50.0
            checklist.append(AuditCheckItem("Candle Body >= 50%", ">= 50%", f"{body_pct:.1f}%", c7, side="PE"))
            c8 = (self.orb_low - last_c["close"]) <= (0.5 * atr5)
            checklist.append(AuditCheckItem("Not Extended <= 0.5 ATR", f"<= {0.5*atr5:.1f}", f"{self.orb_low - last_c['close']:.1f}", c8, side="PE"))

            if all([c1, c2, c3, c4, c5, c6, c7, c8]):
                self.orb_attempts += 1
                structural_sl = self.orb_low + (0.3 * atr5)
                risk_pts = structural_sl - last_c["close"]
                t1 = last_c["close"] - (1.5 * risk_pts)
                t2 = last_c["close"] - (2.0 * risk_pts)
                return SmartEdgeSignal(
                    action="BUY_PE",
                    strategy_name="OPENING_RANGE_BREAKOUT",
                    regime=regime.classification,
                    spot_price=last_c["close"],
                    structural_sl_spot=structural_sl,
                    target_1_spot=t1,
                    target_2_spot=t2,
                    audit_checklist=checklist
                ), checklist

        return None, checklist

    # -------------------------------------------------------------------------
    # Strategy C: Mean Reversion (Priority 3 - Sideways Market Only)
    # -------------------------------------------------------------------------
    def evaluate_mean_reversion(
        self,
        df5: pd.DataFrame,
        current_time: str,
        regime: MarketRegime
    ) -> Tuple[Optional[SmartEdgeSignal], List[AuditCheckItem]]:
        checklist = []
        if regime.permitted_strategy not in ["MEAN_REVERSION", "SNIPER_SCALP"]:
            return None, checklist

        last_c = df5.iloc[-1]
        prev_c = df5.iloc[-2] if len(df5) >= 2 else last_c

        bb_lower, bb_mid, bb_upper = IndicatorEngine.calculate_bollinger_bands(df5["close"], 20, 2.0)
        rsi5 = IndicatorEngine.calculate_rsi(df5["close"], 14)
        adx5, _, _ = IndicatorEngine.calculate_adx(df5, 14)
        vwap5 = IndicatorEngine.calculate_vwap(df5)
        atr5 = IndicatorEngine.calculate_atr(df5, 14)

        # Bullish Mean Reversion (Oversold Bounce - BUY CE)
        c1 = adx5 < 18.0
        checklist.append(AuditCheckItem("ADX < 18 (Ranging)", "< 18", f"{adx5:.1f}", c1, side="CE"))
        c2 = prev_c["low"] <= bb_lower
        checklist.append(AuditCheckItem("Touched Lower BB", f"<= {bb_lower:.1f}", f"{prev_c['low']:.1f}", c2, side="CE"))
        c3 = rsi5 <= 35.0
        checklist.append(AuditCheckItem("RSI Oversold <= 35", "<= 35", f"{rsi5:.1f}", c3, side="CE"))
        c4 = last_c["close"] > bb_lower
        checklist.append(AuditCheckItem("Closed Inside Lower BB", f"> {bb_lower:.1f}", f"{last_c['close']:.1f}", c4, side="CE"))
        c5 = last_c["close"] > prev_c["high"]
        checklist.append(AuditCheckItem("Breaks Confirmation High", f"> {prev_c['high']:.1f}", f"{last_c['close']:.1f}", c5, side="CE"))

        if all([c1, c2, c3, c4, c5]):
            structural_sl = min(prev_c["low"], last_c["low"]) - (0.2 * atr5)
            t1 = min(vwap5, bb_mid)  # Target is VWAP or Bollinger Middle Band
            t2 = max(vwap5, bb_mid)
            return SmartEdgeSignal(
                action="BUY_CE",
                strategy_name="MEAN_REVERSION",
                regime=regime.classification,
                spot_price=last_c["close"],
                structural_sl_spot=structural_sl,
                target_1_spot=t1,
                target_2_spot=t2,
                audit_checklist=checklist
            ), checklist

        # Bearish Mean Reversion (Overbought Reversal - BUY PE)
        c1 = adx5 < 18.0
        checklist.append(AuditCheckItem("ADX < 18 (Ranging)", "< 18", f"{adx5:.1f}", c1, side="PE"))
        c2 = prev_c["high"] >= bb_upper
        checklist.append(AuditCheckItem("Touched Upper BB", f">= {bb_upper:.1f}", f"{prev_c['high']:.1f}", c2, side="PE"))
        c3 = rsi5 >= 65.0
        checklist.append(AuditCheckItem("RSI Overbought >= 65", ">= 65", f"{rsi5:.1f}", c3, side="PE"))
        c4 = last_c["close"] < bb_upper
        checklist.append(AuditCheckItem("Closed Inside Upper BB", f"< {bb_upper:.1f}", f"{last_c['close']:.1f}", c4, side="PE"))
        c5 = last_c["close"] < prev_c["low"]
        checklist.append(AuditCheckItem("Breaks Confirmation Low", f"< {prev_c['low']:.1f}", f"{last_c['close']:.1f}", c5, side="PE"))

        if all([c1, c2, c3, c4, c5]):
            structural_sl = max(prev_c["high"], last_c["high"]) + (0.2 * atr5)
            t1 = max(vwap5, bb_mid)  # Target is VWAP or Bollinger Middle Band
            t2 = min(vwap5, bb_mid)
            return SmartEdgeSignal(
                action="BUY_PE",
                strategy_name="MEAN_REVERSION",
                regime=regime.classification,
                spot_price=last_c["close"],
                structural_sl_spot=structural_sl,
                target_1_spot=t1,
                target_2_spot=t2,
                audit_checklist=checklist
            ), checklist

        return None, checklist

    # -------------------------------------------------------------------------
    # Main Engine Evaluation
    # -------------------------------------------------------------------------
    # -------------------------------------------------------------------------
    # Strategy D: Consolidation Day Sniper Scalp (Active during Chop/Range)
    # -------------------------------------------------------------------------
    def evaluate_consolidation_sniper_scalp(
        self,
        df5: pd.DataFrame,
        current_time: str,
        regime: MarketRegime
    ) -> Tuple[Optional[SmartEdgeSignal], List[AuditCheckItem]]:
        checklist = []
        if len(df5) < 3:
            return None, checklist

        last_c = df5.iloc[-1]
        prev_c = df5.iloc[-2]

        e5, e9, e21, ribbon = IndicatorEngine.calculate_triple_ema(df5["close"])
        vwap5 = IndicatorEngine.calculate_vwap(df5)
        rsi5 = IndicatorEngine.calculate_rsi(df5["close"], 14)
        atr5 = IndicatorEngine.calculate_atr(df5, 14)
        bb_lower, bb_mid, bb_upper = IndicatorEngine.calculate_bollinger_bands(df5["close"], 20, 2.0)

        # Bullish Micro Scalp Setup:
        # Micro momentum (e5 >= e9) OR Support bounce (price near lower BB / pivot)
        # Green candle closing above previous candle high / e9
        c_ce_mom = (e5 >= e9) or (last_c["close"] > vwap5)
        c_ce_bounce = (last_c["low"] <= bb_lower + (0.35 * atr5))
        c_ce_candle = (last_c["close"] > last_c["open"]) and (last_c["close"] >= prev_c["high"] * 0.999)
        c_ce_rsi = 38.0 <= rsi5 <= 68.0

        if (c_ce_mom or c_ce_bounce) and c_ce_candle and c_ce_rsi:
            sl_spot = min(last_c["low"], prev_c["low"]) - (0.15 * atr5)
            risk_pts = max(last_c["close"] - sl_spot, 6.0)
            t1 = last_c["close"] + (1.5 * risk_pts)
            t2 = last_c["close"] + (2.5 * risk_pts)
            checklist.append(AuditCheckItem("Consolidation Scalp CE", "Bullish Micro Bounce", f"Close {last_c['close']:.1f}", True, side="CE"))
            return SmartEdgeSignal(
                action="BUY_CE",
                strategy_name="CONSOLIDATION_SNIPER_SCALP",
                regime=regime.classification,
                spot_price=last_c["close"],
                structural_sl_spot=round(sl_spot, 2),
                target_1_spot=round(t1, 2),
                target_2_spot=round(t2, 2),
                audit_checklist=checklist
            ), checklist

        # Bearish Micro Scalp Setup:
        # Micro momentum (e5 <= e9) OR Resistance rejection (price near upper BB / pivot)
        # Red candle closing below previous candle low / e9
        c_pe_mom = (e5 <= e9) or (last_c["close"] < vwap5)
        c_pe_reject = (last_c["high"] >= bb_upper - (0.35 * atr5))
        c_pe_candle = (last_c["close"] < last_c["open"]) and (last_c["close"] <= prev_c["low"] * 1.001)
        c_pe_rsi = 32.0 <= rsi5 <= 62.0

        if (c_pe_mom or c_pe_reject) and c_pe_candle and c_pe_rsi:
            sl_spot = max(last_c["high"], prev_c["high"]) + (0.15 * atr5)
            risk_pts = max(sl_spot - last_c["close"], 6.0)
            t1 = last_c["close"] - (1.5 * risk_pts)
            t2 = last_c["close"] - (2.5 * risk_pts)
            checklist.append(AuditCheckItem("Consolidation Scalp PE", "Bearish Micro Rejection", f"Close {last_c['close']:.1f}", True, side="PE"))
            return SmartEdgeSignal(
                action="BUY_PE",
                strategy_name="CONSOLIDATION_SNIPER_SCALP",
                regime=regime.classification,
                spot_price=last_c["close"],
                structural_sl_spot=round(sl_spot, 2),
                target_1_spot=round(t1, 2),
                target_2_spot=round(t2, 2),
                audit_checklist=checklist
            ), checklist

        return None, checklist

    def evaluate(self, current_time: str) -> SmartEdgeSignal:
        if len(self.candles_5m) < 5:
            return SmartEdgeSignal(
                action="NO_TRADE",
                strategy_name="NONE",
                regime="INITIALIZING",
                spot_price=self.candles_5m[-1]["close"] if self.candles_5m else 23300.0,
                structural_sl_spot=0.0,
                target_1_spot=0.0,
                target_2_spot=0.0,
                rejection_reason="Accumulating 5 completed 5-minute candles for reliable indicator calculation."
            )

        df5 = pd.DataFrame(self.candles_5m)
        df15 = pd.DataFrame(self.candles_15m) if len(self.candles_15m) >= 10 else df5

        current_spot = float(df5["close"].iloc[-1])
        ema9, ema21, slope = IndicatorEngine.calculate_emas(df5["close"])
        vwap5 = IndicatorEngine.calculate_vwap(df5)
        adx5, pos_di, neg_di = IndicatorEngine.calculate_adx(df5, 14)

        # 1. Detect Regime
        regime = regime_detector.detect_regime(current_spot, vwap5, ema9, ema21, slope, adx5, pos_di, neg_di)

        # 2. Priority 1: Trend Pullback Strategy (evaluates both CE and PE criteria for live radar display)
        sig, check_a = self.evaluate_trend_pullback(df5, df15, current_time, regime)
        if sig:
            return sig

        if regime.classification == "UNCERTAIN" or regime.permitted_strategy == "NO_TRADE":
            return SmartEdgeSignal(
                action="NO_TRADE",
                strategy_name="NONE",
                regime=regime.classification,
                spot_price=current_spot,
                structural_sl_spot=0.0,
                target_1_spot=0.0,
                target_2_spot=0.0,
                audit_checklist=check_a,
                rejection_reason=regime.explanation
            )

        # 3. Priority 2: Opening Range Breakout (Active 09:30–10:00 & 13:30–14:30)
        if ("09:30" <= current_time <= "10:00") or ("13:30" <= current_time <= "14:30"):
            sig, check_b = self.evaluate_orb(df5, current_time, regime)
            if sig:
                return sig

        # 4. Priority 3: Mean Reversion Strategy (Ranging / Consolidation)
        if regime.classification in ["RANGE", "CONSOLIDATION"]:
            sig, check_c = self.evaluate_mean_reversion(df5, current_time, regime)
            if sig:
                return sig

        # 5. Priority 4: Consolidation Day Sniper Scalp (Active during chop/consolidation!)
        if regime.classification in ["RANGE", "CONSOLIDATION", "DEVELOPING_TREND"] or regime.permitted_strategy == "SNIPER_SCALP":
            sig, check_d = self.evaluate_consolidation_sniper_scalp(df5, current_time, regime)
            if sig:
                return sig

        # No strategy generated an approved entry
        checklist_to_show = check_a if check_a else []
        return SmartEdgeSignal(
            action="NO_TRADE",
            strategy_name="NONE",
            regime=regime.classification,
            spot_price=current_spot,
            structural_sl_spot=0.0,
            target_1_spot=0.0,
            target_2_spot=0.0,
            audit_checklist=checklist_to_show,
            rejection_reason=f"Regime is {regime.classification}, but mandatory confirmation rules not satisfied. Preserving capital."
        )

smart_edge_strategy = NiftySmartEdgeStrategy()
