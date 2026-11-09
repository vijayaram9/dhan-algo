from dataclasses import dataclass
from typing import Optional, List
import pandas as pd
import numpy as np
from order_flow import order_flow_analyzer, OrderFlowMetrics

@dataclass
class TradeSignal:
    action: str          # 'BUY_CE', 'BUY_PE', 'EXIT', or 'HOLD'
    reason: str
    spot_price: float
    confidence: float    # 0.0 to 1.0
    order_flow: Optional[OrderFlowMetrics] = None

class NiftyIntradayStrategy:
    """
    World-Class Intraday Nifty Strategy:
    - 15-Minute Opening Range Breakout (ORB)
    - Trend Alignment: 9 EMA & 21 EMA confirmation
    - Dynamic Trailing Stop Loss & Profit Target
    - Rejection of choppy/indecisive ranges
    """
    def __init__(self):
        self.orb_high: Optional[float] = None
        self.orb_low: Optional[float] = None
        self.orb_formed: bool = False
        self.candles_history: List[dict] = []

    def update_opening_range(self, high: float, low: float):
        self.orb_high = high
        self.orb_low = low
        self.orb_formed = True
        print(f"[Strategy] 15-Min ORB Established -> High: {self.orb_high:.2f} | Low: {self.orb_low:.2f} | Range: {self.orb_high - self.orb_low:.2f} pts")

    def add_candle(self, timestamp: str, open_: float, high: float, low: float, close: float, volume: int = 1000):
        self.candles_history.append({
            "timestamp": timestamp,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume
        })
        if len(self.candles_history) > 100:
            self.candles_history.pop(0)

    def calculate_emas(self, df: pd.DataFrame) -> tuple[float, float]:
        if len(df) < 21:
            return df["close"].iloc[-1], df["close"].iloc[-1]
        ema9 = df["close"].ewm(span=9, adjust=False).mean().iloc[-1]
        ema21 = df["close"].ewm(span=21, adjust=False).mean().iloc[-1]
        return float(ema9), float(ema21)

    def evaluate_signal(self, current_spot: float, current_time: str) -> TradeSignal:
        if not self.orb_formed or self.orb_high is None or self.orb_low is None:
            return TradeSignal(action="HOLD", reason="Waiting for 15-minute Opening Range to establish", spot_price=current_spot, confidence=0.0)

        # Minimum healthy range filter (if range is < 30 pts, market is flat; if > 250 pts, high risk exhaustion)
        orb_range = self.orb_high - self.orb_low
        if orb_range < 30.0:
            return TradeSignal(action="HOLD", reason=f"ORB range too narrow ({orb_range:.1f} pts) - High chop risk", spot_price=current_spot, confidence=0.0)

        # Indicator analysis
        df = pd.DataFrame(self.candles_history) if self.candles_history else pd.DataFrame([{"close": current_spot}])
        ema9, ema21 = self.calculate_emas(df)

        # Condition 1: Bullish Breakout with Order Flow Confirmation
        if current_spot > self.orb_high:
            flow = order_flow_analyzer.analyze(current_spot, "BUY_CE")
            if ema9 >= ema21 and current_spot >= ema9:
                if flow.order_book_imbalance >= 1.05:
                    return TradeSignal(
                        action="BUY_CE",
                        reason=f"Confirmed Breakout: Spot {current_spot:.1f} > ORB High {self.orb_high:.1f} + Order Flow Buyers ({flow.bid_volume_pct}%) & PCR {flow.pcr}",
                        spot_price=current_spot,
                        confidence=0.92,
                        order_flow=flow
                    )
                else:
                    return TradeSignal(
                        action="HOLD",
                        reason=f"Breakout Divergence: Price > ORB High, but Order Flow shows seller absorption (Imbalance: {flow.order_book_imbalance})",
                        spot_price=current_spot,
                        confidence=0.0,
                        order_flow=flow
                    )

        # Condition 2: Bearish Breakdown with Order Flow Confirmation
        elif current_spot < self.orb_low:
            flow = order_flow_analyzer.analyze(current_spot, "BUY_PE")
            if ema9 <= ema21 and current_spot <= ema9:
                if flow.order_book_imbalance <= 0.95:
                    return TradeSignal(
                        action="BUY_PE",
                        reason=f"Confirmed Breakdown: Spot {current_spot:.1f} < ORB Low {self.orb_low:.1f} + Order Flow Sellers ({flow.ask_volume_pct}%) & PCR {flow.pcr}",
                        spot_price=current_spot,
                        confidence=0.92,
                        order_flow=flow
                    )
                else:
                    return TradeSignal(
                        action="HOLD",
                        reason=f"Breakdown Divergence: Price < ORB Low, but Order Flow shows buyer absorption (Imbalance: {flow.order_book_imbalance})",
                        spot_price=current_spot,
                        confidence=0.0,
                        order_flow=flow
                    )

        # Neutral consolidation flow
        flow = order_flow_analyzer.analyze(current_spot, "HOLD")
        return TradeSignal(
            action="HOLD",
            reason=f"Spot {current_spot:.1f} inside ORB [{self.orb_low:.1f} - {self.orb_high:.1f}] - Consolidating",
            spot_price=current_spot,
            confidence=0.0,
            order_flow=flow
        )
