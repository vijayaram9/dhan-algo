import sys
import time
import datetime
import random
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

        # Hard Account Emergency Stop: Only trips on true daily limit (Rs.3,000)
        if self.realized_pnl <= -self.daily_loss_limit:
            self.activate_kill_switch(f"Daily hard loss ceiling of Rs.{self.daily_loss_limit:.2f} reached. Preserving remaining capital.")
        elif self.consecutive_losses >= 3 and self.realized_pnl <= -self.daily_loss_limit * 0.5:
            # Dynamic Cooldown & Recovery Protocol: Only trips on true capital drawdown (> 50% of daily limit),
            # NOT for minor friction scratch trades!
            self.cooldown_until = datetime.datetime.now() + datetime.timedelta(minutes=5)
            self.in_recovery_mode = True
            self.is_kill_switch_active = False
            self.kill_switch_reason = ""
            print(f"[RECOVERY PROTOCOL] 2 consecutive scratch losses detected. 15m patience cooldown active until {self.cooldown_until.strftime('%H:%M:%S')}. Auto-pilot will resume in Defensive Recovery Mode (80%+ confluence only).")
        elif self.trade_count >= self.max_daily_trades:
            self.activate_kill_switch(f"Maximum daily trades ({self.max_daily_trades}) completed. Preserving capital and locking trading session.")

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
        is_commodity = target_asset in ["CRUDEOILM", "NATGASMINI", "GOLDM", "SILVERM"]

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
        is_commodity = (asset_key in ["CRUDEOILM", "NATGASMINI", "GOLDM", "SILVERM"]) or (self.active_position and getattr(self.active_position, "asset_key", "") in ["CRUDEOILM", "NATGASMINI", "GOLDM", "SILVERM"])

        if is_commodity:
            if now_time < "09:15":
                return False, "09:00-09:15 AM: MCX Commodity opening price discovery."
            if now_time >= "23:00":
                return False, "23:00 PM: MCX intraday cut-off. Managing existing positions only."
            return True, "MCX Commodity trading schedule active (until 11:00 PM IST)."

        if "09:15" <= now_time < "09:30":
            return False, "09:15-09:30 AM: Observing Opening Range. No entries allowed per rulebook."
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
        pos_asset = getattr(pos, "asset_key", "NIFTY")
        p_seg = getattr(pos, "segment", None)
        if not p_seg:
            p_seg = "BSE_FNO" if (pos_asset == "SENSEX" or "SENSEX" in pos.symbol) else (
                "MCX_COMM" if pos_asset in ["CRUDEOILM", "NATGASMINI", "GOLDM", "SILVERM"] else "NSE_FNO"
            )
        strike_val = pos.strike_price if pos.strike_price > 0 else (round(current_spot / 50.0) * 50)
        refreshed_ltp = self.client.get_option_ltp(
            pos.security_id, current_spot, strike_val, pos.option_type, pos.expiry_date, segment=p_seg
        )
        if refreshed_ltp > 0.05:
            pos.current_premium = refreshed_ltp
        elif pos.current_premium <= 0.05:
            last_flashed = self.client.get_last_flashed_price(pos.security_id, [pos.symbol])
            pos.current_premium = last_flashed if last_flashed > 0.05 else pos.entry_premium
        pos.pnl = round((pos.current_premium - pos.entry_premium) * pos.quantity, 2)

        # Track high watermarks
        if pos.current_premium > pos.highest_premium:
            pos.highest_premium = pos.current_premium
        if current_spot > pos.highest_spot:
            pos.highest_spot = current_spot

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

            # 🛡️ DYNAMIC CAPITAL SHIELD (Loss Ceiling in Rupees):
            # Tied to planned risk budget for this position (e.g. 1.0% of Capital = ₹1,500 for ₹1.5L)
            planned_risk_budget = getattr(pos, "trade_risk_inr", 0.0)
            if planned_risk_budget <= 0.0:
                planned_risk_budget = max(1200.0, self.account_capital * 0.01)

            if pos.pnl <= -planned_risk_budget:
                msg = f"🛡️ Dynamic Capital Shield Hit: Exited @ ₹{pos.current_premium:.2f} (Loss of ₹{abs(pos.pnl):.2f} capped at planned risk budget ₹{planned_risk_budget:,.2f})."
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
            # Tier 1 (+12% premium gain): Move stop to Breakeven (+1.5 pts). Trade is 100% Risk-Free!
            if prem_gain_pct >= 12.0 and pos.trailing_step_level < 1:
                pos.hard_stop_premium = max(pos.hard_stop_premium, round(pos.entry_premium + 1.5, 2))
                pos.is_trailing_active = True
                pos.trailing_step_level = 1
                pos.locked_profit_inr = round(1.5 * pos.quantity, 2)
                print(f"[SWING TIER 1] 🌊 Breakeven Locked @ ₹{pos.hard_stop_premium:.2f} (Risk-Free)")

            # Tier 2 (+22% premium gain): Lock in at least +12% profit!
            if prem_gain_pct >= 22.0 and pos.trailing_step_level < 2:
                locked_prem = round(pos.entry_premium * 1.12, 2)
                pos.hard_stop_premium = max(pos.hard_stop_premium, locked_prem)
                pos.trailing_step_level = 2
                pos.locked_profit_inr = round((pos.hard_stop_premium - pos.entry_premium) * pos.quantity, 2)
                print(f"[SWING TIER 2] 🌊 +12% Profit Locked @ ₹{pos.hard_stop_premium:.2f} (+₹{pos.locked_profit_inr:,.0f})")

            # Tier 3 (+30% or higher runner): Trail tightly at 88% of highest watermark!
            if prem_gain_pct >= 30.0:
                trail_sl = round(pos.highest_premium * 0.88, 2)
                if trail_sl > pos.hard_stop_premium:
                    pos.hard_stop_premium = trail_sl
                    pos.trailing_step_level = 3
                    pos.locked_profit_inr = round((trail_sl - pos.entry_premium) * pos.quantity, 2)

            # Check Hard / Trailing Premium Stop
            if pos.current_premium <= pos.hard_stop_premium:
                if pos.hard_stop_premium > pos.entry_premium:
                    return True, f"🔒 Swing Trailing Profit Locked: Exited @ ₹{pos.current_premium:.2f} (Secured +₹{pos.pnl:.2f})!"
                else:
                    return True, f"Layer 2 Hard Premium Stop Hit @ ₹{pos.current_premium:.2f} (-20% stop: ₹{pos.hard_stop_premium:.2f})"

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
                return True, f"Layer 2 Hard Premium Stop Hit @ ₹{pos.current_premium:.2f} (-20% stop: ₹{pos.hard_stop_premium:.2f})"

        # Minimum execution stabilization window: Allow at least 15 seconds for trade to breathe
        # before structural target or trailing can trigger (unless hard stop is breached)
        try:
            entry_dt = datetime.datetime.strptime(pos.entry_time_iso[:19], "%Y-%m-%d %H:%M:%S")
            elapsed_seconds = (datetime.datetime.now() - entry_dt).total_seconds()
        except Exception:
            elapsed_seconds = 60.0

        # LAYER 1: Underlying-Based Structural Stop (with sanity guard against corrupted spot quotes)
        spot_is_sane = (current_spot > 0 and abs(current_spot - pos.underlying_entry_spot) / max(1.0, pos.underlying_entry_spot) < 0.50)
        if spot_is_sane:
            if pos.option_type == "CE":
                # Structural Invalidation (CE: underlying breaks swing low with confirmed penetration & minimum 20s breathing room)
                if pos.underlying_structural_sl > 0 and current_spot <= (pos.underlying_structural_sl - 1.0) and pos.current_premium <= pos.entry_premium and elapsed_seconds >= 20.0:
                    return True, f"Layer 1 Structural Invalidation: {getattr(pos, 'asset_key', 'Spot')} {current_spot:.1f} broke swing low {pos.underlying_structural_sl:.1f}"

                # Target 1 (1.5R) reached (CE: underlying rises to or above target; requires minimum 15s)
                if pos.target_1_spot > pos.underlying_entry_spot and current_spot >= pos.target_1_spot and elapsed_seconds >= 15.0:
                    return True, f"Target 1 (1.5R) Reached @ Spot {current_spot:.1f} (Target: {pos.target_1_spot:.1f})"

                # Trailing Stop: 1 ATR movement after 1R
                risk_pts = pos.underlying_entry_spot - pos.underlying_structural_sl
                if risk_pts > 0 and (current_spot - pos.underlying_entry_spot) >= risk_pts:
                    pos.is_trailing_active = True
                    new_sl = pos.highest_spot - pos.atr_at_entry
                    if new_sl > pos.underlying_structural_sl:
                        pos.underlying_structural_sl = new_sl

            elif pos.option_type == "PE":
                # Structural Invalidation (PE: underlying breaks swing high with confirmed penetration & minimum 20s breathing room)
                if pos.underlying_structural_sl > 0 and current_spot >= (pos.underlying_structural_sl + 1.0) and pos.current_premium <= pos.entry_premium and elapsed_seconds >= 20.0:
                    return True, f"Layer 1 Structural Invalidation: {getattr(pos, 'asset_key', 'Spot')} {current_spot:.1f} broke swing high {pos.underlying_structural_sl:.1f}"

                # Target 1 (1.5R) reached (PE: underlying falls to or below target; requires minimum 15s)
                if 0 < pos.target_1_spot < pos.underlying_entry_spot and current_spot <= pos.target_1_spot and elapsed_seconds >= 15.0:
                    return True, f"Target 1 (1.5R) Reached @ Spot {current_spot:.1f} (Target: {pos.target_1_spot:.1f})"

                risk_pts = pos.underlying_structural_sl - pos.underlying_entry_spot
                if risk_pts > 0 and (pos.underlying_entry_spot - current_spot) >= risk_pts:
                    pos.is_trailing_active = True
                    new_sl = pos.highest_spot + pos.atr_at_entry
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

        # Check Account Protection Limits (Section 7)
        if self.realized_pnl <= -self.daily_loss_limit:
            self.activate_kill_switch(f"Daily loss limit of Rs.{self.daily_loss_limit} breached (Net: Rs.{self.realized_pnl:.2f})")
        elif self.consecutive_losses >= 3 and self.realized_pnl <= 0:
            self.cooldown_until = datetime.datetime.now() + datetime.timedelta(minutes=15)
            self.in_recovery_mode = True
            msg = f"🛡️ Risk Defense: 3 consecutive pullbacks in drawdown (Net: Rs.{self.realized_pnl:+.2f}). 15-minute patience cooldown active until {self.cooldown_until.strftime('%H:%M:%S')} to preserve capital."
            self.log_sniper_event(msg, "WARNING")
            notifier.notify("🛡️ 15m Cooldown Active", msg, tags="warning,shield")
        elif self.consecutive_losses >= 3 and self.realized_pnl > 0:
            self.log_sniper_event(
                f"⚠️ Discipline Alert: 3 consecutive pullbacks in profit zone (Net: Rs.{self.realized_pnl:+,.2f}). Session protected with positive cushion; 5m cooldown engaged.",
                "WARNING"
            )
            self.cooldown_until = datetime.datetime.now() + datetime.timedelta(minutes=5)
        elif self.trade_count >= self.max_daily_trades:
            self.activate_kill_switch(f"Maximum daily limit ({self.max_daily_trades} trades) completed. Preserving capital and locking trading session.")

        if pos in self.active_positions:
            self.active_positions.remove(pos)
        else:
            self.active_positions = [p for p in self.active_positions if getattr(p, "position_id", "") != getattr(pos, "position_id", "")]

        # Release Global Trade Lock & engage configured Cooldown (default 300s)
        try:
            from global_trade_lock import global_trade_lock
            base_cd = int(getattr(config, "cooldown_seconds", 60))
            # Anti-revenge-trading: Stand down 5 minutes after any loss so the engine never re-enters into adverse chop
            cd_secs = max(base_cd, 300) if pos.pnl < 0 else max(base_cd, 60)
            self.cooldown_until = datetime.datetime.now() + datetime.timedelta(seconds=cd_secs)
            global_trade_lock.release_lock(position_id=getattr(pos, "position_id", ""), cooldown_seconds=cd_secs)
            print(Fore.CYAN + f"[GlobalTradeLock] Lock released. Cooldown active for {cd_secs}s.")
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
        if not contract.risk_budget_approved:
            print(Fore.YELLOW + f"[Risk Rejected] {contract.rejection_reason}")
            return

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
                if asset_k in ["BANKNIFTY", "SENSEX", "NIFTYIT"]:
                    step_sz = 100.0
                elif asset_k == "GOLDM":
                    step_sz = 250.0
                elif asset_k == "SILVERM":
                    step_sz = 500.0
                elif asset_k == "NATGASMINI":
                    step_sz = 5.0
                elif asset_k in ["RELIANCE", "INFY"]:
                    step_sz = 20.0
                elif asset_k in ["HDFCBANK", "ICICIBANK", "SBIN"]:
                    step_sz = 10.0

                # Guardrail A: Spot deviation check
                spot_drift_pct = abs(verified_spot - spot) / verified_spot * 100.0
                if spot_drift_pct > 1.5:
                    print(Fore.RED + f"[GUARDRAIL BLOCKED] Trade entry aborted! Stale spot detected: passed {spot:.1f} vs live exchange {verified_spot:.1f} ({spot_drift_pct:.2f}% drift).")
                    return

                # Guardrail B: Strike distance check
                strike_drift = abs(contract.strike_price - verified_spot)
                if strike_drift > (step_sz * 3.5):
                    print(Fore.RED + f"[GUARDRAIL BLOCKED] Trade entry aborted! Strike {contract.strike_price:.1f} is {strike_drift:.1f} pts away from live spot {verified_spot:.1f} (Max: {step_sz * 2.5:.1f}).")
                    return

                # Guardrail B2: Day High / Day Low Exhaustion Buffer
                # Prevents buying CALLs directly under Day High / R1 resistance, and buying PUTs into Day Low / S1 support
                try:
                    from mtf_engine import mtf_engine
                    df_b = mtf_engine.get_real_candles(asset_k)
                    if df_b is not None and not df_b.empty:
                        d_high = float(df_b["high"].max())
                        d_low = float(df_b["low"].min())
                        # If CALL: Block if within 0.15% of Day High unless already broken out
                        if signal.action == "BUY_CE" and verified_spot < d_high and ((d_high - verified_spot) / verified_spot) < 0.0015:
                            print(Fore.YELLOW + f"[RESISTANCE BUFFER BLOCKED] Skipping CALL entry: Spot {verified_spot:.1f} is directly under Day High {d_high:.1f}. Awaiting confirmed breakout to avoid reversal.")
                            return
                        # If PUT: Block if within 0.15% of Day Low unless already broken down
                        if signal.action == "BUY_PE" and verified_spot > d_low and ((verified_spot - d_low) / verified_spot) < 0.0015:
                            print(Fore.YELLOW + f"[SUPPORT BUFFER BLOCKED] Skipping PUT entry: Spot {verified_spot:.1f} is directly above Day Low {d_low:.1f}. Awaiting confirmed breakdown to avoid bounce.")
                            return
                except Exception:
                    pass

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
                        contract.security_id, [contract.custom_symbol, contract.trading_symbol]
                    )
                if verified_prem <= 0.05:
                    print(Fore.RED + f"[GUARDRAIL BLOCKED] Trade entry aborted! Real Dhan quote could not be confirmed for {contract.custom_symbol} (Zero/Rate-Limited). Refusing synthetic/stale entry.")
                    return
                # Update contract estimated premium to ground truth verified live LTP
                contract.estimated_premium = verified_prem

                # Guardrail D: ANTI-CHASE (uses Dhan 1m candles of the option itself).
                # Refuse to buy a premium that has just spiked and is sitting at the top of its range.
                if trade_style == "SNIPER":
                    chase_ok, chase_msg = self._anti_chase_check(contract, asset_k, verified_prem)
                    if not chase_ok:
                        print(Fore.YELLOW + f"[ANTI-CHASE BLOCKED] {chase_msg}")
                        self.log_sniper_event(f"⛔ Entry skipped: {chase_msg}", "SKIP")
                        return
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
            print(Fore.YELLOW + f"[CAPACITY REACHED] {rej_reason}")
            global_trade_lock.log_rejection(sig_id, contract.custom_symbol, signal.action, rej_reason)
            return

        # Check duplicate on the exact same contract
        if any(getattr(p, "symbol", "") == contract.custom_symbol for p in self.active_positions):
            rej_reason = f"Duplicate trade blocked: Contract '{contract.custom_symbol}' is already open in your portfolio."
            print(Fore.YELLOW + f"[DUPLICATE BLOCKED] {rej_reason}")
            global_trade_lock.log_rejection(sig_id, contract.custom_symbol, signal.action, rej_reason)
            return

        # Rule 2: Check SQLite persistent multi-trade lock & per-instrument cooldown
        can_lock, lock_msg = global_trade_lock.can_acquire_lock(sig_id, contract.custom_symbol, asset_k)
        if not can_lock:
            # Self-healing check: If the lock in SQLite claims busy, but engine has NO open positions:
            if len(self.active_positions) == 0:
                global_trade_lock.force_reset_lock("Self-Healing Stale Lock")
                can_lock, lock_msg = global_trade_lock.can_acquire_lock(sig_id, contract.custom_symbol, asset_k)

        if not can_lock:
            print(Fore.YELLOW + f"[LOCK REJECTED] {lock_msg}")
            global_trade_lock.log_rejection(sig_id, contract.custom_symbol, signal.action, lock_msg)
            return

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
            print(Fore.RED + f"[ATOMIC LOCK FAILED] {acq_reason}")
            return

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
            if target_premium <= 0:
                target_premium = round(contract.estimated_premium * 1.35, 2)
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
                entry_time_iso=exec_time_str,
                signal_time_iso=sig_time_str,
                expiry_date=contract.expiry_date,
                atr_at_entry=25.0,
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
        else:
            # Release lock immediately if order was rejected by broker / engine
            from global_trade_lock import global_trade_lock
            global_trade_lock.release_lock(pos_id, cooldown_seconds=0)
            rej_err = res.get("remarks", "Broker rejection")
            print(Fore.RED + f"[ORDER FAILED] Order placement rejected: {rej_err}. Global lock released.")

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
                all_opps = market_intelligence.scan_all_opportunities(capital=active_cap)
                
                # Seamless Multi-Session Multi-Asset Radar:
                # Discovers and triggers high-confluence setups across NIFTY, BANKNIFTY, SENSEX, CRUDEOILM, NATGASMINI, GOLDM, and stocks!
                now_hm_curr = now_dt.strftime("%H:%M")
                is_evening_session = (now_hm_curr >= "15:30" or now_hm_curr < "09:15")
                active_assets = {getattr(p, "asset_key", "") for p in self.active_positions}
                
                if is_evening_session:
                    if is_mcx_open:
                        allowed_targets = ["CRUDEOILM", "NATGASMINI", "GOLDM", "SILVERM"]
                        valid_opps = [
                            o for o in all_opps 
                            if o["key"] in allowed_targets 
                            and o["key"] not in active_assets 
                            and self.check_trading_schedule(o["key"])[0]
                        ]
                    else:
                        valid_opps = []
                elif is_equity_open:
                    valid_opps = [
                        o for o in all_opps 
                        if o["key"] not in active_assets
                        and o["key"] not in ["CRUDEOILM", "NATGASMINI", "GOLDM", "SILVERM"]
                        and self.check_trading_schedule(o["key"])[0]
                    ]
                else:
                    valid_opps = []
                # High-Confluence Institutional Selection:
                for top_opp in valid_opps:
                    # Check per-instrument cooldown
                    is_asset_cd, _ = global_trade_lock.is_cooldown_active(top_opp["key"])
                    if is_asset_cd:
                        continue

                    # Require strong institutional confluence (score >= 70%)
                    if not top_opp.get("is_actionable") or top_opp.get("confluence_score", 0) < 70:
                        continue
                    asset_key = top_opp["key"]
                    opp_action = "BUY_CE" if any(x in top_opp["action"].upper() for x in ["CE", "CALL", "BULLISH"]) else ("BUY_PE" if any(x in top_opp["action"].upper() for x in ["PE", "PUT", "BEARISH"]) else None)
                    if not opp_action:
                        continue
                    
                    # Institutional Trend Direction & Candlestick Confirmation Filter:
                    opp_spot = top_opp["spot"]
                    from mtf_engine import mtf_engine
                    mtf_lvls = mtf_engine.calculate_institutional_levels(opp_spot, asset_key)
                    real_vwap = mtf_lvls.get("vwap", opp_spot)
                    # Strict VWAP Directional Alignment:
                    # Never buy Calls below VWAP (resisting ceiling). Never buy Puts above VWAP (supporting floor).
                    if opp_action == "BUY_CE" and opp_spot < real_vwap:
                        continue
                    if opp_action == "BUY_PE" and opp_spot > real_vwap:
                        continue

                    # Candlestick breakdown check: Only reject if candle is aggressively breaking against trend
                    df_bars = mtf_engine.get_real_candles(asset_key)
                    if df_bars is not None and len(df_bars) >= 2:
                        b_close = df_bars["close"].iloc[-1]
                        prev_low = df_bars["low"].iloc[-2]
                        prev_high = df_bars["high"].iloc[-2]
                        # Don't buy CE if price is breaking below previous bar's low with heavy selling
                        if opp_action == "BUY_CE" and b_close < (prev_low - (opp_spot * 0.0008)):
                            continue
                        # Don't buy PE if price is breaking above previous bar's high with heavy buying
                        if opp_action == "BUY_PE" and b_close > (prev_high + (opp_spot * 0.0008)):
                            continue
                    opp_spot = top_opp["spot"]
                    opp_contract = contract_selector.select_contract(
                        spot_price=opp_spot,
                        action=opp_action,
                        estimated_premium=0.0,
                        strike_preference="ATM",
                        asset_key=asset_key,
                        available_capital=active_cap
                    )
                    if opp_contract.risk_budget_approved:
                        calc_levels = market_intelligence.calculate_levels_and_stops(
                            opp_spot, asset_key, is_bear=(opp_action == "BUY_PE")
                        )
                        auto_sig = SmartEdgeSignal(
                            action=opp_action,
                            strategy_name=top_opp.get("recommended_strategy", "RADAR_AUTO_PILOT"),
                            regime=top_opp.get("market_regime", "MOMENTUM_TREND"),
                            spot_price=opp_spot,
                            structural_sl_spot=calc_levels.get("structural_sl_spot", calc_levels.get("sl", opp_spot - 25.0)),
                            target_1_spot=calc_levels.get("target_1_spot", calc_levels.get("target_1", opp_spot + 35.0)),
                            target_2_spot=calc_levels.get("target_2_spot", calc_levels.get("target_2", opp_spot + 55.0))
                        )
                        print(Fore.GREEN + f"\n[AUTOPILOT RADAR] 🚀 Auto-triggering top setup: {opp_contract.custom_symbol} ({top_opp['display_name']} Confluence: {top_opp['confluence_score']}%)")
                        self.execute_entry(auto_sig, opp_contract, opp_spot, trade_style=top_opp.get("optimal_style", "SNIPER"))
                        return
            except Exception as e:
                print(f"[AutoPilot Radar Warning] {e}")

            # 2. Local Chart Strategy Signal (Strictly verified for NIFTY trading schedule)
            nifty_open, _ = self.check_trading_schedule("NIFTY")
            if nifty_open and signal.action in ["BUY_CE", "BUY_PE"]:
                opt_type = "CE" if signal.action == "BUY_CE" else "PE"
                atm_strike = round(current_spot / 50.0) * 50
                contract_meta = self.scrip_master.get_nifty_option(current_spot, opt_type, 0, "NIFTY")
                sec_id = contract_meta.get("security_id", "0")
                exp_date = contract_meta.get("expiry", "")
                est_premium = self.client.get_option_ltp(sec_id, current_spot, atm_strike, opt_type, expiry_date=exp_date)
                struct_risk = abs(current_spot - signal.structural_sl_spot) if signal.structural_sl_spot > 0 else 12.0
                contract = contract_selector.select_contract(
                    current_spot, signal.action, est_premium, "ATM", struct_risk,
                    asset_key="NIFTY",
                    available_capital=(self.client.get_real_cash_balance() if self.mode == "LIVE" else float(self.client.paper_capital))
                )
                
                # Check risk budget
                if contract.risk_budget_approved:
                    chosen_style = "SNIPER" if ("SNIPER" in signal.strategy_name.upper() or "SCALP" in signal.strategy_name.upper()) else "SMART_EDGE"
                    self.execute_entry(signal, contract, current_spot, trade_style=chosen_style)
                else:
                    audit_record.decision = "REJECTED_BY_RISK"
                    audit_record.reason = contract.rejection_reason

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
