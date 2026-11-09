from dataclasses import dataclass
from typing import Optional, List

@dataclass
class MarketRegime:
    classification: str     # 'STRONG_BULLISH', 'STRONG_BEARISH', 'DEVELOPING_TREND', 'RANGE', 'UNCERTAIN'
    permitted_strategy: str # 'TREND_CE', 'TREND_PE', 'TREND_OR_ORB', 'MEAN_REVERSION', 'NO_TRADE'
    adx: float
    ema9_slope: float
    above_vwap: bool
    explanation: str

class MarketRegimeDetector:
    """
    NIFTY SMART EDGE - Section 4 Market Regime Detector
    Evaluates market condition on every completed 5-minute candle.
    Authorizes which strategy is permitted to fire.
    """
    def __init__(self):
        self.vwap_crossings_count: int = 0
        self.last_cross_side: Optional[str] = None

    def record_vwap_state(self, price: float, vwap: float):
        current_side = "ABOVE" if price >= vwap else "BELOW"
        if self.last_cross_side and current_side != self.last_cross_side:
            self.vwap_crossings_count += 1
        self.last_cross_side = current_side

    def detect_regime(
        self,
        current_price: float,
        vwap: float,
        ema9: float,
        ema21: float,
        ema9_slope: float,
        adx: float,
        pos_di: float,
        neg_di: float
    ) -> MarketRegime:
        self.record_vwap_state(current_price, vwap)
        above_vwap = current_price >= vwap

        # Condition 1: Strong Bullish Trend
        # ADX above 25, EMA slope positive, above VWAP, EMA9 > EMA21
        if adx > 25.0 and ema9_slope > 0 and above_vwap and ema9 > ema21:
            return MarketRegime(
                classification="STRONG_BULLISH",
                permitted_strategy="TREND_CE",
                adx=adx,
                ema9_slope=ema9_slope,
                above_vwap=above_vwap,
                explanation=f"Strong Bullish: ADX {adx:.1f} > 25, EMA slope positive, Price > VWAP. Trend Pullback (CE) authorized."
            )

        # Condition 2: Strong Bearish Trend
        # ADX above 25, EMA slope negative, below VWAP, EMA9 < EMA21
        if adx > 25.0 and ema9_slope < 0 and (not above_vwap) and ema9 < ema21:
            return MarketRegime(
                classification="STRONG_BEARISH",
                permitted_strategy="TREND_PE",
                adx=adx,
                ema9_slope=ema9_slope,
                above_vwap=above_vwap,
                explanation=f"Strong Bearish: ADX {adx:.1f} > 25, EMA slope negative, Price < VWAP. Trend Pullback (PE) authorized."
            )

        # Condition 3: Developing Trend
        # ADX 20–25 with directional alignment
        if 20.0 < adx <= 25.0:
            if (above_vwap and ema9 > ema21 and pos_di > neg_di) or (not above_vwap and ema9 < ema21 and neg_di > pos_di):
                return MarketRegime(
                    classification="DEVELOPING_TREND",
                    permitted_strategy="TREND_OR_ORB",
                    adx=adx,
                    ema9_slope=ema9_slope,
                    above_vwap=above_vwap,
                    explanation=f"Developing Trend: ADX {adx:.1f} between 20-25 with directional DI alignment. Trend Pullback & ORB authorized."
                )

        # Condition 4: Range / Sideways Consolidation Market
        # ADX below 20 or flat EMA slope
        if adx < 20.0 or abs(ema9_slope) < 2.5:
            return MarketRegime(
                classification="CONSOLIDATION",
                permitted_strategy="SNIPER_SCALP",
                adx=adx,
                ema9_slope=ema9_slope,
                above_vwap=above_vwap,
                explanation=f"Consolidation / Range Day (ADX {adx:.1f}, slope {ema9_slope:.1f}). Sniper micro-scalps & swing reversals active on key boundaries."
            )

        # Condition 5: Mixed / Conflicting Market Structure
        # Instead of shutting down into cash, authorize Sniper Scalp setups to trade quick micro swings
        return MarketRegime(
            classification="CONSOLIDATION",
            permitted_strategy="SNIPER_SCALP",
            adx=adx,
            ema9_slope=ema9_slope,
            above_vwap=above_vwap,
            explanation=f"Consolidation Chop (ADX {adx:.1f}, slope {ema9_slope:.1f}). Sniper micro-scalps active with fast profit lock."
        )

regime_detector = MarketRegimeDetector()
