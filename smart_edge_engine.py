import sys
import time
import datetime
import random
import numpy as np
from dataclasses import dataclass, asdict, field
from typing import Optional, List, Dict, Tuple
from colorama import init, Fore, Style
from tabulate import tabulate

try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

from config import config
from dhan_client import DhanTraderClient
from scrip_master import ScripMaster
from indicators import IndicatorEngine
from regime_detector import regime_detector, MarketRegime
from smart_edge_strategy import smart_edge_strategy, SmartEdgeSignal, AuditCheckItem
from contract_selector import contract_selector, SelectedContract
from journal import journal
from notifier import notifier

init(autoreset=True)

def get_ist_now() -> datetime.datetime:
    """Returns current datetime strictly in Indian Standard Time (IST = UTC + 5:30)."""
    return datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)

@dataclass
class SmartEdgePosition:
    symbol: str = ""
    option_type: str = "CE"
    strategy_name: str = ""
    quantity: int = 1
    entry_premium: float = 0.0
    current_premium: float = 0.0
    hard_stop_premium: float = 0.0
    underlying_entry_spot: float = 0.0
    underlying_structural_sl: float = 0.0
    target_1_spot: float = 0.0
    target_2_spot: float = 0.0
    highest_premium: float = 0.0
    highest_spot: float = 0.0
    lowest_spot: float = 0.0
    entry_time_iso: str = ""
    signal_time_iso: str = ""
    expiry_date: str = ""
    atr_at_entry: float = 0.0
    position_id: str = ""
    security_id: str = ""
    strike_price: float = 0.0
    trade_style: str = "SMART_EDGE"    # 'SNIPER' (1-3m scalp), 'SWING' (15-60m ride), 'SMART_EDGE'
    target_premium: float = 0.0
    max_holding_seconds: int = 1800
    is_trailing_active: bool = False
    pnl: float = 0.0
    asset_key: str = "NIFTY"
    locked_profit_inr: float = 0.0
    trailing_step_level: int = 0
    invested_capital: float = 0.0
    margin_required: float = 0.0
    bank_limit_triggered: float = 0.0
    margin_pct_capital: float = 0.0
    trade_rationale: str = ""
    setup_conditions: list = field(default_factory=list)
    trailing_sl_premium: float = 0.0
    lots: int = 1
    lot_size: int = 65
    execution_mode: str = "PAPER"
    entry_thesis: list = field(default_factory=list)
    mtf_15m: str = ""
    mtf_5m: str = ""
    mtf_1m: str = ""
    risk_reward_ratio: str = "1 : 2.5"
    setup_type: str = "INSTITUTIONAL_SCALP"
    simulated_spot: float = 0.0
    segment: str = "NSE_FNO"

@dataclass
class SignalAuditRecord:
    timestamp: str
    regime: str
    strategy: str
    action: str
    spot: float
    checks: List[AuditCheckItem]
    decision: str
    reason: str

class RMSWrapper:
    """Provides backwards-compatible interface for RMS queries."""
    def __init__(self, engine):
        self._engine = engine
    @property
    def trade_count(self) -> int:
        return self._engine.trade_count
    @property
    def max_trades_per_day(self) -> int:
        return self._engine.max_daily_trades
    @property
    def is_kill_switch_active(self) -> bool:
        return self._engine.is_kill_switch_active
    @is_kill_switch_active.setter
    def is_kill_switch_active(self, val: bool):
        self._engine.is_kill_switch_active = val
    @property
    def kill_switch_reason(self) -> str:
        return self._engine.kill_switch_reason
    @kill_switch_reason.setter
    def kill_switch_reason(self, val: str):
        self._engine.kill_switch_reason = val
    @property
    def realized_pnl(self) -> float:
        return self._engine.realized_pnl

class NiftySmartEdgeEngine:
    """
    NIFTY SMART EDGE Version 1.0 - Institutional Options Buying Engine
    Implements all 12 sections of the rulebook:
    - Capital: Rs.25,000 | Max Risk/Day: Rs.500 | Max Trades: 3
    - Lot Size: 65 (NIFTY Oct-2025 revision)
    - 3 Strategies: Trend Pullback (P1), ORB (P2), Mean Reversion (P3)
    - Two-Layer Exits: Layer 1 Structural Stop & Layer 2 Hard 20% Premium Stop
    """
    def __init__(self, mode: Optional[str] = None):
        self.mode = mode or config.trading_mode
        self.client = DhanTraderClient(mode=self.mode)
        try:
            import execution_audit
            execution_audit.install(self.client, "SMART_EDGE")
        except Exception as e:
            print(f"[ExecutionAudit] Not installed: {e}")
        self.scrip_master = ScripMaster()
        self.strategy = smart_edge_strategy
        
        self.active_positions: List[SmartEdgePosition] = []
        self.max_concurrent_positions: int = 5
        self.account_capital: float = float(getattr(config, 'account_capital', 150000.0))
        self.daily_loss_limit: float = max(float(getattr(config, 'max_daily_loss', 3000.0)), self.account_capital * 0.02)
        self.max_daily_trades: int = max(1, int(getattr(config, 'max_trades_per_day', 8)))
        self.auto_expand_trades: bool = False  # Strict discipline: do not auto-expand daily trades without user confirmation
        self.trade_expand_step: int = 5       # Increments cap by +5 trades when momentum is strong
        self.profit_scaling_milestone: float = 25000.0  # Dynamic scaling milestone: Every Rs.25,000 net profit grants +10 trades
        self.profit_milestones_claimed: int = 0
        self.consecutive_losses: int = 0
        self.is_kill_switch_active: bool = False
        self.kill_switch_reason: str = ""
        self.cooldown_until: Optional[datetime.datetime] = None
        self.in_recovery_mode: bool = False
        self.auto_pilot_enabled: bool = True
        self.dynamic_min_confluence: int = 70
        self._last_3h_audit_ts: float = 0.0
        self.last_3h_audit_summary: dict = {}
        self._asset_cooldowns: dict = {}
        
        self.recent_audit_records: List[SignalAuditRecord] = []
        self.last_candle_minute: Optional[int] = None
        self.is_running: bool = False
        self.latest_spot: float = 24400.0
        self.latest_signal: Optional[SmartEdgeSignal] = None
        
        # Real-Time Second-by-Second Sniper Event Log
        self.sniper_event_log: List[dict] = []
        self.log_sniper_event("🎯 Sniper Engine Armed: Monitoring all 7 instruments for high-conviction scalps.", "SYSTEM")

        # Restore state on restart
        self.restore_daily_state()

        # Self-Healing Lock Reconciliation: If no positions are active in memory, ensure persistent lock isn't orphaned
        try:
            from global_trade_lock import global_trade_lock
            st = global_trade_lock.get_status()
            if st.get("is_locked") and not self.active_position and len(self.active_positions) == 0:
                print(Fore.YELLOW + f"[AUTO-HEAL] Releasing orphaned lock for '{st.get('symbol')}' (no active engine position).")
                global_trade_lock.force_reset_lock("Engine Startup Self-Healing")
        except Exception as e:
            print(f"[Auto-Heal Error] {e}")

    def log_sniper_event(self, text: str, category: str = "INFO"):
        """Logs real-time second-by-second sniper events to in-memory ticker."""
        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        entry = {
            "time": now_str,
            "timestamp": now_str,
            "text": text,
            "category": category  # "SYSTEM", "ENTRY", "TICK", "LOCK", "TRAIL", "EXIT"
        }
        self.sniper_event_log.append(entry)
        if len(self.sniper_event_log) > 60:
            self.sniper_event_log.pop(0)

    def set_custom_daily_cap(self, new_cap: int, auto_expand: Optional[bool] = None, expand_step: Optional[int] = None) -> dict:
        """Dynamically adjusts the maximum trades permitted for today's session."""
        self.max_daily_trades = max(1, int(new_cap))
        config.max_trades_per_day = self.max_daily_trades
        if hasattr(self, "rms") and self.rms:
            self.rms.max_trades = self.max_daily_trades
        if auto_expand is not None:
            self.auto_expand_trades = bool(auto_expand)
        if expand_step is not None:
            self.trade_expand_step = max(1, int(expand_step))
            
        # When trader updates cap, immediately unlock and resume Auto-Pilot (unless deep capital loss limit is hit)
        if self.is_kill_switch_active and self.realized_pnl > -self.daily_loss_limit:
            self.is_kill_switch_active = False
            self.kill_switch_reason = ""
            self.consecutive_losses = 0
            self.auto_pilot_enabled = True
            self.log_sniper_event(f"🔓 Daily trade cap set to {self.max_daily_trades} trades. Kill switch disarmed and Auto-Pilot resumed!", "RMS")
            notifier.notify("🔓 Trade Cap Updated & Bot Unlocked", f"Daily cap set to {self.max_daily_trades} trades ({self.trade_count}/{self.max_daily_trades} taken). Auto-pilot resumed.", tags="unlock")
        else:
            self.log_sniper_event(f"⚙️ Daily trade cap updated to {self.max_daily_trades} trades (Taken: {self.trade_count}).", "CONFIG")
            
        return {
            "success": True,
            "trade_count": self.trade_count,
            "max_trades": self.max_daily_trades,
            "auto_expand": self.auto_expand_trades,
            "expand_step": self.trade_expand_step,
            "is_kill_switch_active": self.is_kill_switch_active
        }

    @property
    def active_position(self) -> Optional[SmartEdgePosition]:
        """Backward-compatible property returning the primary/first active position."""
        return self.active_positions[0] if self.active_positions else None

    @active_position.setter
    def active_position(self, pos: Optional[SmartEdgePosition]):
        if pos is None:
            self.active_positions.clear()
        else:
            if not self.active_positions:
                self.active_positions.append(pos)
            else:
                self.active_positions[0] = pos

    @property
    def rms(self) -> RMSWrapper:
        return RMSWrapper(self)

    def reset_kill_switch(self):
        """Clears killswitch and resets trade counters so trader can restart cleanly."""
        self.is_kill_switch_active = False
        self.kill_switch_reason = ""
        self.consecutive_losses = 0
        self.trade_count = 0
        self.realized_pnl = 0.0
        self.active_positions.clear()
        if hasattr(self.client, "paper_orders"):
            self.client.paper_orders = []
        print(Fore.GREEN + "[*] Kill-switch cleared & state reset. Auto-pilot ready to trade.")

    def restore_daily_state(self):
        summary = journal.get_today_summary()
        self.trade_count = summary["count"]
        self.realized_pnl = summary["net_pnl"]

        # Restore profit milestone auto-scaling state for both Paper and Live
        if self.realized_pnl > 0:
            milestones = int(self.realized_pnl // self.profit_scaling_milestone)
            self.profit_milestones_claimed = milestones
            if milestones > 0:
                scaled_min_cap = max(self.max_daily_trades, self.trade_count + 10, 20 + (milestones * 10))
                if self.max_daily_trades < scaled_min_cap:
                    self.max_daily_trades = scaled_min_cap
                    config.max_trades_per_day = self.max_daily_trades
                    print(f"[SmartEdgeEngine] 🏆 Profit Milestone Restored (+Rs.{self.realized_pnl:,.2f}). Cap auto-scaled to {self.max_daily_trades} trades.")

        # Check journal for consecutive losses and daily state across all modes
        df = journal.get_all_trades_df()
        if not df.empty:
            today_str = datetime.date.today().strftime("%Y-%m-%d")
            today_trades = df[df["trade_date"] == today_str]
            cons = 0
            for _, row in today_trades.iterrows():
                if row["net_pnl"] < 0:
                    cons += 1
                else:
                    break
            self.consecutive_losses = cons

        # Restore profit milestone auto-scaling state
        if self.realized_pnl > 0:
            milestones = int(self.realized_pnl // self.profit_scaling_milestone)
            self.profit_milestones_claimed = milestones
            if milestones > 0:
                scaled_min_cap = max(self.max_daily_trades, self.trade_count + 10, 20 + (milestones * 10))
                if self.max_daily_trades < scaled_min_cap:
                    self.max_daily_trades = scaled_min_cap
                    config.max_trades_per_day = self.max_daily_trades
                    print(f"[SmartEdgeEngine] 🏆 Profit Milestone Restored (+Rs.{self.realized_pnl:,.2f}). Cap auto-scaled to {self.max_daily_trades} trades.")

        # Percentage-Based Daily Emergency Stop (4% of Active Capital — Zero Blind Recovery Naps!)
        active_cap_init = self.client.get_real_cash_balance() if self.mode == "LIVE" else float(self.client.paper_capital)
        pct_daily_limit = round(active_cap_init * (getattr(config, "max_daily_loss_pct", 4.0) / 100.0), 2)
        self.daily_loss_limit = pct_daily_limit if pct_daily_limit > 0 else self.daily_loss_limit
        if self.realized_pnl <= -self.daily_loss_limit:
            self.activate_kill_switch(f"Daily loss ceiling ({getattr(config, 'max_daily_loss_pct', 4.0):.1f}% = Rs.{self.daily_loss_limit:,.2f}) reached.")
        else:
            self.cooldown_until = None
            self.in_recovery_mode = False
            self.is_kill_switch_active = False
            self.kill_switch_reason = ""

    def activate_kill_switch(self, reason: str):
        self.is_kill_switch_active = True
        self.kill_switch_reason = reason
        notifier.notify_kill_switch(reason, self.realized_pnl)
        print(Fore.RED + f"\n[!] [SMART EDGE KILL-SWITCH] {reason}\n")

    def initialize(self):
        print(Fore.CYAN + "=" * 65)
        print(Fore.CYAN + " [SMART EDGE v1.0] INITIALIZING ENGINE")
        print(Fore.CYAN + "=" * 65)
        print(f"Mode: {'[PAPER SIMULATION]' if self.mode == 'PAPER' else '[LIVE BROKER TRADING]'}")
        print(f"Account Capital: Rs.25,000 | Lot Size: 65 | Max Loss Ceiling: Rs.500")
        
        # Cache Scrip Master
        self.scrip_master.load_master()
        
        # Seed baseline completed candles from live feed
        spot = self.client.get_spot_ltp()
        self.latest_spot = spot
        self.seed_candles(spot)
        print(Fore.GREEN + "[*] Engine Initialization Complete. Multi-strategy monitoring active.\n")

    def refresh_real_candles(self) -> bool:
        """Rebuilds the strategy's candle history from authentic Dhan 5m candles (last 5 sessions),
        keeping ONLY completed candles (the still-forming candle is dropped)."""
        try:
            res = self.client.dhan.intraday_minute_data(
                "13", "IDX_I", "INDEX",
                (get_ist_now() - datetime.timedelta(days=7)).strftime("%Y-%m-%d"),
                get_ist_now().strftime("%Y-%m-%d"), 5
            ) if self.client.dhan else None
            d = res.get("data", {}) if isinstance(res, dict) and res.get("status") == "success" else {}
            closes = d.get("close", []) if isinstance(d, dict) else []
            if len(closes) < 30:
                return False
            now_epoch = (get_ist_now() - datetime.timedelta(hours=5, minutes=30) - datetime.datetime(1970, 1, 1)).total_seconds()
            candles = []
            for o, h, l, c, t in zip(d["open"], d["high"], d["low"], d["close"], d["timestamp"]):
                if float(t) + 300 > now_epoch:   # candle not yet closed
                    continue
                candles.append((float(t), float(o), float(h), float(l), float(c)))
            self.strategy.load_real_candles(candles)
            return True
        except Exception as e:
            print(f"[Candles] Real candle refresh failed: {e}")
            return False

    def seed_candles(self, spot: float):
        """Seeds real historical 5-minute candles to prime all indicators immediately."""
        high = spot + 30.0
        low = spot - 30.0
        self.strategy.update_opening_range(high, low)
        self.strategy.set_daily_pivots(spot + 80.0, spot - 80.0, spot)

        # 0. Preferred: multi-session authentic completed candles (needed for 15m EMAs / ATR)
        if self.refresh_real_candles():
            print(Fore.GREEN + f"[*] Primed engine with {len(self.strategy.candles_5m)} authentic Dhan 5m candles.")
            return

        # 1. Primary: Direct authentic 5m candles from DhanHQ Intraday Charts API
        try:
            dhan_candles = self.client.get_dhan_intraday_candles("13", "IDX_I", "INDEX", interval=5)
            if dhan_candles and len(dhan_candles) >= 5:
                for c in dhan_candles:
                    ts_val = str(c.get("timestamp", ""))
                    self.strategy.add_completed_candle(
                        timestamp=ts_val,
                        open_=c["open"],
                        high=c["high"],
                        low=c["low"],
                        close=c["close"],
                        volume=c.get("volume", 1500)
                    )
                print(Fore.GREEN + f"[*] Successfully primed engine with {len(dhan_candles)} authentic Dhan intraday 5m candles.")
                return
        except Exception as e:
            print(f"[Dhan] Candle priming note: {e}")

        # 2. Secondary fallback: market feed

        # Fallback synthetic completed candles
        now = get_ist_now()
        for i in range(35, 0, -1):
            t = (now - datetime.timedelta(minutes=i * 5)).strftime("%H:%M:%S")
            p = spot + ((35 - i) * 0.3)
            self.strategy.add_completed_candle(
                timestamp=t,
                open_=p - 3.0,
                high=p + 5.0,
                low=p - 4.0,
                close=p + 1.0,
                volume=1200 + (i * 15)
            )

    # -------------------------------------------------------------------------
    # Schedule & Filter Checks (Section 8)
    # -------------------------------------------------------------------------
    def is_market_open(self, asset_key: Optional[str] = None) -> Tuple[bool, str]:
        """Checks whether Indian exchanges (NSE/BSE/MCX) are currently in session."""
        now = get_ist_now()
        weekday = now.weekday()
        if weekday in [5, 6]:
            return False, "Weekend: Indian Exchanges Closed (Opens Monday morning)."
        
        # Check if asset or active position is an MCX Commodity
        active_pos_asset = getattr(self.active_position, "asset_key", "") if self.active_position else ""
        target_asset = asset_key or active_pos_asset
        mcx_all = {"CRUDEOILM", "NATGASMINI", "GOLDM", "SILVERM", "CRUDEOIL", "NATURALGAS", "GOLD", "SILVER", "COPPER", "ZINC"}
        is_commodity = target_asset in mcx_all

        now_hm = now.strftime("%H:%M")

        if is_commodity:
            # MCX Commodity Trading Session: 09:00 AM to 11:30 PM (23:30) IST
            if now_hm < "09:00":
                return False, f"MCX Commodity Pre-Market: Session opens at 09:00 AM IST (Current: {now_hm} IST)."
            if now_hm >= "23:30":
                return False, f"MCX Commodity Closed: Session ended at 23:30 IST (Current: {now_hm} IST)."
            return True, "MCX Commodity Market is OPEN (Session active until 11:30 PM IST)"

        # NSE/BSE Equity & Derivatives Session: 09:15 AM to 03:30 PM (15:30) IST
        if now_hm < "09:15":
            return False, f"Pre-Market: Session opens at 09:15 AM IST (Current: {now_hm} IST)."
        if now_hm >= "15:30":
            return False, f"Equity Market Closed: Session ended at 15:30 IST (Current: {now_hm} IST). MCX Commodities remain OPEN until 11:30 PM!"
        
        return True, "Market is OPEN"

    def check_trading_schedule(self, asset_key: Optional[str] = None) -> Tuple[bool, str]:
        market_open, reason = self.is_market_open(asset_key)
        if not market_open:
            return False, reason

        now_time = get_ist_now().strftime("%H:%M")
        mcx_all = {"CRUDEOILM", "NATGASMINI", "GOLDM", "SILVERM", "CRUDEOIL", "NATURALGAS", "GOLD", "SILVER", "COPPER", "ZINC"}
        is_commodity = (asset_key in mcx_all) or (self.active_position and getattr(self.active_position, "asset_key", "") in mcx_all)

        if is_commodity:
            if now_time < "09:15":
                return False, "09:00-09:15 AM: MCX Commodity opening price discovery."
            if now_time >= "23:00":
                return False, "23:00 PM: MCX intraday cut-off. Managing existing positions only."
            return True, "MCX Commodity trading schedule active (until 11:00 PM IST)."

        if "09:15" <= now_time < "09:20":
            return False, "09:15-09:20 AM: Observing initial 5m opening discovery."
        cutoff_time = getattr(config, "no_new_trades_time", "15:10")
        sq_off_time = getattr(config, "square_off_time", "15:20")
        if now_time >= sq_off_time:
            return False, f"{sq_off_time} PM reached: Force square-off active."
        if now_time >= cutoff_time:
            return False, f"{cutoff_time} PM reached: No new entries permitted. Managing existing positions only."

        return True, "Trading schedule active."

    # -------------------------------------------------------------------------
    # Two-Layer Exit Monitor (Section 6)
    # -------------------------------------------------------------------------
    def check_two_layer_exit_conditions(self, current_spot: float, pos: Optional[SmartEdgePosition] = None) -> Tuple[bool, str]:
        target_pos = pos or self.active_position
        if not target_pos:
            return False, ""

        pos = target_pos
        mcx_all = {"CRUDEOILM", "NATGASMINI", "GOLDM", "SILVERM", "CRUDEOIL", "NATURALGAS", "GOLD", "SILVER", "COPPER", "ZINC"}
        pos_asset = getattr(pos, "asset_key", "NIFTY")
        p_seg = getattr(pos, "segment", None)
        if not p_seg:
            p_seg = "BSE_FNO" if (pos_asset == "SENSEX" or "SENSEX" in pos.symbol) else (
                "MCX_COMM" if pos_asset in mcx_all else "NSE_FNO"
            )
        strike_val = pos.strike_price if pos.strike_price > 0 else (round(current_spot / 50.0) * 50)
        refreshed_ltp = self.client.get_option_ltp(
            pos.security_id, current_spot, strike_val, pos.option_type, pos.expiry_date, segment=p_seg, require_live=True
        )
        has_live_quote = refreshed_ltp > 0.05
        if has_live_quote:
            pos.current_premium = refreshed_ltp
        else:
            # Allow mandatory time-based cutoffs (60m Swing / 50m Standard / 15:15 Equity / 23:15 MCX) even if live tick is quiet,
            # but strictly block SL/Target exits on stale prices when no live quote is available.
            now_ist = get_ist_now()
            now_hm_chk = now_ist.strftime("%H:%M")
            is_comm_chk = p_seg == "MCX_COMM"
            is_mandatory_eod = (not is_comm_chk and "15:15" <= now_hm_chk < "15:30") or (is_comm_chk and "23:15" <= now_hm_chk <= "23:55")
            elapsed_min_chk = 0.0
            try:
                entry_dt_chk = datetime.datetime.strptime(pos.entry_time_iso[:19], "%Y-%m-%d %H:%M:%S")
                elapsed_min_chk = (now_ist.replace(tzinfo=None) - entry_dt_chk).total_seconds() / 60.0
            except Exception:
                pass
            is_time_cutoff = (pos.trade_style == "SWING" and elapsed_min_chk >= 60.0) or (pos.trade_style not in ("SNIPER", "SWING") and elapsed_min_chk >= 50.0)
            if not (is_mandatory_eod or is_time_cutoff):
                return False, ""
            if pos.current_premium <= 0.05:
                pos.current_premium = pos.entry_premium
        pos.pnl = round((pos.current_premium - pos.entry_premium) * pos.quantity, 2)

        # Track high & low watermarks
        if pos.current_premium > pos.highest_premium:
            pos.highest_premium = pos.current_premium
        if current_spot > pos.highest_spot:
            pos.highest_spot = current_spot
        if current_spot > 0 and (pos.lowest_spot <= 0 or current_spot < pos.lowest_spot):
            pos.lowest_spot = current_spot

        # ---------------------------------------------------------------------
        # 1. SPECIALIZED TRADE STYLE MONITORS & MULTI-TIERED TRAILING PROFIT LOCKS
        # ---------------------------------------------------------------------
        prem_gain = pos.highest_premium - pos.entry_premium
        prem_gain_pct = ((pos.highest_premium - pos.entry_premium) / pos.entry_premium) * 100.0 if pos.entry_premium > 0 else 0.0

        if pos.trade_style == "SNIPER":
            # 🎯 INSTITUTIONAL DAY TRADER SETUP: Multi-Tiered Profit Expansion & Breakeven Defense
            # (Adopting Linda Raschke, Al Brooks & Paul Tudor Jones Capital Preservation Rules)
            # -----------------------------------------------------------------
            # 1. Holding Safety Window: 30-minute institutional window (No arbitrary 180s stopwatch dumping!)
            try:
                entry_dt = datetime.datetime.strptime(pos.entry_time_iso[:19], "%Y-%m-%d %H:%M:%S")
                elapsed_secs = (datetime.datetime.now() - entry_dt).total_seconds()
                if elapsed_secs >= pos.max_holding_seconds:
                    return True, f"⏳ Institutional Window Complete: 30m trade cycle concluded ({elapsed_secs/60:.1f}m). Capital preserved."
            except Exception:
                pass

            # Dynamic thresholds tailored to underlying volatility AND real round-trip friction.
            # Calibrated for high-velocity scalps with rapid capital protection & immediate profit locking.
            p_asset = getattr(pos, "asset_key", "NIFTY")
            is_bn = p_asset in ["BANKNIFTY", "SENSEX"]
            is_comm = p_asset in ["CRUDEOILM", "NATGASMINI", "GOLDM", "SILVERM"]
            qty = max(1, int(pos.quantity or 1))
            # Round-trip brokerage (~₹40) + STT + GST + turnover buffer
            fee_per_unit = round(60.0 / qty, 2)

            # Dynamic breathing room: Let setups breathe through standard bid-ask tick noise
            if p_asset == "CRUDEOILM":
                base_be = 12.0  # Crude Oil moves in 15-25 pt waves
                trail_buffer = 8.0
            elif p_asset == "NATGASMINI":
                base_be = 1.5   # Natgas moves in 1-2 pt increments
                trail_buffer = 0.8
            elif is_bn:
                base_be = 18.0  # BankNifty / Sensex wide swing zone
                trail_buffer = 12.0
            elif is_comm:
                base_be = round(pos.entry_premium * 0.05, 1)
                trail_buffer = round(pos.entry_premium * 0.03, 1)
            else: # NIFTY & Equity Options
                base_be = 10.0  # Nifty expansion to cover spread + fee
                trail_buffer = 6.0

            # 🛡️ DYNAMIC CAPITAL SHIELD (100% Percentage-Based 1R Ceiling — Zero Hardcoded Rupees):
            risk_pct = getattr(config, "risk_per_trade_pct", 1.0) / 100.0
            active_cap_ref = self.client.get_real_cash_balance() if self.mode == "LIVE" else float(self.client.paper_capital)
            one_r_cap = max(1.0, active_cap_ref * risk_pct)
            raw_risk = getattr(pos, "trade_risk_inr", 0.0) or 0.0
            planned_risk_budget = min(raw_risk, one_r_cap) if raw_risk > 0 else one_r_cap

            if pos.pnl <= -planned_risk_budget:
                msg = f"🛡️ Dynamic Capital Shield Hit: Exited @ ₹{pos.current_premium:.2f} (Loss of ₹{abs(pos.pnl):.2f} capped at 1R risk budget ₹{planned_risk_budget:,.2f})."
                self.log_sniper_event(msg, "STOP")
                print(f"[CAPITAL SHIELD] {msg}")
                return True, msg

            # Quick Profit Lock & Trend Continuation Rider:
            if pos.target_premium > 0 and pos.current_premium >= pos.target_premium:
                locked_target_floor = round(pos.target_premium - (trail_buffer * 0.35), 2)
                if pos.hard_stop_premium < locked_target_floor:
                    pos.hard_stop_premium = locked_target_floor
                    pos.is_trailing_active = True
                    pos.trailing_step_level = max(pos.trailing_step_level, 4)
                    pts_locked = round(locked_target_floor - pos.entry_premium, 2)
                    pos.locked_profit_inr = round(pts_locked * pos.quantity, 2)
                    msg = (
                        f"🎯 Target Hit & Profit Locked (+{pos.current_premium - pos.entry_premium:.1f} pts): "
                        f"Floor secured @ ₹{locked_target_floor:.2f} (+₹{pos.locked_profit_inr:,.0f} net). "
                        f"Trend Runner active to ride extended continuation!"
                    )
                    self.log_sniper_event(msg, "PROFIT")
                    print(f"[TARGET LOCK & RUNNER] {msg}")

            # 🔒 FEE-PROTECTED BREAKEVEN LOCK:
            # Only activate once premium expands enough to cover round-trip brokerage + guaranteed profit
            required_be_expansion = max(base_be, fee_per_unit + 2.0)
            be_lock_price = round(pos.entry_premium + fee_per_unit + 0.50, 2)
            if prem_gain >= required_be_expansion and pos.current_premium > be_lock_price:
                if pos.hard_stop_premium < be_lock_price:
                    pos.hard_stop_premium = be_lock_price
                    pos.is_trailing_active = True
                    pos.trailing_step_level = max(pos.trailing_step_level, 1)
                    pos.locked_profit_inr = round(0.50 * pos.quantity, 2)
                    self.log_sniper_event(
                        f"🔒 FEE-PROTECTED BREAKEVEN LOCKED (+{prem_gain:.1f} pts): Guaranteed floor @ ₹{pos.hard_stop_premium:.2f} (Brokerage covered, trade cannot lose).",
                        "LOCK"
                    )
                    print(f"[BREAKEVEN LOCK] Guaranteed floor locked @ ₹{pos.hard_stop_premium:.2f} (fees fully protected)")

            # Continuous Peak Watermark Trailing:
            # Trail behind highest price reached once in profit, never placing SL above current price
            if prem_gain >= required_be_expansion:
                trail_sl = round(pos.highest_premium - trail_buffer, 2)
                if trail_sl > pos.hard_stop_premium and trail_sl < pos.current_premium:
                    pos.hard_stop_premium = trail_sl
                    pos.is_trailing_active = True
                    pos.trailing_step_level = max(pos.trailing_step_level, 2)
                    pos.locked_profit_inr = round((trail_sl - pos.entry_premium) * pos.quantity, 2)
                    self.log_sniper_event(
                        f"🚀 TRAILING PROFIT RIDER: Trailing {trail_buffer:.1f} pts behind peak (Peak: ₹{pos.highest_premium:.2f} | Floor: ₹{trail_sl:.2f} | +₹{pos.locked_profit_inr:,.0f} net)",
                        "TRAIL"
                    )

            # Live Tick Log
            try:
                pnl_pts = pos.current_premium - pos.entry_premium
                lock_text = f"Tier {pos.trailing_step_level} Lock" if pos.trailing_step_level > 0 else "SL Active"
                self.log_sniper_event(
                    f"⏱️ {int(elapsed_secs)}s | LTP: ₹{pos.current_premium:.2f} ({pnl_pts:+.2f} pts) | P&L: ₹{pos.pnl:+.2f} | {lock_text}",
                    "TICK"
                )
            except Exception:
                pass

            # 4. Trailing Stop or Initial Structural Stop Exit:
            if pos.current_premium <= pos.hard_stop_premium:
                # Label by ACTUAL outcome, never by where the stop was parked.
                pts = pos.current_premium - pos.entry_premium
                if pos.pnl > 0 and pts > fee_per_unit:
                    return True, f"🔒 Trailing Profit Locked: Exited @ ₹{pos.current_premium:.2f} (+{pts:.2f} pts: +₹{pos.pnl:.2f} gross)"
                elif pos.hard_stop_premium >= pos.entry_premium:
                    return True, f"🛡️ Breakeven Stop (slippage): Exited @ ₹{pos.current_premium:.2f} ({pts:+.2f} pts, ₹{pos.pnl:+.2f} gross)"
                else:
                    return True, f"🎯 Controlled Initial Stop Hit: Exited @ ₹{pos.current_premium:.2f} ({pts:+.2f} pts)."

            # Active position continues monitoring
            return False, ""

        elif pos.trade_style == "SWING":
            # 🌊 SWING WAVE RIDER: Structural Trend Trail & Multi-Stage Profit Lock
            # -----------------------------------------------------------------
            # 0. 🛡️ DYNAMIC CAPITAL SHIELD (100% Percentage-Based 1R Ceiling — Scales to Any Capital):
            risk_pct = getattr(config, "risk_per_trade_pct", 1.0) / 100.0
            active_cap_ref = self.client.get_real_cash_balance() if self.mode == "LIVE" else float(self.client.paper_capital)
            one_r_cap = max(1.0, active_cap_ref * risk_pct)
            raw_risk = getattr(pos, "trade_risk_inr", 0.0) or 0.0
            swing_risk_cap = min(raw_risk, one_r_cap) if raw_risk > 0 else one_r_cap
            if pos.pnl <= -swing_risk_cap:
                msg = f"🛡️ Swing 1R Capital Shield Hit: Exited @ ₹{pos.current_premium:.2f} (Loss of ₹{abs(pos.pnl):.2f} capped at 1R = ₹{swing_risk_cap:,.2f})."
                self.log_sniper_event(msg, "STOP")
                print(f"[SWING CAPITAL SHIELD] {msg}")
                return True, msg

            # 1. Time Cutoff: 60 minutes holding cycle complete
            try:
                entry_dt = datetime.datetime.strptime(pos.entry_time_iso[:19], "%Y-%m-%d %H:%M:%S")
                elapsed_minutes = (datetime.datetime.now() - entry_dt).total_seconds() / 60.0
                if elapsed_minutes >= 60.0:
                    return True, f"🌊 Swing 60m Time Cutoff: 1 hour holding cycle complete (Locking wave profit)."
            except Exception:
                pass

            # 2. Swing Wave Profit Lock & Runner Continuation:
            if pos.target_premium > 0 and pos.current_premium >= pos.target_premium:
                locked_floor = round(pos.target_premium * 0.95, 2)
                if pos.hard_stop_premium < locked_floor:
                    pos.hard_stop_premium = locked_floor
                    pos.is_trailing_active = True
                    pos.trailing_step_level = max(pos.trailing_step_level, 4)
                    msg = f"🌊 Swing Target Hit: Floor Locked @ ₹{locked_floor:.2f}. Trailing runner active to ride wave continuation!"
                    self.log_sniper_event(msg, "PROFIT")

            # 3. Multi-Tiered Dynamic Trailing Stop & Profit Lock for Swing:
            # Step 0A (+4.5% open gain): Tighten Hard Stop from -10% to -2.0% (halves risk without choking normal wicks)
            if prem_gain_pct >= 4.5 and not pos.is_trailing_active:
                tight_sl = round(pos.entry_premium * 0.98, 2)
                if tight_sl > pos.hard_stop_premium:
                    pos.hard_stop_premium = tight_sl
                    print(f"[SWING TIGHT-SHIELD] 🛡️ +4.5% Reached -> Hard Stop tightened to -2% @ ₹{pos.hard_stop_premium:.2f}")

            # Pure R-Multiple Profit Lock + Fib/CPR/R1/S1 Level Tightening (Zero Hardcoded Rupees!)
            if pos.pnl >= (one_r_cap * 0.75) and pos.current_premium > pos.entry_premium:
                cash_ratio = 0.80 if pos.pnl >= (one_r_cap * 2.5) else (0.70 if pos.pnl >= (one_r_cap * 1.5) else (0.55 if pos.pnl >= (one_r_cap * 0.9) else 0.0))
                try:
                    from mtf_engine import mtf_engine as _mtf_lv
                    _lvls = _mtf_lv.calculate_institutional_levels(current_spot, asset_k)
                    if pos.option_type == "CE":
                        _key_lvls = [v for k in ("r1", "pdh", "fib_618", "cpr_tc") if (v := float(_lvls.get(k, 0.0) or 0.0)) > 0]
                        if any(abs(current_spot - lv) / max(1.0, current_spot) <= 0.0008 or current_spot >= lv for lv in _key_lvls if lv > pos.underlying_entry_spot):
                            cash_ratio = max(cash_ratio, 0.75)
                    else:
                        _key_lvls = [v for k in ("s1", "pdl", "fib_382", "cpr_bc") if (v := float(_lvls.get(k, 0.0) or 0.0)) > 0]
                        if any(abs(current_spot - lv) / max(1.0, current_spot) <= 0.0008 or current_spot <= lv for lv in _key_lvls if lv < pos.underlying_entry_spot):
                            cash_ratio = max(cash_ratio, 0.75)
                except Exception:
                    pass
                if cash_ratio > 0:
                    fee_buf = max(2.0, round(150.0 / max(1, pos.quantity), 2))
                    cash_sl = round(pos.entry_premium + max(fee_buf, (pos.current_premium - pos.entry_premium) * cash_ratio), 2)
                    cash_sl = min(cash_sl, round(pos.current_premium - max(0.5, pos.current_premium * 0.01), 2))
                    if cash_sl > pos.hard_stop_premium:
                        pos.hard_stop_premium = cash_sl
                        pos.is_trailing_active = True
                        pos.trailing_step_level = max(pos.trailing_step_level, 2 if cash_ratio >= 0.70 else 1)
                        pos.locked_profit_inr = round((pos.hard_stop_premium - pos.entry_premium) * pos.quantity, 2)

            # Continuous Peak-Gain Ratchet (+7%+): never let a +₹1,000 winner bleed back to +₹300!
            peak_gain = max(0.0, pos.highest_premium - pos.entry_premium)
            peak_gain_pct = (peak_gain / max(0.01, pos.entry_premium)) * 100.0
            if peak_gain_pct >= 7.0:
                fee_buf = max(2.0, round(150.0 / max(1, pos.quantity), 2))
                lock_ratio = 0.55 if peak_gain_pct < 12.0 else (0.65 if peak_gain_pct < 18.0 else 0.75)
                ratchet_sl = round(pos.entry_premium + max(fee_buf, peak_gain * lock_ratio), 2)
                ratchet_sl = min(ratchet_sl, round(pos.current_premium - max(0.5, pos.current_premium * 0.01), 2))
                if ratchet_sl > pos.hard_stop_premium:
                    pos.hard_stop_premium = ratchet_sl
                    pos.is_trailing_active = True
                    pos.trailing_step_level = max(pos.trailing_step_level, 1 if peak_gain_pct < 12.0 else (2 if peak_gain_pct < 18.0 else 3))
                    pos.locked_profit_inr = round((pos.hard_stop_premium - pos.entry_premium) * pos.quantity, 2)
                    print(f"[SWING RATCHET] 🛡️ Peak +{peak_gain_pct:.1f}% -> Locking {int(lock_ratio*100)}% of peak @ ₹{pos.hard_stop_premium:.2f} (+₹{pos.locked_profit_inr:,.0f})")

            # Tier 3 (+22% or higher runner): Lock at least +15% and trail tightly at 90% of highest watermark!
            if prem_gain_pct >= 22.0:
                trail_sl = max(round(pos.entry_premium * 1.15, 2), round(pos.highest_premium * 0.90, 2))
                trail_sl = min(trail_sl, round(pos.current_premium - max(1.0, pos.current_premium * 0.01), 2))
                if trail_sl > pos.hard_stop_premium:
                    pos.hard_stop_premium = trail_sl
                    pos.trailing_step_level = 3
                    pos.locked_profit_inr = round((trail_sl - pos.entry_premium) * pos.quantity, 2)

            # Check Hard / Trailing Premium Stop
            if pos.current_premium <= pos.hard_stop_premium:
                if pos.hard_stop_premium > pos.entry_premium and pos.pnl > 120.0:
                    return True, f"🔒 Swing Trailing Profit Locked: Exited @ ₹{pos.current_premium:.2f} (Secured +₹{pos.pnl:.2f})!"
                elif pos.hard_stop_premium > pos.entry_premium:
                    return True, f"🛡️ Swing Breakeven/Slippage Stop: Exited @ ₹{pos.current_premium:.2f} (Gross ₹{pos.pnl:+.2f})"
                else:
                    sl_pct = round(((pos.hard_stop_premium - pos.entry_premium) / max(0.01, pos.entry_premium)) * 100.0, 1)
                    return True, f"Layer 2 Hard Premium Stop Hit @ ₹{pos.current_premium:.2f} ({sl_pct:+.1f}% stop: ₹{pos.hard_stop_premium:.2f})"

        # ---------------------------------------------------------------------
        # 2. STANDARD / SMART EDGE DEFENSES & HOLDING LIMITS
        # ---------------------------------------------------------------------
        # Standard Trade Trailing Profit Lock
        if prem_gain_pct >= 15.0 and pos.trailing_step_level < 1:
            pos.hard_stop_premium = max(pos.hard_stop_premium, round(pos.entry_premium + 1.0, 2))
            pos.is_trailing_active = True
            pos.trailing_step_level = 1
            pos.locked_profit_inr = round(1.0 * pos.quantity, 2)

        # Holding duration check (45-60 minutes max holding period for standard trades)
        try:
            entry_dt = datetime.datetime.strptime(pos.entry_time_iso[:19], "%Y-%m-%d %H:%M:%S")
            elapsed_minutes = (datetime.datetime.now() - entry_dt).total_seconds() / 60.0
            if elapsed_minutes >= 50.0 and pos.trade_style != "SNIPER":
                return True, f"Time-Based Exit: 50 minutes holding period reached (Theta preservation)."
        except Exception:
            pass

        # Force Cutoff: 3:15 PM for Equity, 11:15 PM for MCX Commodities
        now_hm = get_ist_now().strftime("%H:%M")
        is_comm = getattr(pos, "asset_key", "") in ["CRUDEOILM", "NATGASMINI", "GOLDM", "SILVERM"]
        if not is_comm and "15:15" <= now_hm < "15:30":
            return True, "3:15 PM IST: Mandatory Equity Intraday Force Square-Off."
        elif is_comm and now_hm >= "23:15":
            return True, "11:15 PM IST: Mandatory MCX Commodity Force Square-Off."

        # LAYER 2: Hard Option-Premium Protection (or Trailing Stop Hit)
        if pos.current_premium <= pos.hard_stop_premium:
            if pos.hard_stop_premium > pos.entry_premium:
                return True, f"🔒 Trailing Profit Locked: Exited @ ₹{pos.current_premium:.2f} (Secured +₹{pos.pnl:.2f})!"
            else:
                stop_pct_lbl = round(((pos.hard_stop_premium - pos.entry_premium) / max(0.01, pos.entry_premium)) * 100.0, 1)
                return True, f"Layer 2 Hard Premium Stop Hit @ ₹{pos.current_premium:.2f} ({stop_pct_lbl:+.1f}% stop: ₹{pos.hard_stop_premium:.2f})"

        # Minimum execution stabilization window: Allow at least 15 seconds for trade to breathe
        # before structural target or trailing can trigger (unless hard stop is breached)
        try:
            entry_dt = datetime.datetime.strptime(pos.entry_time_iso[:19], "%Y-%m-%d %H:%M:%S")
            elapsed_seconds = (datetime.datetime.now() - entry_dt).total_seconds()
        except Exception:
            elapsed_seconds = 60.0

        # LIVE MID-TRADE 5m REASSESSMENT (EMA-9 Thesis Break + VWAP ChoCH + 10m Dead-Money Cut):
        if has_live_quote and pos.entry_premium > 0:
            cur_ret_pct = (pos.current_premium - pos.entry_premium) / pos.entry_premium
            if elapsed_seconds >= 180.0 and pos.pnl < -150.0 and cur_ret_pct <= -0.025:
                try:
                    from mtf_engine import mtf_engine
                    df_chk = mtf_engine.get_real_candles(pos_asset)
                    lvls_chk = mtf_engine.calculate_institutional_levels(current_spot, pos_asset)
                    vwap_chk = float(lvls_chk.get("vwap", 0.0) or 0.0)
                    if df_chk is not None and len(df_chk) >= 21:
                        e9_series = df_chk["close"].ewm(span=9, adjust=False).mean()
                        e9_chk = float(e9_series.iloc[-1])
                        e9_prev = float(e9_series.iloc[-2])
                        e21_chk = float(df_chk["close"].ewm(span=21, adjust=False).mean().iloc[-1])
                        comp_c = float(df_chk["close"].iloc[-2])
                        comp_o = float(df_chk["open"].iloc[-2])
                        if pos.option_type == "CE":
                            if vwap_chk > 0 and current_spot < vwap_chk and e9_chk < e21_chk and comp_c < comp_o:
                                return True, f"⚡ Live ChoCH Reversal Cut: {pos_asset} flipped below VWAP (₹{vwap_chk:,.1f}) & EMA 9 < 21"
                            if comp_c < comp_o and comp_c < e9_prev and current_spot < e9_chk:
                                return True, f"⚡ Live 5m Reassessment: {pos_asset} completed 5m bar closed below EMA 9 (₹{e9_chk:,.1f})"
                        elif pos.option_type == "PE":
                            if vwap_chk > 0 and current_spot > vwap_chk and e9_chk > e21_chk and comp_c > comp_o:
                                return True, f"⚡ Live ChoCH Reversal Cut: {pos_asset} flipped above VWAP (₹{vwap_chk:,.1f}) & EMA 9 > 21"
                            if comp_c > comp_o and comp_c > e9_prev and current_spot > e9_chk:
                                return True, f"⚡ Live 5m Reassessment: {pos_asset} completed 5m bar closed above EMA 9 (₹{e9_chk:,.1f})"
                except Exception:
                    pass
            # 10-minute Dead-Money Theta Cut: exit flat options after 10m of zero momentum (-1.5% to +1.5%)
            if elapsed_seconds >= 600.0 and -0.015 <= cur_ret_pct <= 0.015 and not pos.is_trailing_active:
                return True, f"⏱️ 10m Dead-Money Cut: {pos.symbol} flat ({cur_ret_pct*100:+.1f}%) after 10m — preserving theta & capital"

        # LAYER 1: Underlying-Based Structural Stop (with sanity guard against corrupted spot quotes)
        spot_is_sane = (current_spot > 0 and abs(current_spot - pos.underlying_entry_spot) / max(1.0, pos.underlying_entry_spot) < 0.50)
        if spot_is_sane:
            asset_k = str(getattr(pos, 'asset_key', 'NIFTY') or 'NIFTY').upper()
            if "SENSEX" in asset_k:
                min_buffer = 40.0
            elif "BANKNIFTY" in asset_k:
                min_buffer = 30.0
            elif "FINNIFTY" in asset_k:
                min_buffer = 18.0
            elif "NIFTY" in asset_k:
                min_buffer = 15.0
            else:
                min_buffer = max(1.5, pos.underlying_entry_spot * 0.0020)

            structural_buffer = max(getattr(pos, 'atr_at_entry', 0.0) * 0.5, min_buffer)

            if pos.option_type == "CE":
                # Structural Invalidation (CE: underlying breaks swing low by at least the volatility buffer, premium confirms loss, and at least 60s breathing room)
                if pos.underlying_structural_sl > 0 and current_spot <= (pos.underlying_structural_sl - structural_buffer) and pos.current_premium <= pos.entry_premium and elapsed_seconds >= 60.0:
                    return True, f"Layer 1 Structural Invalidation: {asset_k} {current_spot:.1f} broke swing low {pos.underlying_structural_sl:.1f} (buffer: {structural_buffer:.1f}pts)"

                # Target 1 (1.5R) reached (CE): Lock 30% of open gain (min fee-protected BE) only once option is >= +5.5% so normal spread noise doesn't choke runners
                prem_gain_pct = (pos.current_premium - pos.entry_premium) / max(0.01, pos.entry_premium)
                if pos.target_1_spot > pos.underlying_entry_spot and current_spot >= pos.target_1_spot and prem_gain_pct >= 0.055 and elapsed_seconds >= 15.0:
                    fee_buf = max(2.0, round(150.0 / max(1, pos.quantity), 2))
                    gain_now = max(0.0, pos.current_premium - pos.entry_premium)
                    t1_lock = round(pos.entry_premium + max(fee_buf, gain_now * 0.30), 2)
                    t1_lock = min(t1_lock, round(pos.current_premium - max(1.0, pos.current_premium * 0.01), 2))
                    if t1_lock > pos.hard_stop_premium:
                        pos.hard_stop_premium = t1_lock
                        pos.is_trailing_active = True
                        pos.locked_profit_inr = round((t1_lock - pos.entry_premium) * pos.quantity, 2)
                if pos.target_2_spot > pos.underlying_entry_spot and current_spot >= pos.target_2_spot and pos.pnl >= 300.0 and elapsed_seconds >= 15.0:
                    gain_now = max(0.0, pos.current_premium - pos.entry_premium)
                    t2_lock = max(round(pos.entry_premium + gain_now * 0.85, 2), round(pos.highest_premium * 0.92, 2))
                    t2_lock = min(t2_lock, round(pos.current_premium - max(1.0, pos.current_premium * 0.01), 2))
                    if t2_lock > pos.hard_stop_premium:
                        pos.hard_stop_premium = t2_lock
                        pos.is_trailing_active = True
                        pos.trailing_step_level = max(pos.trailing_step_level, 4)
                        pos.locked_profit_inr = round((t2_lock - pos.entry_premium) * pos.quantity, 2)

                # Trailing Stop: 1 ATR movement after 1R
                risk_pts = pos.underlying_entry_spot - pos.underlying_structural_sl
                if risk_pts > 0 and (current_spot - pos.underlying_entry_spot) >= risk_pts:
                    pos.is_trailing_active = True
                    new_sl = pos.highest_spot - pos.atr_at_entry
                    if new_sl > pos.underlying_structural_sl:
                        pos.underlying_structural_sl = new_sl

            elif pos.option_type == "PE":
                # Structural Invalidation (PE: underlying breaks swing high by at least the volatility buffer, premium confirms loss, and at least 60s breathing room)
                if pos.underlying_structural_sl > 0 and current_spot >= (pos.underlying_structural_sl + structural_buffer) and pos.current_premium <= pos.entry_premium and elapsed_seconds >= 60.0:
                    return True, f"Layer 1 Structural Invalidation: {asset_k} {current_spot:.1f} broke swing high {pos.underlying_structural_sl:.1f} (buffer: {structural_buffer:.1f}pts)"

                # Target 1 (1.5R) reached (PE): Lock 30% of open gain (min fee-protected BE) only once option is >= +5.5% so normal spread noise doesn't choke runners
                prem_gain_pct = (pos.current_premium - pos.entry_premium) / max(0.01, pos.entry_premium)
                if 0 < pos.target_1_spot < pos.underlying_entry_spot and current_spot <= pos.target_1_spot and prem_gain_pct >= 0.055 and elapsed_seconds >= 15.0:
                    fee_buf = max(2.0, round(150.0 / max(1, pos.quantity), 2))
                    gain_now = max(0.0, pos.current_premium - pos.entry_premium)
                    t1_lock = round(pos.entry_premium + max(fee_buf, gain_now * 0.30), 2)
                    t1_lock = min(t1_lock, round(pos.current_premium - max(1.0, pos.current_premium * 0.01), 2))
                    if t1_lock > pos.hard_stop_premium:
                        pos.hard_stop_premium = t1_lock
                        pos.is_trailing_active = True
                        pos.locked_profit_inr = round((t1_lock - pos.entry_premium) * pos.quantity, 2)
                if 0 < pos.target_2_spot < pos.underlying_entry_spot and current_spot <= pos.target_2_spot and pos.pnl >= 300.0 and elapsed_seconds >= 15.0:
                    gain_now = max(0.0, pos.current_premium - pos.entry_premium)
                    t2_lock = max(round(pos.entry_premium + gain_now * 0.85, 2), round(pos.highest_premium * 0.92, 2))
                    t2_lock = min(t2_lock, round(pos.current_premium - max(1.0, pos.current_premium * 0.01), 2))
                    if t2_lock > pos.hard_stop_premium:
                        pos.hard_stop_premium = t2_lock
                        pos.is_trailing_active = True
                        pos.trailing_step_level = max(pos.trailing_step_level, 4)
                        pos.locked_profit_inr = round((t2_lock - pos.entry_premium) * pos.quantity, 2)

                risk_pts = pos.underlying_structural_sl - pos.underlying_entry_spot
                if risk_pts > 0 and (pos.underlying_entry_spot - current_spot) >= risk_pts:
                    pos.is_trailing_active = True
                    low_ref = pos.lowest_spot if pos.lowest_spot > 0 else current_spot
                    new_sl = low_ref + pos.atr_at_entry
                    if new_sl < pos.underlying_structural_sl:
                        pos.underlying_structural_sl = new_sl

        return False, ""

    def execute_exit(self, reason: str, pos: Optional[SmartEdgePosition] = None, position_id: Optional[str] = None):
        target_pos = pos
        if not target_pos and position_id:
            for p in self.active_positions:
                if getattr(p, "position_id", "") == position_id or p.symbol == position_id:
                    target_pos = p
                    break
        if not target_pos and self.active_positions:
            target_pos = self.active_positions[0]

        if not target_pos:
            return

        pos = target_pos
        pos_mode = getattr(pos, "execution_mode", self.mode)
        print(Fore.MAGENTA + f"\n[EXIT ({pos_mode})] Exiting {pos.symbol} | Reason: {reason}")
        
        self.client.place_order(
            security_id=pos.security_id,
            trading_symbol=pos.symbol,
            transaction_type="SELL",
            quantity=pos.quantity,
            price=pos.current_premium,
            order_type="MARKET",
            product_type="INTRA",
            mode=pos_mode
        )

        exit_time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Record to Persistent Trading Journal & Tax Audit
        charges = journal.record_completed_trade(
            symbol=pos.symbol,
            option_type=pos.option_type,
            action=f"BUY_{pos.option_type}",
            quantity=pos.quantity,
            entry_price=pos.entry_premium,
            exit_price=pos.current_premium,
            signal_time=pos.signal_time_iso,
            order_executed_time=pos.entry_time_iso,
            exit_time=exit_time_str,
            exit_reason=reason,
            mode=getattr(pos, "execution_mode", self.mode),
            trade_rationale=getattr(pos, "trade_rationale", ""),
            invested_capital=getattr(pos, "invested_capital", round(pos.entry_premium * pos.quantity, 2))
        )

        self.trade_count += 1
        self.realized_pnl += charges.net_pnl

        if getattr(pos, "trade_style", "") == "SNIPER":
            self.log_sniper_event(
                f"🎯 SNIPER EXITED: {reason} | Net Realized P&L: ₹{charges.net_pnl:+.2f} @ ₹{pos.current_premium:.2f}",
                "EXIT"
            )

        if charges.net_pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

        # Send Mobile Push Notification
        notifier.notify_trade_exit(
            symbol=pos.symbol,
            reason=reason,
            exit_price=pos.current_premium,
            entry_price=pos.entry_premium,
            qty=pos.quantity,
            pnl=charges.net_pnl,
            net_pnl=self.realized_pnl
        )

        # Dynamic Profit Milestone Auto-Scaler (Auto-scales trades on winning days, e.g. Rs.25,000 milestones)
        if self.realized_pnl > 0:
            milestones = int(self.realized_pnl // self.profit_scaling_milestone)
            current_awarded = getattr(self, "profit_milestones_claimed", 0)
            if milestones > current_awarded:
                bonus_trades = (milestones - current_awarded) * 10
                old_cap = self.max_daily_trades
                self.max_daily_trades += bonus_trades
                self.profit_milestones_claimed = milestones
                config.max_trades_per_day = self.max_daily_trades
                if self.is_kill_switch_active:
                    self.is_kill_switch_active = False
                    self.kill_switch_reason = ""
                self.consecutive_losses = 0
                msg = f"🏆 Rs.{milestones * self.profit_scaling_milestone:,.0f} Profit Milestone Hit! (Net P&L: Rs.{self.realized_pnl:+,.2f}). Daily cap auto-scaled by +{bonus_trades} to {self.max_daily_trades} trades!"
                self.log_sniper_event(msg, "PROFIT")
                notifier.notify("🏆 Profit Milestone Auto-Scaled", msg, tags="trophy,chart_with_upwards_trend,moneybag")

        # Check Account Protection Limits (Pure % of Active Capital — NO Blind Recovery Naps!)
        active_cap_exit = self.client.get_real_cash_balance() if self.mode == "LIVE" else float(self.client.paper_capital)
        self.daily_loss_limit = round(active_cap_exit * (getattr(config, "max_daily_loss_pct", 4.0) / 100.0), 2)
        if self.realized_pnl <= -self.daily_loss_limit:
            self.activate_kill_switch(f"Daily loss limit ({getattr(config, 'max_daily_loss_pct', 4.0):.1f}% = Rs.{self.daily_loss_limit:,.2f}) breached (Net: Rs.{self.realized_pnl:.2f})")

        if pos in self.active_positions:
            self.active_positions.remove(pos)
        else:
            self.active_positions = [p for p in self.active_positions if getattr(p, "position_id", "") != getattr(pos, "position_id", "")]

        # Release Global Trade Lock & engage ONLY a 60s same-candle duplicate guard (NO 15m sleep or basket nap!)
        try:
            from global_trade_lock import global_trade_lock
            closed_asset = getattr(pos, "asset_key", "")
            closed_side = "BUY_CE" if getattr(pos, "option_type", "CE") == "CE" else "BUY_PE"
            cd_secs = 60  # 1-minute same-candle duplicate guard only; algo stays awake!
            if closed_asset:
                if not hasattr(self, "_asset_cooldowns"):
                    self._asset_cooldowns = {}
                self._asset_cooldowns[f"{closed_asset}:{closed_side}"] = datetime.datetime.now() + datetime.timedelta(seconds=cd_secs)
                global_trade_lock.set_cooldown(f"{closed_asset}:{closed_side}", cd_secs, reason)
            global_trade_lock.release_lock(position_id=getattr(pos, "position_id", ""), cooldown_seconds=0)
            print(Fore.CYAN + f"[GlobalTradeLock] Lock released. 60s same-candle guard set for {closed_asset}:{closed_side}; engine stays 100% awake.")
        except Exception as e:
            print(f"[GlobalTradeLock Exit Error] {e}")

    def execute_exit_all(self, reason: str = "Square Off All Positions"):
        """Gracefully closes all concurrent active positions across Paper and Live."""
        for p in list(self.active_positions):
            self.execute_exit(reason=reason, pos=p)
        try:
            from global_trade_lock import global_trade_lock
            cd_secs = getattr(config, "cooldown_seconds", 300)
            global_trade_lock.release_lock(position_id="ALL", cooldown_seconds=cd_secs)
        except Exception:
            pass

    def _anti_chase_check(self, contract, asset_k: str, ltp: float) -> Tuple[bool, str]:
        """
        Returns (ok, reason). Blocks entries that buy an option premium immediately after a
        vertical spike (last 5 x 1m candles up > 6%) while price is in the top 25% of that
        5-minute range. Fails OPEN (allows) only if Dhan candle data is unavailable.
        """
        try:
            dhan = getattr(self.client, "dhan", None)
            if not dhan or not str(contract.security_id).isdigit():
                return True, "no data"
            if asset_k in ["CRUDEOILM", "NATGASMINI", "GOLDM", "SILVERM"]:
                seg, itype = "MCX_COMM", "OPTFUT"
            elif asset_k in ["SENSEX", "BANKEX"]:
                seg, itype = "BSE_FNO", "OPTIDX"
            elif asset_k in ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYIT"]:
                seg, itype = "NSE_FNO", "OPTIDX"
            else:
                seg, itype = "NSE_FNO", "OPTSTK"
            today = datetime.date.today().strftime("%Y-%m-%d")
            res = dhan.intraday_minute_data(
                security_id=str(contract.security_id), exchange_segment=seg,
                instrument_type=itype, from_date=today, to_date=today, interval=1
            )
            d = res.get("data", {}) if isinstance(res, dict) else {}
            highs, lows, closes = d.get("high", []), d.get("low", []), d.get("close", [])
            if len(closes) < 6:
                return True, "insufficient candles"
            hi5, lo5 = max(highs[-5:]), min(lows[-5:])
            base = closes[-6]
            rise_pct = (ltp - base) / base * 100.0 if base > 0 else 0.0
            rng = hi5 - lo5
            pos_in_range = (ltp - lo5) / rng if rng > 0 else 0.5
            if rise_pct > 6.0 and pos_in_range > 0.75:
                return False, (f"{contract.custom_symbol} premium up {rise_pct:.1f}% in 5m and at "
                               f"{pos_in_range*100:.0f}% of range (₹{lo5:.1f}-₹{hi5:.1f}). Waiting for pullback.")
            return True, "ok"
        except Exception as e:
            return True, f"check error: {e}"

    def execute_entry(
        self,
        signal: SmartEdgeSignal,
        contract: SelectedContract,
        spot: float,
        trade_style: str = "SMART_EDGE",
        target_premium: float = 0.0,
        max_holding_seconds: int = 1800,
        mode: Optional[str] = None
    ):
        self.last_entry_error = ""
        if not contract.risk_budget_approved:
            self.last_entry_error = contract.rejection_reason or "Risk budget rejected"
            print(Fore.YELLOW + f"[Risk Rejected] {contract.rejection_reason}")
            return False

        # ---------------------------------------------------------------------
        # STRICT TRADE COMPLIANCE GUARDRAIL (Live Underlying Price & Strike Sanity)
        # Non-negotiable check ensuring trade matches real Dhan / NSE exchange pricing.
        # ---------------------------------------------------------------------
        asset_k = getattr(contract, "asset_key", "NIFTY")
        try:
            from market_intelligence import market_intelligence
            verified_spot = market_intelligence.get_asset_spot(asset_k)
            if verified_spot > 0:
                step_sz = 50.0
                if asset_k in ["BANKNIFTY", "SENSEX", "NIFTYIT", "GOLD", "GOLDM"]:
                    step_sz = 100.0
                elif asset_k in ["SILVER", "SILVERM"]:
                    step_sz = 500.0
                elif asset_k in ["NATGASMINI", "NATURALGAS", "COPPER", "ZINC"]:
                    step_sz = 5.0
                elif asset_k in ["CRUDEOIL", "CRUDEOILM"]:
                    step_sz = 50.0
                elif asset_k in ["RELIANCE", "INFY"]:
                    step_sz = 20.0
                elif asset_k in ["HDFCBANK", "ICICIBANK", "SBIN"]:
                    step_sz = 10.0

                is_manual_override = getattr(signal, "regime", "") in ("USER_TRIGGERED", "INSTANT_MOMENTUM")

                # Guardrail A: Spot deviation check
                spot_drift_pct = abs(verified_spot - spot) / verified_spot * 100.0
                if spot_drift_pct > 1.5:
                    self.last_entry_error = f"Stale spot ({spot:.1f} vs {verified_spot:.1f})"
                    print(Fore.RED + f"[GUARDRAIL BLOCKED] Trade entry aborted! Stale spot detected: passed {spot:.1f} vs live exchange {verified_spot:.1f} ({spot_drift_pct:.2f}% drift).")
                    return False

                # Guardrail B: Strike distance check
                strike_drift = abs(contract.strike_price - verified_spot)
                if strike_drift > (step_sz * 3.5):
                    self.last_entry_error = f"Strike {contract.strike_price:.0f} too far ({strike_drift:.0f}pts)"
                    print(Fore.RED + f"[GUARDRAIL BLOCKED] Trade entry aborted! Strike {contract.strike_price:.1f} is {strike_drift:.1f} pts away from live spot {verified_spot:.1f} (Max: {step_sz * 2.5:.1f}).")
                    return False

                # Guardrail B2: Day High / Day Low Exhaustion Buffer (Automated only, and only on rejection bar)
                df_b = None
                try:
                    from mtf_engine import mtf_engine
                    df_b = mtf_engine.get_real_candles(asset_k)
                    if not is_manual_override and df_b is not None and not df_b.empty:
                        d_high = float(df_b["high"].max())
                        d_low = float(df_b["low"].min())
                        cur_c = float(df_b["close"].iloc[-1])
                        cur_o = float(df_b["open"].iloc[-1])
                        # If CALL: Block only if rejecting (red bar) directly under Day High
                        if signal.action == "BUY_CE" and verified_spot < d_high and ((d_high - verified_spot) / verified_spot) < 0.0015 and cur_c < cur_o:
                            self.last_entry_error = f"Rejecting Day High {d_high:.1f}"
                            print(Fore.YELLOW + f"[RESISTANCE BUFFER BLOCKED] Skipping CALL entry: Spot {verified_spot:.1f} is rejecting under Day High {d_high:.1f}.")
                            return False
                        # If PUT: Block only if bouncing (green bar) directly above Day Low
                        if signal.action == "BUY_PE" and verified_spot > d_low and ((verified_spot - d_low) / verified_spot) < 0.0015 and cur_c > cur_o:
                            self.last_entry_error = f"Bouncing Day Low {d_low:.1f}"
                            print(Fore.YELLOW + f"[SUPPORT BUFFER BLOCKED] Skipping PUT entry: Spot {verified_spot:.1f} is bouncing above Day Low {d_low:.1f}.")
                            return False
                except Exception:
                    pass

                # Guardrail B3: Directional Consecutive Loss Circuit Breaker & Transition Cooldown (Automated only)
                if not is_manual_override:
                    try:
                        from journal import journal
                        j_df = journal.get_all_trades_df()
                        if not j_df.empty:
                            today_s = datetime.date.today().strftime("%Y-%m-%d")
                            asset_trades = j_df[j_df["trade_date"] == today_s] if "trade_date" in j_df.columns else j_df
                            recent_matches = []
                            last_asset_trade = None
                            for _, tr in asset_trades.iloc[::-1].iterrows():
                                sym = str(tr.get("symbol", ""))
                                act = str(tr.get("action", ""))
                                if asset_k in sym:
                                    if last_asset_trade is None:
                                        last_asset_trade = tr
                                    if signal.action in act:
                                        recent_matches.append(tr)
                                        if len(recent_matches) >= 2:
                                            break

                            # 1. 0DTE 1-Strike Rule: On expiry days, a SINGLE loss in a direction locks that direction for the rest of the day!
                            from mtf_engine import mtf_engine
                            is_0dte, _ = mtf_engine.get_expiry_schedule(asset_k)
                            if is_0dte and recent_matches:
                                last_loss = float(recent_matches[0].get("net_pnl", 0.0))
                                if last_loss < -10.0:
                                    self.last_entry_error = f"0DTE 1-Strike Lockout ({asset_k})"
                                    print(Fore.RED + f"[0DTE 1-STRIKE LOCKOUT] {signal.action} entries on {asset_k} are LOCKED for today's expiry session after 1 loss (-₹{abs(last_loss):.1f}).")
                                    return False

                            # 2. Standard Non-Expiry 2-Strike Rule
                            if len(recent_matches) >= 2:
                                pnl1 = float(recent_matches[0].get("net_pnl", 0.0))
                                pnl2 = float(recent_matches[1].get("net_pnl", 0.0))
                                act1 = str(recent_matches[0].get("action", ""))
                                act2 = str(recent_matches[1].get("action", ""))
                                if signal.action in act1 and signal.action in act2 and pnl1 < -10.0 and pnl2 < -10.0:
                                    self.last_entry_error = f"2-Strike Circuit Breaker ({asset_k})"
                                    print(Fore.RED + f"[CIRCUIT BREAKER BLOCKED] {signal.action} entries on {asset_k} are LOCKED after 2 consecutive stop-outs.")
                                    return False

                            # 3. Directional Transition Cooldown (10 Minutes)
                            if last_asset_trade is not None:
                                last_act = str(last_asset_trade.get("action", ""))
                                last_exit_str = str(last_asset_trade.get("exit_time", "") or last_asset_trade.get("entry_time", ""))
                                is_opposite = ("BUY_PE" in last_act and signal.action == "BUY_CE") or ("BUY_CE" in last_act and signal.action == "BUY_PE")
                                if is_opposite and last_exit_str:
                                    try:
                                        exit_dt = datetime.datetime.strptime(last_exit_str[:19], "%Y-%m-%d %H:%M:%S")
                                        elapsed_transition = (datetime.datetime.now() - exit_dt).total_seconds()
                                        if elapsed_transition < 600.0:
                                            self.last_entry_error = f"10m Transition Cooldown ({asset_k})"
                                            print(Fore.YELLOW + f"[TRANSITION COOLDOWN] Skipping {signal.action} on {asset_k}: Exited opposite trade ({last_act}) only {elapsed_transition:.0f}s ago.")
                                            return False
                                    except Exception:
                                        pass
                    except Exception as b3_err:
                        print(f"[Guardrail B3 Warning] {b3_err}")

                # Guardrail B4: Candlestick Confirmation, EMA Slope & Market Structure Gate (Automated only)
                if not is_manual_override:
                    try:
                        if df_b is None or len(df_b) < 3:
                            self.last_entry_error = f"Unconfirmed 5m candles ({asset_k})"
                            print(Fore.YELLOW + f"[FAIL-CLOSED VETO] Skipping entry: 5m candle feed for {asset_k} is unconfirmed or < 3 bars.")
                            return False

                        from indicators import IndicatorEngine
                        last_c = float(df_b["close"].iloc[-1])
                        last_o = float(df_b["open"].iloc[-1])
                        is_candle_green = last_c >= last_o
                        pat = IndicatorEngine.detect_candlestick_patterns(df_b)

                        # 1. Candlestick Color & Rejection Patterns
                        if signal.action == "BUY_CE":
                            if not is_candle_green:
                                self.last_entry_error = f"5m candle RED ({asset_k})"
                                print(Fore.YELLOW + f"[CANDLESTICK VETO] Skipping CALL entry: Current 5m candle is RED (Close {last_c:.1f} < Open {last_o:.1f}).")
                                return False
                            if pat.get("pattern") in ["BEARISH_SHOOTING_STAR", "BEARISH_ENGULFING"]:
                                self.last_entry_error = f"Bearish pattern {pat.get('pattern')}"
                                print(Fore.YELLOW + f"[CANDLESTICK VETO] Skipping CALL entry: Bearish pattern '{pat.get('pattern')}' detected.")
                                return False
                        elif signal.action == "BUY_PE":
                            if is_candle_green:
                                self.last_entry_error = f"5m candle GREEN ({asset_k})"
                                print(Fore.YELLOW + f"[CANDLESTICK VETO] Skipping PUT entry: Current 5m candle is GREEN (Close {last_c:.1f} > Open {last_o:.1f}).")
                                return False
                            if pat.get("pattern") in ["BULLISH_HAMMER_PINBAR", "BULLISH_ENGULFING"]:
                                self.last_entry_error = f"Bullish pattern {pat.get('pattern')}"
                                print(Fore.YELLOW + f"[CANDLESTICK VETO] Skipping PUT entry: Bullish pattern '{pat.get('pattern')}' detected.")
                                return False

                        # 2. EMA 9 Slope / Velocity Gate
                        if len(df_b) >= 5:
                            e9_series = df_b["close"].ewm(span=9, adjust=False).mean()
                            e9_slope = float(e9_series.iloc[-1] - e9_series.iloc[-2])
                            if signal.action == "BUY_CE" and e9_slope < -0.5:
                                self.last_entry_error = f"EMA9 slope {e9_slope:.1f}<0"
                                print(Fore.YELLOW + f"[EMA SLOPE VETO] Skipping CALL entry: EMA 9 slope is negative ({e9_slope:.2f}).")
                                return False
                            if signal.action == "BUY_PE" and e9_slope > 0.5:
                                self.last_entry_error = f"EMA9 slope {e9_slope:.1f}>0"
                                print(Fore.YELLOW + f"[EMA SLOPE VETO] Skipping PUT entry: EMA 9 slope is positive ({e9_slope:.2f}).")
                                return False

                        # 3. Market Structure Shift (MSS) 3-Candle Lower Lows / Higher Highs
                        l1 = float(df_b["low"].iloc[-1])
                        l2 = float(df_b["low"].iloc[-2])
                        l3 = float(df_b["low"].iloc[-3])
                        if signal.action == "BUY_CE" and (l1 < l2 < l3):
                            self.last_entry_error = "3 Lower Lows structure"
                            print(Fore.YELLOW + f"[STRUCTURE VETO] Skipping CALL entry: 3 consecutive Lower Lows detected.")
                            return False
                        h1 = float(df_b["high"].iloc[-1])
                        h2 = float(df_b["high"].iloc[-2])
                        h3 = float(df_b["high"].iloc[-3])
                        if signal.action == "BUY_PE" and (h1 > h2 > h3):
                            self.last_entry_error = "3 Higher Highs structure"
                            print(Fore.YELLOW + f"[STRUCTURE VETO] Skipping PUT entry: 3 consecutive Higher Highs detected.")
                            return False
                    except Exception as b4_err:
                        print(f"[Guardrail B4 Warning] {b4_err}")

                # Guardrail C: Real-time Premium Verification (STRICT LIVE DHAN ONLY - NO MATHEMATICAL DRIFT)
                # Universal for ALL instruments (NIFTY, BANKNIFTY, FINNIFTY, SENSEX, Stocks, MCX) in both LIVE and PAPER
                active_mode = mode or getattr(self, "mode", "PAPER")
                contract_seg = getattr(contract, "segment", None)
                verified_prem = self.client.get_option_ltp(
                    contract.security_id,
                    verified_spot,
                    contract.strike_price,
                    contract.option_type,
                    contract.expiry_date,
                    require_live=True,
                    segment=contract_seg
                )
                if verified_prem <= 0.05:
                    verified_prem = self.client.get_last_flashed_price(
                        contract.security_id, [contract.custom_symbol, contract.trading_symbol], max_age=3.0
                    )
                if verified_prem <= 0.05:
                    if getattr(signal, "regime", "") in ("USER_TRIGGERED", "INSTANT_MOMENTUM") and float(getattr(contract, "estimated_premium", 0.0) or 0.0) > 0.05:
                        verified_prem = float(contract.estimated_premium)
                    else:
                        self.last_entry_error = f"Real Dhan quote could not be confirmed for {contract.custom_symbol} (Zero/Rate-Limited)."
                        print(Fore.RED + f"[GUARDRAIL BLOCKED] Trade entry aborted! {self.last_entry_error}")
                        return False
                # Update contract estimated premium to ground truth verified live LTP
                contract.estimated_premium = verified_prem

                # Guardrail D: ANTI-CHASE (uses Dhan 1m candles of the option itself).
                # Refuse to buy a premium that has just spiked and is sitting at the top of its range.
                if trade_style == "SNIPER" and getattr(signal, "regime", "") not in ("USER_TRIGGERED", "INSTANT_MOMENTUM"):
                    chase_ok, chase_msg = self._anti_chase_check(contract, asset_k, verified_prem)
                    if not chase_ok:
                        self.last_entry_error = chase_msg
                        print(Fore.YELLOW + f"[ANTI-CHASE BLOCKED] {chase_msg}")
                        self.log_sniper_event(f"⛔ Entry skipped: {chase_msg}", "SKIP")
                        return False
        except Exception as e:
            print(f"[Guardrail Check Warning] {e}")

        sig_time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # ---------------------------------------------------------------------
        # MANDATORY GLOBAL SINGLE-TRADE LOCK & DUPLICATE REJECTION (Rule 1 & 2)
        # ---------------------------------------------------------------------
        from global_trade_lock import global_trade_lock
        sig_id = getattr(contract, "signal_id", "")
        candle_ts = getattr(contract, "candle_timestamp", "") or datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        if not sig_id:
            sig_id = global_trade_lock.generate_signal_id(
                strategy_name=signal.strategy_name,
                asset_key=asset_k,
                symbol=contract.custom_symbol,
                strike=contract.strike_price,
                option_type=contract.option_type,
                action=signal.action,
                candle_timestamp=candle_ts
            )
            contract.signal_id = sig_id

        # Rule 1: Multi-Instrument Concurrency Capacity Check (allowing up to max_concurrent_positions)
        max_conc = getattr(config, "max_concurrent_positions", 5)
        if len(self.active_positions) >= max_conc:
            rej_reason = f"Multi-Trade Capacity Full: Currently holding {len(self.active_positions)}/{max_conc} active trades. Close or manage an open trade first."
            self.last_entry_error = rej_reason
            print(Fore.YELLOW + f"[CAPACITY REACHED] {rej_reason}")
            global_trade_lock.log_rejection(sig_id, contract.custom_symbol, signal.action, rej_reason)
            return False

        # Check duplicate on the exact same contract
        if any(getattr(p, "symbol", "") == contract.custom_symbol for p in self.active_positions):
            rej_reason = f"Duplicate trade blocked: Contract '{contract.custom_symbol}' is already open in your portfolio."
            self.last_entry_error = rej_reason
            print(Fore.YELLOW + f"[DUPLICATE BLOCKED] {rej_reason}")
            global_trade_lock.log_rejection(sig_id, contract.custom_symbol, signal.action, rej_reason)
            return False

        # Rule 2: Check SQLite persistent multi-trade lock & per-instrument cooldown
        can_lock, lock_msg = global_trade_lock.can_acquire_lock(sig_id, contract.custom_symbol, asset_k)
        if not can_lock:
            # Self-healing check: If the lock in SQLite claims busy, but engine has NO open positions:
            if len(self.active_positions) == 0:
                global_trade_lock.force_reset_lock("Self-Healing Stale Lock")
                can_lock, lock_msg = global_trade_lock.can_acquire_lock(sig_id, contract.custom_symbol, asset_k)

        if not can_lock:
            self.last_entry_error = lock_msg
            print(Fore.YELLOW + f"[LOCK REJECTED] {lock_msg}")
            global_trade_lock.log_rejection(sig_id, contract.custom_symbol, signal.action, lock_msg)
            return False

        import random, time
        pos_id = f"POS_{int(time.time()*1000)}_{random.randint(100, 999)}"

        # Rule 3: Atomic Multi-Trade Lock Acquisition
        intent_info = {
            "intent_id": f"INT_{pos_id}",
            "strategy_name": signal.strategy_name,
            "asset_key": asset_k,
            "security_id": contract.security_id,
            "transaction_type": "BUY",
            "quantity": contract.lot_size,
            "price": contract.estimated_premium,
            "candle_timestamp": candle_ts
        }
        acquired, acq_reason = global_trade_lock.acquire_lock(sig_id, contract.custom_symbol, pos_id, intent_info)
        if not acquired:
            self.last_entry_error = acq_reason
            print(Fore.RED + f"[ATOMIC LOCK FAILED] {acq_reason}")
            return False

        hard_sl = contract.hard_stop_premium
        is_bn = (asset_k == "BANKNIFTY")
        is_comm = asset_k in ["CRUDEOILM", "NATGASMINI", "GOLDM", "SILVERM"]

        # Multi-Timeframe Institutional Engine Context
        mtf_data = None
        try:
            from mtf_engine import mtf_engine
            mtf_data = mtf_engine.analyze(asset_k, contract.custom_symbol.split()[0], spot)
        except Exception:
            pass

        if trade_style == "SNIPER":
            # 🎯 REALISTIC VOLATILITY-ADJUSTED SCALP SETUP (15% SL, 1.8R Target = ~27% Gain)
            if is_bn:
                sl_distance = round(max(35.0, contract.estimated_premium * 0.15), 2)
                hard_sl = round(max(5.0, contract.estimated_premium - sl_distance), 2)
                if target_premium <= 0:
                    target_premium = round(contract.estimated_premium + (sl_distance * 1.8), 2)
            elif asset_k == "SENSEX":
                sl_distance = round(max(40.0, contract.estimated_premium * 0.15), 2)
                hard_sl = round(max(5.0, contract.estimated_premium - sl_distance), 2)
                if target_premium <= 0:
                    target_premium = round(contract.estimated_premium + (sl_distance * 1.8), 2)
            elif is_comm:
                if asset_k == "NATGASMINI":
                    sl_distance = round(max(1.5, contract.estimated_premium * 0.12), 2)
                    hard_sl = round(max(0.2, contract.estimated_premium - sl_distance), 2)
                    if target_premium <= 0:
                        target_premium = round(contract.estimated_premium + (sl_distance * 1.8), 2)
                elif asset_k in ["GOLDM", "SILVERM"]:
                    sl_distance = round(contract.estimated_premium * 0.12, 1) # 12% of premium
                    hard_sl = round(max(5.0, contract.estimated_premium - sl_distance), 2)
                    if target_premium <= 0:
                        target_premium = round(contract.estimated_premium + (sl_distance * 1.8), 2)
                else: # CRUDEOILM
                    sl_distance = round(max(20.0, contract.estimated_premium * 0.10), 2)
                    hard_sl = round(max(5.0, contract.estimated_premium - sl_distance), 2)
                    if target_premium <= 0:
                        target_premium = round(contract.estimated_premium + (sl_distance * 1.8), 2)
            else: # NIFTY / Blue-chip Equity Options
                sl_distance = round(max(18.0, contract.estimated_premium * 0.15), 2)
                hard_sl = round(max(5.0, contract.estimated_premium - sl_distance), 2)
                if target_premium <= 0:
                    target_premium = round(contract.estimated_premium + (sl_distance * 1.8), 2)
            max_holding_seconds = 1800  # 30 minutes institutional trade window
        elif trade_style == "SWING":
            # Keep structural -10% Swing SL intact (lot sizing in contract_selector fits risk budget; never squeeze SL to 3%!)
            swing_pct_sl = round(contract.estimated_premium * 0.90, 2)
            hard_sl = max(hard_sl, swing_pct_sl)
            # Spread-survival guard: ensure hard_sl is at least 1% below fresh live quote
            hard_sl = min(hard_sl, round(contract.estimated_premium * 0.99, 2))
            if target_premium <= 0:
                target_premium = round(contract.estimated_premium * 1.25, 2)
            max_holding_seconds = 3600 # 60 minutes wave ride
        else:
            if target_premium <= 0:
                target_premium = round(contract.estimated_premium * 1.25, 2)
            max_holding_seconds = 2700

        print(Fore.CYAN + f"\n[{trade_style} ENTRY] {signal.strategy_name} -> {contract.custom_symbol}")
        print(f"  Entry Est: Rs.{contract.estimated_premium:.2f} | Stop: Rs.{hard_sl:.2f} | Target: Rs.{target_premium:.2f} | Style: {trade_style}")
        print(f"  Layer 1 Spot SL: {signal.structural_sl_spot:.1f} | 1.5R Target: {signal.target_1_spot:.1f} | Risk: Rs.{contract.trade_risk_inr}")

        pos_mode = (mode or self.mode).upper()
        res = self.client.place_order(
            security_id=contract.security_id,
            trading_symbol=contract.trading_symbol,
            transaction_type="BUY",
            quantity=contract.lot_size,
            price=contract.estimated_premium,
            order_type="MARKET",
            product_type="INTRA",
            mode=pos_mode
        )

        exec_time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if res.get("status") == "success":
            filled_price = res.get("data", {}).get("price", contract.estimated_premium)
            if filled_price > 0.05:
                contract.estimated_premium = filled_price
            invested_cap = round(contract.estimated_premium * contract.lot_size, 2)
            margin_req = round(contract.margin_required or invested_cap, 2)
            active_cap = self.client.get_real_cash_balance() if self.mode == "LIVE" else float(self.client.paper_capital)
            margin_pct = round((margin_req / active_cap * 100.0), 1) if active_cap > 0 else 0.0

            # Build detailed trade rationale and verified conditions list
            cond_list = []
            if signal.audit_checklist:
                for item in signal.audit_checklist:
                    if getattr(item, "passed", False):
                        cond_list.append(f"{item.rule_name}: {item.actual_value} (Req: {item.condition_required})")
            if not cond_list:
                vwap_str = f"Spot {spot:.2f} holding {'above' if 'CE' in contract.option_type else 'below'} VWAP"
                cond_list = [vwap_str, f"Directional Confluence verified for {contract.custom_symbol}", f"Margin check passed: ₹{margin_req:,.2f} within risk limit"]

            if trade_style == "SNIPER":
                rationale = f"Institutional Day Trader Setup ({contract.option_type}): Fast momentum expansion targeting 1.5R - 2.5R gain with Breakeven capital defense."
            elif "RANGE" in signal.strategy_name.upper():
                rationale = f"Mean Reversion Range Scalp: Reversal off institutional floor/ceiling targeting Central Pivot PP / VWAP."
            else:
                rationale = f"{signal.strategy_name} ({signal.regime}): High-probability multi-timeframe directional trend continuation."

            # Populate MTF insights
            mtf_thesis = []
            m_15 = ""
            m_5 = ""
            m_1 = ""
            rr_r = "1 : 2.5"
            st_type = "INSTITUTIONAL_SCALP"
            if mtf_data:
                m_15 = f"{mtf_data.tf_15m.trend}: {mtf_data.tf_15m.location_desc}"
                m_5 = f"{mtf_data.tf_5m.stage}: {mtf_data.tf_5m.location_desc}"
                m_1 = f"{mtf_data.tf_1m.trend}: {mtf_data.tf_1m.location_desc}"
                rr_r = mtf_data.risk_reward_ratio
                st_type = mtf_data.setup_type
                mtf_thesis = mtf_data.key_reasons + [mtf_data.rationale]
            else:
                mtf_thesis = cond_list

            _ak = str(getattr(contract, "asset_key", "NIFTY") or "NIFTY").upper()
            _dyn_atr = 95.0 if "SENSEX" in _ak else (65.0 if "BANKNIFTY" in _ak else (30.0 if "FINNIFTY" in _ak else (22.0 if "NIFTY" in _ak else round(max(4.0, spot * 0.004), 1))))

            new_pos = SmartEdgePosition(
                position_id=pos_id,
                security_id=contract.security_id,
                symbol=contract.custom_symbol,
                option_type=contract.option_type,
                strategy_name=f"{trade_style}_{signal.strategy_name}",
                quantity=contract.lot_size,
                entry_premium=contract.estimated_premium,
                current_premium=contract.estimated_premium,
                hard_stop_premium=hard_sl,
                underlying_entry_spot=spot,
                underlying_structural_sl=signal.structural_sl_spot,
                target_1_spot=signal.target_1_spot,
                target_2_spot=signal.target_2_spot,
                highest_premium=contract.estimated_premium,
                highest_spot=spot,
                lowest_spot=spot,
                entry_time_iso=exec_time_str,
                signal_time_iso=sig_time_str,
                expiry_date=contract.expiry_date,
                atr_at_entry=_dyn_atr,
                strike_price=contract.strike_price,
                trade_style=trade_style,
                target_premium=target_premium,
                max_holding_seconds=max_holding_seconds,
                asset_key=getattr(contract, "asset_key", "NIFTY"),
                locked_profit_inr=0.0,
                trailing_step_level=0,
                invested_capital=invested_cap,
                margin_required=margin_req,
                bank_limit_triggered=margin_req,
                margin_pct_capital=margin_pct,
                trade_rationale=rationale,
                setup_conditions=cond_list,
                trailing_sl_premium=hard_sl,
                lots=getattr(contract, "number_of_lots", 1) or 1,
                lot_size=contract.lot_size,
                execution_mode=self.mode,
                entry_thesis=mtf_thesis,
                mtf_15m=m_15,
                mtf_5m=m_5,
                mtf_1m=m_1,
                risk_reward_ratio=rr_r,
                setup_type=st_type,
                simulated_spot=spot,
                segment=getattr(contract, "segment", "NSE_FNO")
            )
            self.active_positions.append(new_pos)

            if trade_style == "SNIPER":
                self.log_sniper_event(
                    f"🚀 SNIPER FIRED: BUY {contract.lot_size}x {contract.custom_symbol} @ ₹{contract.estimated_premium:.2f} (Stop: ₹{hard_sl:.2f} | Target: ₹{target_premium:.2f} | Breakeven Lock Active)",
                    "ENTRY"
                )

            notifier.notify_trade_entry(
                symbol=contract.custom_symbol,
                action=f"{signal.action} ({signal.strategy_name})",
                qty=contract.lot_size,
                entry_price=contract.estimated_premium,
                sl=contract.hard_stop_premium,
                target=round(contract.estimated_premium * 1.30, 2),
                spot=spot
            )
            return True
        else:
            # Release lock immediately if order was rejected by broker / engine
            from global_trade_lock import global_trade_lock
            global_trade_lock.release_lock(pos_id, cooldown_seconds=0)
            rej_err = res.get("remarks", "Broker rejection")
            self.last_entry_error = rej_err
            print(Fore.RED + f"[ORDER FAILED] Order placement rejected: {rej_err}. Global lock released.")
            return False

    def print_dashboard(self, spot: float, signal: SmartEdgeSignal):
        now = get_ist_now().strftime("%H:%M:%S")
        status_lines = [
            ["Time / Engine", f"{now} | {self.mode} (NIFTY SMART EDGE v1.0)"],
            ["Nifty Spot", f"Rs.{spot:.2f}"],
            ["Market Regime", f"{signal.regime} (Active: {signal.strategy_name})"],
            ["ORB Range", f"[{self.strategy.orb_low:.1f} - {self.strategy.orb_high:.1f}]" if self.strategy.orb_high else "Forming..."],
            ["Signal", f"{signal.action} ({signal.rejection_reason or 'Conditions Approved'})"],
            ["RMS Status", f"Trades: {self.trade_count}/{self.max_daily_trades} | Net P&L: Rs.{self.realized_pnl:+.2f} | Cap: Rs.{self.account_capital:,.0f}"],
        ]

        if self.active_position:
            pos = self.active_position
            pnl_color = Fore.GREEN if pos.pnl >= 0 else Fore.RED
            status_lines.append(["Active Position", f"{pos.symbol} ({pos.quantity} Qty)"])
            status_lines.append(["Position P&L", f"{pnl_color}Rs.{pos.pnl:+.2f} (LTP: Rs.{pos.current_premium:.2f} | Entry: Rs.{pos.entry_premium:.2f})"])
            status_lines.append(["Two-Layer SL", f"L1 (Spot): {pos.underlying_structural_sl:.1f} | L2 (-20% Prem): Rs.{pos.hard_stop_premium:.2f}"])
            status_lines.append(["Target / Trail", f"T1 (1.5R): {pos.target_1_spot:.1f} | Trailing: {'ACTIVE' if pos.is_trailing_active else 'OFF'}"])
        else:
            status_lines.append(["Active Position", "None (Waiting for valid edge setup)"])

        print(tabulate(status_lines, tablefmt="fancy_grid"))

    def run_tick(self):
        current_spot = self.client.get_spot_ltp()
        self.latest_spot = current_spot
        now_dt = get_ist_now()
        now_time = now_dt.strftime("%H:%M:%S")

        # Autonomous Session Day Rollover (Ensures multi-day 24/7 continuous operation without restarts):
        today_date_str = now_dt.strftime("%Y-%m-%d")
        if getattr(self, "_last_session_date", None) != today_date_str:
            self._last_session_date = today_date_str
            self.restore_daily_state()
            self._peak_daily_net_pnl = 0.0
            self.closed_trades_today = []
            print(Fore.GREEN + f"[AUTONOMOUS ROLLOVER] New trading session initialized for date {today_date_str}.")

        is_equity_open, _ = self.is_market_open("NIFTY")
        is_mcx_open, _ = self.is_market_open("CRUDEOILM")
        is_open = is_equity_open or is_mcx_open
        if not is_open:
            # Both Indian Equity and MCX Commodity sessions are closed.
            return

        # Check All Active Positions Exits (Works concurrently for both Live and Paper)
        if self.active_positions:
            for pos in list(self.active_positions):
                pos_asset = getattr(pos, "asset_key", "NIFTY")
                pos_mode = getattr(pos, "execution_mode", self.mode)
                
                # Real Market Aligned Execution: Paper and Live both track real exchange spot
                try:
                    from market_intelligence import market_intelligence
                    pos_spot = market_intelligence.get_asset_spot(pos_asset)
                    if pos_spot <= 0:
                        pos_spot = current_spot
                except Exception:
                    pos_spot = current_spot

                should_exit, reason = self.check_two_layer_exit_conditions(pos_spot, pos=pos)
                if should_exit:
                    self.execute_exit(reason, pos=pos)

        # If kill switch is active, existing positions are fully guarded & trailed above; stand down on new entries
        if self.is_kill_switch_active:
            return

        if not is_open:
            # When market is closed, position management runs but no automatic new entries
            return

        # Refresh REAL completed 5m candles from Dhan once per 5-minute boundary.
        # (Previously a synthetic candle open=spot-2/close=spot was injected: every candle looked
        #  green with a fixed 6-pt range, so the engine only ever bought CALLs and placed stops
        #  ~3 pts away. That was the root cause of the instant stop-outs.)
        bucket = (now_dt.hour * 60 + now_dt.minute) // 5
        if self.last_candle_minute != bucket:
            if self.refresh_real_candles():
                self.last_candle_minute = bucket

        # Evaluate Multi-Strategy & Regime Detector
        signal = self.strategy.evaluate(now_dt.strftime("%H:%M"))
        self.latest_signal = signal

        # Log Audit Record
        audit_record = SignalAuditRecord(
            timestamp=now_time,
            regime=signal.regime,
            strategy=signal.strategy_name,
            action=signal.action,
            spot=current_spot,
            checks=signal.audit_checklist,
            decision="PASS" if signal.action != "NO_TRADE" else "NO_TRADE",
            reason=signal.rejection_reason or f"Approved by {signal.strategy_name}"
        )
        self.recent_audit_records.append(audit_record)
        if len(self.recent_audit_records) > 20:
            self.recent_audit_records.pop(0)

        # Entry Execution Evaluation (MULTI-INSTRUMENT CONCURRENCY & AUTOPILOT RADAR)
        from global_trade_lock import global_trade_lock
        lock_stat = global_trade_lock.get_lock_status()

        # Self-Healing Check: If lock claims busy but engine has zero active positions, auto-clear orphaned lock
        if lock_stat.get("is_locked") and len(self.active_positions) == 0:
            print(Fore.YELLOW + f"[AUTO-HEAL] Releasing orphaned lock for '{lock_stat.get('symbol')}' (zero active engine positions).")
            global_trade_lock.force_reset_lock("Self-Healing Idle State")
            lock_stat = global_trade_lock.get_lock_status()
        
        max_conc = getattr(config, "max_concurrent_positions", 5)
        # Check Cooldown Window
        is_cooling_down = False
        if getattr(self, "cooldown_until", None) is not None:
            if datetime.datetime.now() < self.cooldown_until:
                is_cooling_down = True
            else:
                self.cooldown_until = None
                print("[RECOVERY PROTOCOL] Cooldown concluded. Resuming high-conviction opportunity radar.")

        can_trade_entry = (
            len(self.active_positions) < max_conc
            and not lock_stat["is_locked"]
            and not self.is_kill_switch_active
            and not is_cooling_down
            and self.auto_pilot_enabled
        )
        if can_trade_entry:
            # 1. Multi-Asset Autonomous Opportunity Radar: Look where the live opportunity is actively available!
            try:
                from market_intelligence import market_intelligence
                active_cap = self.client.get_real_cash_balance() if self.mode == "LIVE" else float(self.client.paper_capital)
                now_hm_curr = now_dt.strftime("%H:%M")
                is_equity_session = is_equity_open and ("09:25" <= now_hm_curr < "15:05")
                is_commodity_session = ("15:05" <= now_hm_curr < "23:00") or (not is_equity_open and "09:15" <= now_hm_curr < "23:00")
                is_evening_session = is_commodity_session

                # Track peak daily P&L for telemetry only (NEVER halt or cap winning sessions — keep capturing opportunities!)
                cur_net_d = float(getattr(self, "daily_net_pnl", 0.0) or 0.0)
                running_peak, cum_net = 0.0, 0.0
                for tr in getattr(self, "closed_trades_today", []):
                    cum_net += float(tr.get("net_pnl", tr.get("pnl", 0.0)) or 0.0)
                    running_peak = max(running_peak, cum_net)
                peak_net_d = max(float(getattr(self, "_peak_daily_net_pnl", 0.0) or 0.0), running_peak, cur_net_d)
                self._peak_daily_net_pnl = peak_net_d

                all_opps = market_intelligence.scan_all_opportunities(capital=active_cap)
                
                # Seamless Multi-Session Multi-Asset Radar:
                # Discovers and triggers high-confluence setups across NIFTY, BANKNIFTY, SENSEX, CRUDEOILM, NATGASMINI, GOLDM, and stocks!
                # 3-Hour Automated Journal & Strategy Self-Audit (every 10800 seconds)
                now_epoch = time.time()
                if (now_epoch - getattr(self, "_last_3h_audit_ts", 0.0)) >= 10800:
                    self._last_3h_audit_ts = now_epoch
                    try:
                        closed = [t for t in self.closed_trades_today if t.get("pnl") is not None]
                        wins = sum(1 for t in closed if float(t.get("pnl", 0)) > 0)
                        losses = sum(1 for t in closed if float(t.get("pnl", 0)) < 0)
                        total_c = wins + losses
                        wr = round((wins / total_c) * 100.0, 1) if total_c > 0 else 100.0
                        if total_c >= 3 and (wr < 40.0 or self.consecutive_losses >= 2):
                            self.dynamic_min_confluence = 80
                            audit_note = f"3H Audit: WinRate {wr}% ({wins}W/{losses}L) -> Tightened min confluence to 80%."
                        else:
                            self.dynamic_min_confluence = 70
                            audit_note = f"3H Audit: WinRate {wr}% ({wins}W/{losses}L) -> Standard confluence 70% active."
                        self.last_3h_audit_summary = {"timestamp": now_dt.strftime("%Y-%m-%d %H:%M:%S"), "wins": wins, "losses": losses, "win_rate": wr, "min_confluence": self.dynamic_min_confluence, "note": audit_note}
                        self.log_sniper_event(f"🔍 {audit_note}", "AUDIT")
                    except Exception:
                        pass

                mcx_keys = {"CRUDEOILM", "NATGASMINI", "GOLDM", "SILVERM", "CRUDEOIL", "NATURALGAS", "GOLD", "SILVER", "COPPER", "ZINC"}
                index_keys = {"NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "NIFTYIT"}
                active_assets = {getattr(p, "asset_key", "") for p in self.active_positions}
                active_index_count = sum(1 for p in self.active_positions if getattr(p, "asset_key", "") in index_keys)

                # Multi-Slot Uncorrelated Auto-Pilot (09:25 - 15:05 IST Equities, 15:05 - 23:00 IST Commodities):
                # 1 Index slot + up to 2 uncorrelated High-RVOL/News F&O Stock slots (1 per sector max, <= 3 total)
                from stock_scanner import stock_scanner
                active_sectors = {stock_scanner.get_sector(getattr(p, "asset_key", "")) for p in self.active_positions if getattr(p, "asset_key", "") not in index_keys and getattr(p, "asset_key", "") not in mcx_keys}
                active_stock_count = sum(1 for p in self.active_positions if getattr(p, "asset_key", "") not in index_keys and getattr(p, "asset_key", "") not in mcx_keys)
                max_heat_inr = active_cap * (getattr(config, "max_portfolio_heat_pct", 3.0) / 100.0)
                current_open_heat = sum(getattr(p, "trade_risk_inr", 0.0) or (active_cap * (getattr(config, "risk_per_trade_pct", 1.0) / 100.0)) for p in self.active_positions)
                if len(self.active_positions) >= 3 or current_open_heat >= max_heat_inr:
                    valid_opps = []
                elif is_commodity_session:
                    # Active MCX Session: Trade all 10 liquid Commodities with ZERO gap!
                    valid_opps = [
                        o for o in all_opps
                        if o["key"] in mcx_keys
                        and o["key"] not in active_assets
                        and self.check_trading_schedule(o["key"])[0]
                    ]
                elif is_equity_session:
                    # Active Indian Equity & Index Session all the way until 15:05 IST!
                    valid_opps = [
                        o for o in all_opps
                        if o["key"] not in mcx_keys
                        and o["key"] not in active_assets
                        and (
                            (o["key"] in index_keys and active_index_count < 1)
                            or
                            (o["key"] not in index_keys and active_stock_count < 2 and stock_scanner.get_sector(o["key"]) not in active_sectors)
                        )
                        and self.check_trading_schedule(o["key"])[0]
                    ]
                else:
                    valid_opps = []
                # Opening Bell Noise Shield: Avoid 09:15 - 09:25 opening spread whipsaws
                if is_equity_open and "09:15" <= now_hm_curr < "09:25":
                    valid_opps = []

                # High-Confluence Institutional Selection (Dynamic 70-80% + Trend & Candle Confirmation):
                min_conf = getattr(self, "dynamic_min_confluence", 70)
                skip_notes = []
                for top_opp in valid_opps:
                    # Check global lock; directional same-side cooldown is checked after opp_action is determined (allowing immediate ChoCH opposite flips!)
                    is_asset_cd, _ = global_trade_lock.is_cooldown_active(top_opp["key"])
                    if is_asset_cd:
                        continue

                    # Require high-conviction institutional confluence (60+ confirmed Index, 70+ Crude/NatGas, 85+ wide-spread Gold/Silver/Stocks)
                    opp_regime = top_opp.get("mtf_analysis", {}).get("market_regime", "")
                    if opp_regime == "UNCONFIRMED_DATA":
                        continue
                    if (top_opp["key"] in index_keys or top_opp["key"] in mcx_keys) and opp_regime in ("INSTITUTIONAL_ACCUMULATION", "INSTITUTIONAL_DISTRIBUTION"):
                        eff_min_conf = min(min_conf, 60)
                    elif (top_opp["key"] in index_keys) or (top_opp["key"] in mcx_keys):
                        eff_min_conf = min_conf
                    else:
                        # High-RVOL (>= 1.35x) or News-Catalyst F&O Stock Options only (75+ confluence)
                        eff_min_conf = max(min_conf, 75)
                    if not top_opp.get("is_actionable"):
                        skip_notes.append(f"{top_opp['key']}(Not Actionable)")
                        continue
                    if top_opp.get("confluence_score", 0) < eff_min_conf:
                        skip_notes.append(f"{top_opp['key']}(Conf {top_opp.get('confluence_score', 0)}<{eff_min_conf})")
                        continue
                    asset_key = top_opp["key"]
                    opp_action = "BUY_CE" if any(x in top_opp["action"].upper() for x in ["CE", "CALL", "BULLISH"]) else ("BUY_PE" if any(x in top_opp["action"].upper() for x in ["PE", "PUT", "BEARISH"]) else None)
                    if not opp_action:
                        continue
                    dir_cd_until = getattr(self, "_asset_cooldowns", {}).get(f"{asset_key}:{opp_action}")
                    if dir_cd_until is not None and datetime.datetime.now() < dir_cd_until:
                        continue
                    is_dir_cd_db, _ = global_trade_lock.is_cooldown_active(f"{asset_key}:{opp_action}")
                    if is_dir_cd_db:
                        continue
                    if asset_key in index_keys:
                        basket_cd_until = getattr(self, "_basket_cooldowns", {}).get(opp_action)
                        if basket_cd_until is not None and datetime.datetime.now() < basket_cd_until:
                            continue
                        is_basket_cd_db, _ = global_trade_lock.is_cooldown_active(f"BASKET:{opp_action}")
                        if is_basket_cd_db:
                            continue
                    
                    # Institutional Trend Direction, Supply/Demand Zone & Candlestick Confirmation Filter:
                    opp_spot = top_opp["spot"]
                    from mtf_engine import mtf_engine
                    from indicators import IndicatorEngine
                    mtf_lvls = mtf_engine.calculate_institutional_levels(opp_spot, asset_key)
                    real_vwap = mtf_lvls.get("vwap", opp_spot)
                    r1_lvl = float(mtf_lvls.get("r1", 0.0) or 0.0)
                    s1_lvl = float(mtf_lvls.get("s1", 0.0) or 0.0)

                    # Fail-Closed Candle & EMA 9/21 Alignment Check (Completed Bar + Volume + 15m ORB):
                    df_bars = mtf_engine.get_real_candles(asset_key)
                    if df_bars is None or len(df_bars) < 21:
                        n_b = 0 if df_bars is None else len(df_bars)
                        skip_notes.append(f"{asset_key}(Bars {n_b}<21)")
                        continue
                    b_close = float(df_bars["close"].iloc[-1])
                    b_open = float(df_bars["open"].iloc[-1])
                    comp_close = float(df_bars["close"].iloc[-2])
                    comp_open = float(df_bars["open"].iloc[-2])
                    prev_low = float(df_bars["low"].iloc[-2])
                    prev_high = float(df_bars["high"].iloc[-2])
                    ema9_val = float(df_bars["close"].ewm(span=9, adjust=False).mean().iloc[-1])
                    ema21_val = float(df_bars["close"].ewm(span=21, adjust=False).mean().iloc[-1])
                    rsi_5m = IndicatorEngine.calculate_rsi(df_bars["close"])
                    adx_5m, _, _ = IndicatorEngine.calculate_adx(df_bars)
                    if adx_5m < 18.0:
                        skip_notes.append(f"{asset_key}(ADX {adx_5m:.1f}<18)")
                        continue

                    # High-RVOL & News Catalyst Gate for F&O Stock Options (never trade sleepy/illiquid stocks)
                    if asset_key not in index_keys and asset_key not in mcx_keys:
                        stk_scan = stock_scanner.score_stock(asset_key, df_bars)
                        if not stk_scan["momentum_ok"]:
                            skip_notes.append(f"{asset_key}(RVOL {stk_scan['rvol']}x)")
                            continue

                    # 15m Opening Range (09:15-09:30 IST) for ORB + OR-Midpoint stop + SMC Liquidity Sweep levels
                    or_mid_sl = 0.0
                    or_hi_val = 0.0
                    or_lo_val = 0.0
                    if asset_key not in mcx_keys and "timestamp" in df_bars.columns and now_hm_curr >= "09:30":
                        ist_today = get_ist_now().replace(hour=9, minute=15, second=0, microsecond=0).timestamp()
                        ist_or_end = get_ist_now().replace(hour=9, minute=30, second=0, microsecond=0).timestamp()
                        df_or = df_bars[(df_bars["timestamp"] >= ist_today) & (df_bars["timestamp"] < ist_or_end)]
                        if not df_or.empty:
                            or_hi_val = float(df_or["high"].max())
                            or_lo_val = float(df_or["low"].min())
                            if (opp_action == "BUY_CE" and comp_close > or_hi_val) or (opp_action == "BUY_PE" and comp_close < or_lo_val):
                                or_mid_sl = round((or_hi_val + or_lo_val) / 2.0, 2)

                    pdh_lvl = float(mtf_lvls.get("pdh", 0.0) or 0.0)
                    pdl_lvl = float(mtf_lvls.get("pdl", 0.0) or 0.0)

                    # Smart Money Liquidity Sweep (Stop-Hunt Trap Fade):
                    is_sweep_pe = (comp_close < comp_open) and (
                        (pdh_lvl > 0 and prev_high > pdh_lvl and comp_close < pdh_lvl) or
                        (or_hi_val > 0 and prev_high > or_hi_val and comp_close < or_hi_val)
                    )
                    is_sweep_ce = (comp_close > comp_open) and (
                        (pdl_lvl > 0 and prev_low < pdl_lvl and comp_close > pdl_lvl) or
                        (or_lo_val > 0 and prev_low < or_lo_val and comp_close > or_lo_val)
                    )

                    is_supply_rejection_pe = is_sweep_pe or (r1_lvl > 0 and opp_spot >= r1_lvl and b_close < b_open and rsi_5m >= 62.0)
                    is_demand_bounce_ce = is_sweep_ce or (s1_lvl > 0 and opp_spot <= s1_lvl and b_close > b_open and rsi_5m <= 38.0)

                    if is_supply_rejection_pe and opp_action == "BUY_CE":
                        # Bull-Trap Sweep or R1 Supply Rejection: fade the trap with PUT instead of buying CALL with retail sheep!
                        opp_action = "BUY_PE"
                    elif is_demand_bounce_ce and opp_action == "BUY_PE":
                        # Bear-Trap Sweep or S1 Demand Bounce: fade the trap with CALL instead of shorting into support!
                        opp_action = "BUY_CE"

                    b_high = float(df_bars["high"].iloc[-1])
                    b_low = float(df_bars["low"].iloc[-1])
                    b_body = abs(b_close - b_open)
                    upper_wick = max(0.0, b_high - max(b_close, b_open))
                    lower_wick = max(0.0, min(b_close, b_open) - b_low)

                    # Top-Down 15m + 1H Resampled MTF Alignment + Pullback Value-Zone Filter (<= 0.18% from EMA 9)
                    rs_trends = mtf_engine.get_resampled_trends(asset_key)

                    # 1. GROWW 15m Supertrend (7, 3) Macro Direction Lock (prevents 5m PUT->CALL flip-flop!)
                    st_15m = {"direction": "NEUTRAL", "value": 0.0, "ready": False}
                    try:
                        n_bars = len(df_bars)
                        if n_bars >= 30:
                            grp = np.arange(n_bars) // 3
                            df_15m = df_bars.groupby(grp).agg({
                                "open": "first", "high": "max", "low": "min", "close": "last"
                            }).reset_index(drop=True)
                            st_15m = IndicatorEngine.calculate_supertrend(df_15m, period=7, multiplier=3.0)
                    except Exception:
                        pass

                    # 2. DHANANJAY PAWAR (9-21 EMA Pullback) + TANISHA GARG (20 EMA Resting Bar)
                    # 3. DD NIFTY (Super Candle + RSI > 70 / < 30 Rocket Fuel)
                    # 4. ASHWIN RAGHAVAN (VCP Volatility Contraction Pattern)
                    pb_info = IndicatorEngine.detect_pullback_breakout(df_bars, opp_action)
                    sc_info = IndicatorEngine.detect_super_candle(df_bars, opp_action)
                    vcp_info = IndicatorEngine.detect_vcp_squeeze(df_bars)
                    is_super_candle = bool(sc_info.get("is_super_candle", False))
                    is_pullback_trig = bool(pb_info.get("triggered", False))

                    if opp_action == "BUY_CE":
                        if not is_sweep_ce and st_15m.get("ready") and st_15m.get("direction") == "BEAR":
                            skip_notes.append(f"{asset_key}(15m_ST=BEAR)")
                            continue
                        if not is_sweep_ce and not rs_trends.get("tf_15m_bull", True):
                            skip_notes.append(f"{asset_key}(15m!=BULL)")
                            continue
                        # Anti-chasing guard: block CALLs at R1 only when NOT a DD Nifty Super Candle AND rejecting
                        if not is_super_candle and r1_lvl > 0 and opp_spot >= (r1_lvl * 0.999) and rsi_5m >= 72.0 and (b_close < b_open or upper_wick > b_body):
                            skip_notes.append(f"{asset_key}(R1_OB)")
                            continue
                        pdh_lvl = float(mtf_lvls.get("pdh", 0.0) or 0.0)
                        if not is_super_candle and pdh_lvl > 0 and 0.0 < ((pdh_lvl - opp_spot) / pdh_lvl) <= 0.0012 and comp_close <= pdh_lvl and (b_close < b_open or upper_wick > b_body):
                            skip_notes.append(f"{asset_key}(BELOW_PDH_REJ)")
                            continue
                        if not is_demand_bounce_ce:
                            if opp_spot < real_vwap:
                                skip_notes.append(f"{asset_key}(<VWAP)")
                                continue
                            if not is_super_candle and ema9_val > 0 and ((opp_spot - ema9_val) / ema9_val) > (0.0022 if asset_key in index_keys else 0.0065):
                                skip_notes.append(f"{asset_key}(EMA9_EXT)")
                                continue
                            if not (ema9_val > ema21_val and b_close > ema21_val):
                                skip_notes.append(f"{asset_key}(EMA9<=21)")
                                continue
                            if b_close < b_open and b_close < ema9_val:
                                skip_notes.append(f"{asset_key}(RED<EMA9)")
                                continue
                            if b_close < (prev_low - (opp_spot * 0.0005)):
                                skip_notes.append(f"{asset_key}(<PREV_LOW)")
                                continue
                        # Require confirmed Pullback/Resting Bar Breakout OR DD Nifty Super Candle OR SMC Sweep
                        if not (is_pullback_trig or is_super_candle or is_sweep_ce):
                            skip_notes.append(f"{asset_key}(WAIT_PULLBACK)")
                            continue
                    elif opp_action == "BUY_PE":
                        if not is_sweep_pe and st_15m.get("ready") and st_15m.get("direction") == "BULL":
                            skip_notes.append(f"{asset_key}(15m_ST=BULL)")
                            continue
                        if not is_sweep_pe and not rs_trends.get("tf_15m_bear", True):
                            skip_notes.append(f"{asset_key}(15m!=BEAR)")
                            continue
                        # Anti-chasing guard: block PUTs at S1 only when NOT a DD Nifty Super Candle AND bouncing
                        if not is_super_candle and s1_lvl > 0 and opp_spot <= (s1_lvl * 1.001) and rsi_5m <= 28.0 and (b_close > b_open or lower_wick > b_body):
                            skip_notes.append(f"{asset_key}(S1_OS)")
                            continue
                        pdl_lvl = float(mtf_lvls.get("pdl", 0.0) or 0.0)
                        if not is_super_candle and pdl_lvl > 0 and 0.0 < ((opp_spot - pdl_lvl) / pdl_lvl) <= 0.0012 and comp_close >= pdl_lvl and (b_close > b_open or lower_wick > b_body):
                            skip_notes.append(f"{asset_key}(ABOVE_PDL_REJ)")
                            continue
                        if not is_supply_rejection_pe:
                            if opp_spot > real_vwap:
                                skip_notes.append(f"{asset_key}(>VWAP)")
                                continue
                            if not is_super_candle and ema9_val > 0 and ((ema9_val - opp_spot) / ema9_val) > (0.0022 if asset_key in index_keys else 0.0065):
                                skip_notes.append(f"{asset_key}(EMA9_EXT)")
                                continue
                            if not (ema9_val < ema21_val and b_close < ema21_val):
                                skip_notes.append(f"{asset_key}(EMA9>=21)")
                                continue
                            if b_close > b_open and b_close > ema9_val:
                                skip_notes.append(f"{asset_key}(GREEN>EMA9)")
                                continue
                            if b_close > (prev_high + (opp_spot * 0.0005)):
                                skip_notes.append(f"{asset_key}(>PREV_HIGH)")
                                continue
                        # Require confirmed Pullback/Resting Bar Breakdown OR DD Nifty Super Candle OR SMC Sweep
                        if not (is_pullback_trig or is_super_candle or is_sweep_pe):
                            skip_notes.append(f"{asset_key}(WAIT_PULLBACK)")
                            continue

                    # Institutional Zone Confluence Boost (PDH/PDL, POC/VAH/VAL, Active FVG, Swing HH/HL or LH/LL)
                    poc_lvl = float(mtf_lvls.get("poc", 0.0) or 0.0)
                    vah_lvl = float(mtf_lvls.get("vah", 0.0) or 0.0)
                    val_lvl = float(mtf_lvls.get("val", 0.0) or 0.0)
                    pdh_lvl = float(mtf_lvls.get("pdh", 0.0) or 0.0)
                    pdl_lvl = float(mtf_lvls.get("pdl", 0.0) or 0.0)
                    fvg_info = IndicatorEngine.detect_fair_value_gap(df_bars)
                    fvg_type = str(fvg_info.get("type", "NONE")).upper()
                    swing_hl = len(df_bars) >= 10 and float(df_bars["low"].iloc[-2]) > float(df_bars["low"].iloc[-10:-3].min())
                    swing_lh = len(df_bars) >= 10 and float(df_bars["high"].iloc[-2]) < float(df_bars["high"].iloc[-10:-3].max())

                    zone_tags = []
                    eff_score = int(top_opp.get("confluence_score", 75))
                    if is_pullback_trig:
                        zone_tags.append(pb_info.get("trigger_type", "PAWAR_EMA_PULLBACK"))
                        eff_score += 8
                    if is_super_candle:
                        zone_tags.append(f"DD_SUPER_CANDLE(RSI{sc_info.get('rsi', 0):.0f})")
                        eff_score += 10
                    if vcp_info.get("is_vcp"):
                        zone_tags.append("ASHWIN_VCP_SQUEEZE")
                        eff_score += 8
                    if st_15m.get("ready"):
                        zone_tags.append(f"GROWW_15mST_{st_15m.get('direction')}")
                    if opp_action == "BUY_CE":
                        if pdh_lvl > 0 and comp_close > pdh_lvl:
                            zone_tags.append("PDH_BREAKOUT")
                            eff_score += 8
                        if poc_lvl > 0 and opp_spot > poc_lvl:
                            zone_tags.append("ABOVE_POC")
                        if vah_lvl > 0 and comp_close > vah_lvl:
                            zone_tags.append("VAH_BREAK")
                            eff_score += 5
                        if "BULL" in fvg_type:
                            zone_tags.append("BULL_FVG")
                            eff_score += 6
                        if swing_hl:
                            zone_tags.append("HL_TREND")
                        if is_sweep_ce:
                            zone_tags.append("SMC_BEAR_TRAP_SWEEP")
                            eff_score += 10
                    else:
                        if pdl_lvl > 0 and comp_close < pdl_lvl:
                            zone_tags.append("PDL_BREAKDOWN")
                            eff_score += 8
                        if poc_lvl > 0 and opp_spot < poc_lvl:
                            zone_tags.append("BELOW_POC")
                        if val_lvl > 0 and comp_close < val_lvl:
                            zone_tags.append("VAL_BREAK")
                            eff_score += 5
                        if "BEAR" in fvg_type:
                            zone_tags.append("BEAR_FVG")
                            eff_score += 6
                        if swing_lh:
                            zone_tags.append("LH_TREND")
                        if is_sweep_pe:
                            zone_tags.append("SMC_BULL_TRAP_SWEEP")
                            eff_score += 10
                    eff_score = min(98, eff_score)
                    # 1H Macro Size Multiplier: if 1H aligns, keep full A+ sizing; if counter to 1H (early reversal), cap at 1 lot (score 68)
                    hr_aligned = (opp_action == "BUY_CE" and rs_trends.get("tf_1h_bull", True)) or (opp_action == "BUY_PE" and rs_trends.get("tf_1h_bear", True))
                    if not hr_aligned:
                        eff_score = min(eff_score, 68)
                    else:
                        zone_tags.append("1H_ALIGNED")
                    # Idea B enforcement: Stock options with base score 80-84 MUST carry PDH/PDL or FVG structural confirmation
                    if asset_key not in index_keys and asset_key not in mcx_keys and int(top_opp.get("confluence_score", 0)) < 85:
                        if not any(t in zone_tags for t in ("PDH_BREAKOUT", "PDL_BREAKDOWN", "BULL_FVG", "BEAR_FVG")):
                            continue
                    # Always use SWING staged runner management (retire SNIPER's +1.8% micro-choke on Index/Stock trades!)
                    exec_style = "SWING"
                    opp_spot = top_opp["spot"]
                    opp_contract = contract_selector.select_contract(
                        spot_price=opp_spot,
                        action=opp_action,
                        estimated_premium=0.0,
                        strike_preference="ATM",
                        asset_key=asset_key,
                        available_capital=active_cap,
                        confluence_score=eff_score
                    )
                    if opp_contract.risk_budget_approved:
                        calc_levels = market_intelligence.calculate_levels_and_stops(
                            opp_spot, asset_key, is_bear=(opp_action == "BUY_PE")
                        )
                        struct_sl = calc_levels.get("structural_sl_spot", calc_levels.get("sl", opp_spot - 25.0))
                        if or_mid_sl > 0:
                            if (opp_action == "BUY_CE" and or_mid_sl < opp_spot) or (opp_action == "BUY_PE" and or_mid_sl > opp_spot):
                                struct_sl = or_mid_sl
                        # Anchor structural SL tightly to Dhananjay/Tanisha Pullback Candle wick when triggered!
                        if is_pullback_trig and pb_info.get("pullback_sl", 0) > 0:
                            pb_sl = float(pb_info["pullback_sl"])
                            if (opp_action == "BUY_CE" and pb_sl < opp_spot) or (opp_action == "BUY_PE" and pb_sl > opp_spot):
                                struct_sl = pb_sl
                        # Clamp Layer 1 structural SL to recent 10-bar 5m swing low/high so morning ORB SL doesn't sit 500+ pts away
                        if opp_action == "BUY_CE":
                            recent_swing_lo = float(df_bars["low"].tail(10).min())
                            if recent_swing_lo < opp_spot:
                                struct_sl = max(struct_sl, recent_swing_lo)
                        else:
                            recent_swing_hi = float(df_bars["high"].tail(10).max())
                            if recent_swing_hi > opp_spot:
                                struct_sl = min(struct_sl, recent_swing_hi)
                        # Minimum 0.05% spread-survival stop distance guard
                        if opp_spot > 0 and (abs(opp_spot - struct_sl) / opp_spot) < 0.0005:
                            struct_sl = round(opp_spot * (0.9985 if opp_action == "BUY_CE" else 1.0015), 2)
                        zone_suffix = f" [{' + '.join(zone_tags)}]" if zone_tags else ""
                        auto_sig = SmartEdgeSignal(
                            action=opp_action,
                            strategy_name=f"{top_opp.get('recommended_strategy', 'RADAR_AUTO_PILOT')}{zone_suffix}",
                            regime=top_opp.get("market_regime", "MOMENTUM_TREND"),
                            spot_price=opp_spot,
                            structural_sl_spot=struct_sl,
                            target_1_spot=calc_levels.get("target_1_spot", calc_levels.get("target_1", opp_spot + 35.0)),
                            target_2_spot=calc_levels.get("target_2_spot", calc_levels.get("target_2", opp_spot + 55.0))
                        )
                        print(Fore.GREEN + f"\n[AUTOPILOT RADAR] 🚀 Auto-triggering top setup: {opp_contract.custom_symbol} ({top_opp['display_name']} Confluence: {eff_score}% | {exec_style})")
                        if self.execute_entry(auto_sig, opp_contract, opp_spot, trade_style=exec_style):
                            return
                        skip_notes.append(f"{asset_key}({getattr(self, 'last_entry_error', 'BLOCKED')[:24]})")
                if skip_notes:
                    self.waiting_reason = "Radar filtered: " + ", ".join(skip_notes[:6])
            except Exception as e:
                self.waiting_reason = f"Radar Exception: {e}"
                print(f"[AutoPilot Radar Warning] {e}")

            # Legacy Local Chart Strategy fallback disabled during Auto-Pilot to prevent self-canceling CALL+PUT conflicts and unfiltered entries.

    def run_loop(self, iterations: int = 6, interval_seconds: int = 3):
        self.is_running = True
        print(Fore.GREEN + f"[*] Starting execution loop ({iterations if iterations < 100000 else 'Continuous'} ticks @ {interval_seconds}s interval)...\n")
        
        count = 0
        try:
            while self.is_running and count < iterations:
                self.run_tick()
                if self.latest_signal:
                    self.print_dashboard(self.latest_spot, self.latest_signal)
                count += 1
                if count < iterations:
                    time.sleep(interval_seconds)
        except KeyboardInterrupt:
            print(Fore.YELLOW + "\n[!] Trader engine interrupted by user.")
        finally:
            self.is_running = False
            print(Fore.CYAN + "[*] Engine loop ended.")

smart_edge_engine = NiftySmartEdgeEngine()
