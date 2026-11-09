import datetime
from config import config
from notifier import notifier
from journal import journal

class RiskManager:
    """
    RMS (Risk Management System) - The core engine of professional trading.
    Prevents account blowouts, controls emotions, and enforces strict discipline.
    """
    def __init__(self):
        self.max_daily_loss = config.max_daily_loss
        self.max_daily_profit = config.max_daily_profit
        self.max_trades = config.max_trades_per_day
        
        self.realized_pnl = 0.0
        self.trade_count = 0
        self.is_kill_switch_active = False
        self.kill_switch_reason = ""
        self.last_reset_date = datetime.date.today()

        # Restore persistent state across bot restarts
        self.restore_today_state()

    def restore_today_state(self):
        try:
            summary = journal.get_today_summary()
            self.trade_count = summary["count"]
            self.realized_pnl = summary["net_pnl"]
            if self.trade_count > 0:
                print(f"[RMS] Restored persisted state for today: {self.trade_count}/{self.max_trades} trades taken | Net P&L: Rs.{self.realized_pnl:+.2f}")
                if self.trade_count >= self.max_trades:
                    self.activate_kill_switch(f"Daily limit of {self.max_trades} trades already completed earlier today.")
                elif self.realized_pnl <= -abs(self.max_daily_loss):
                    self.activate_kill_switch(f"Daily loss limit breached earlier today: Rs.{self.realized_pnl:.2f}")
                elif self.realized_pnl >= abs(self.max_daily_profit):
                    self.activate_kill_switch(f"Daily target profit locked earlier today: Rs.{self.realized_pnl:.2f}")
        except Exception as e:
            print(f"[RMS] Note: Could not restore journal state: {e}")

    def check_daily_reset(self):
        today = datetime.date.today()
        if today != self.last_reset_date:
            print("[RMS] New trading session detected. Resetting daily counters.")
            self.realized_pnl = 0.0
            self.trade_count = 0
            self.is_kill_switch_active = False
            self.kill_switch_reason = ""
            self.last_reset_date = today

    def set_max_trades(self, new_trades: int):
        """Dynamically adjusts the daily trade cap and unlocks kill switch if cap is raised."""
        self.max_trades = max(1, int(new_trades))
        if self.is_kill_switch_active and "Daily limit of" in self.kill_switch_reason and self.trade_count < self.max_trades:
            self.is_kill_switch_active = False
            self.kill_switch_reason = ""
        print(f"[RMS] Dynamic Trade Cap updated: {self.max_trades} trades (Taken today: {self.trade_count})")

    def can_take_trade(self) -> tuple[bool, str]:
        self.check_daily_reset()

        if self.is_kill_switch_active:
            return False, f"Kill switch active: {self.kill_switch_reason}"

        if self.trade_count >= self.max_trades:
            return False, f"Maximum daily trades ({self.max_trades}) reached. Preserving discipline."

        if self.realized_pnl <= -abs(self.max_daily_loss):
            self.activate_kill_switch(f"Daily loss limit reached (Rs.{self.realized_pnl:.2f} <= -Rs.{self.max_daily_loss})")
            return False, self.kill_switch_reason

        if self.realized_pnl >= abs(self.max_daily_profit):
            self.activate_kill_switch(f"Daily profit target reached (Rs.{self.realized_pnl:.2f} >= Rs.{self.max_daily_profit}). Greed lock enabled.")
            return False, self.kill_switch_reason

        return True, "RMS checks passed"

    def record_trade_exit(self, pnl: float):
        self.trade_count += 1
        self.realized_pnl += pnl
        print(f"[RMS] Trade #{self.trade_count} recorded with P&L: Rs.{pnl:+.2f} | Net Daily P&L: Rs.{self.realized_pnl:+.2f}")

        # Check thresholds immediately after trade close
        if self.realized_pnl <= -abs(self.max_daily_loss):
            self.activate_kill_switch(f"Max daily loss breached: Rs.{self.realized_pnl:.2f}")
        elif self.realized_pnl >= abs(self.max_daily_profit):
            self.activate_kill_switch(f"Target profit locked for the day: Rs.{self.realized_pnl:.2f}")

    def activate_kill_switch(self, reason: str):
        self.is_kill_switch_active = True
        self.kill_switch_reason = reason
        notifier.notify_kill_switch(reason, self.realized_pnl)
        print(f"\n=======================================================")
        print(f"[!] [RMS KILL SWITCH ACTIVATED] {reason}")
        print(f"    No further orders will be initiated today.")
        print(f"=======================================================\n")
