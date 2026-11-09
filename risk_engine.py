import os
import threading
from datetime import datetime
from config import config

# Simple in‑memory state for risk checks – refreshed on each call.
# For a production system you would persist daily P&L, trade count, etc.
class RiskEngine:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super(RiskEngine, cls).__new__(cls)
                    cls._instance._init_state()
        return cls._instance

    def _init_state(self):
        self.daily_loss = 0.0
        self.daily_profit = 0.0
        self.trades_taken = 0
        self.active_positions = 0
        self.last_reset = datetime.utcnow().date()

    def _reset_if_new_day(self):
        today = datetime.utcnow().date()
        if today != self.last_reset:
            self.daily_loss = 0.0
            self.daily_profit = 0.0
            self.trades_taken = 0
            self.active_positions = 0
            self.last_reset = today

    def record_trade(self, pnl: float, position_opened: bool = True):
        """Update risk metrics after a trade execution.
        * pnl – net profit/loss of the trade.
        * position_opened – True if this call is after a new position is entered, False for exit.
        """
        self._reset_if_new_day()
        if position_opened:
            self.active_positions += 1
            self.trades_taken += 1
        else:
            self.active_positions = max(0, self.active_positions - 1)
        if pnl < 0:
            self.daily_loss += abs(pnl)
        else:
            self.daily_profit += pnl

    def can_place_intent(self, strategy_name: str, intent_type: str, payload: dict) -> tuple[bool, str]:
        """Return (True, "") if the intent passes risk limits, otherwise (False, reason)."""
        self._reset_if_new_day()
        # Max concurrent positions
        if self.active_positions >= config.max_concurrent_positions:
            return False, f"Max concurrent positions ({config.max_concurrent_positions}) reached."
        # Max daily loss
        projected_loss = self.daily_loss
        if payload.get('estimated_pnl') is not None and payload['estimated_pnl'] < 0:
            projected_loss += abs(payload['estimated_pnl'])
        if projected_loss > config.max_daily_loss:
            return False, f"Projected daily loss {projected_loss:.2f} exceeds limit {config.max_daily_loss:.2f}."
        # Max trades per day
        if self.trades_taken >= config.max_trades_per_day:
            return False, f"Max trades per day ({config.max_trades_per_day}) already taken."
        return True, ""

    def summary(self):
        self._reset_if_new_day()
        return {
            "active_positions": self.active_positions,
            "trades_taken": self.trades_taken,
            "daily_loss": self.daily_loss,
            "daily_profit": self.daily_profit,
        }

# Export a singleton for easy import
risk_engine = RiskEngine()
