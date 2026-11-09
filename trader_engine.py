import time
import datetime
from dataclasses import dataclass
from typing import Optional
from colorama import init, Fore, Style
from tabulate import tabulate

from config import config
from dhan_client import DhanTraderClient
from scrip_master import ScripMaster
from rms import RiskManager
from strategy import NiftyIntradayStrategy, TradeSignal
from notifier import notifier
from journal import journal

init(autoreset=True)

@dataclass
class ActivePosition:
    security_id: str
    symbol: str
    option_type: str        # 'CE' or 'PE'
    strike_price: float
    quantity: int
    entry_price: float
    current_price: float
    stop_loss: float
    target: float
    highest_price: float
    signal_time: str
    order_executed_time: str
    expiry_date: str = ""
    pnl: float = 0.0

class DhanNiftyTraderEngine:
    def __init__(self, mode: Optional[str] = None):
        self.mode = mode or config.trading_mode
        self.client = DhanTraderClient(mode=self.mode)
        self.scrip_master = ScripMaster()
        self.rms = RiskManager()
        self.strategy = NiftyIntradayStrategy()
        
        self.active_position: Optional[ActivePosition] = None
        self.trade_history = []
        self.is_running = False

    def initialize(self):
        print(Fore.CYAN + "=" * 65)
        print(Fore.CYAN + " [+] DHAN NIFTY ALGO TRADING ENGINE - INITIALIZING [+]")
        print(Fore.CYAN + "=" * 65)
        mode_str = "[PAPER TRADING (Safe Simulation)]" if self.mode == "PAPER" else "[LIVE BROKER EXECUTION]"
        print(f"Mode: {mode_str}")
        print(f"Lot Size: {config.lot_size} | Max Daily Loss: Rs.{config.max_daily_loss} | Target Daily Profit: Rs.{config.max_daily_profit}")
        
        # Load Scrip Master
        self.scrip_master.load_master()
        
        # Check Account Limits
        funds = self.client.get_fund_limits()
        print(f"[Account] Balance / Limit Status: {funds.get('data', funds)}")
        print(Fore.GREEN + "[*] System Initialization Complete.\n")

    def simulate_opening_range_if_needed(self, spot: float):
        if not self.strategy.orb_formed:
            # For demonstration / live initialization, seed opening range
            high = spot + 35.0
            low = spot - 35.0
            self.strategy.update_opening_range(high, low)
            # Add synthetic baseline candles
            for i in range(25):
                t_str = f"09:{15+i}:00"
                p = spot + (i * 0.5)
                self.strategy.add_candle(t_str, p - 2, p + 3, p - 3, p)

    def check_position_exit_conditions(self, current_spot: float) -> tuple[bool, str]:
        if not self.active_position:
            return False, ""

        pos = self.active_position
        refreshed_p = self.client.get_option_ltp(
            pos.security_id, current_spot, pos.strike_price, pos.option_type, pos.expiry_date
        )
        if refreshed_p > 0.05:
            pos.current_price = refreshed_p
        elif pos.current_price <= 0.05:
            last_flashed = self.client.get_last_flashed_price(pos.security_id, [pos.symbol])
            pos.current_price = last_flashed if last_flashed > 0.05 else pos.entry_price
        pos.pnl = (pos.current_price - pos.entry_price) * pos.quantity

        # Track high watermark for trailing SL
        if pos.current_price > pos.highest_price:
            pos.highest_price = pos.current_price
            
            # Trail Stop Loss
            profit_points = pos.highest_price - pos.entry_price
            trail_step = (config.trailing_sl_percent / 100.0) * pos.entry_price
            if profit_points >= trail_step * 2:
                new_sl = round(pos.highest_price - trail_step, 2)
                if new_sl > pos.stop_loss:
                    pos.stop_loss = new_sl
                    print(Fore.YELLOW + f"[Trailing SL] Trailed SL up to Rs.{pos.stop_loss:.2f}")

        # 1. Stop Loss Hit
        if pos.current_price <= pos.stop_loss:
            return True, f"Stop Loss Hit @ Rs.{pos.current_price:.2f} (SL: Rs.{pos.stop_loss:.2f})"

        # 2. Target Hit
        if pos.current_price >= pos.target:
            return True, f"Target Reached @ Rs.{pos.current_price:.2f} (Target: Rs.{pos.target:.2f})"

        # 3. Market Square-Off Cutoff
        now_str = datetime.datetime.now().strftime("%H:%M")
        if now_str >= config.square_off_time:
            return True, f"Intraday Cutoff Time ({config.square_off_time}) reached - Auto Square-off"

        return False, ""

    def execute_exit(self, reason: str):
        if not self.active_position:
            return

        pos = self.active_position
        print(Fore.MAGENTA + f"\n[EXIT ORDER] Exiting {pos.symbol} | Reason: {reason}")
        res = self.client.place_order(
            security_id=pos.security_id,
            trading_symbol=pos.symbol,
            transaction_type="SELL",
            quantity=pos.quantity,
            price=pos.current_price,
            order_type="MARKET",
            product_type="INTRA"
        )
        exit_time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Record to Persistent Trading Journal & Experience Log with full Tax/Brokerage Audit
        charges = journal.record_completed_trade(
            symbol=pos.symbol,
            option_type=pos.option_type,
            action=f"BUY_{pos.option_type}",
            quantity=pos.quantity,
            entry_price=pos.entry_price,
            exit_price=pos.current_price,
            signal_time=pos.signal_time,
            order_executed_time=pos.order_executed_time,
            exit_time=exit_time_str,
            exit_reason=reason,
            mode=self.mode
        )

        # Update RMS with true Net P&L (after taxes & charges)
        self.rms.record_trade_exit(charges.net_pnl)

        # Send Mobile Push Notification
        notifier.notify_trade_exit(
            symbol=pos.symbol,
            reason=reason,
            exit_price=pos.current_price,
            entry_price=pos.entry_price,
            qty=pos.quantity,
            pnl=charges.net_pnl,
            net_pnl=self.rms.realized_pnl
        )

        self.trade_history.append({
            "symbol": pos.symbol,
            "type": pos.option_type,
            "entry": pos.entry_price,
            "exit": pos.current_price,
            "qty": pos.quantity,
            "pnl": pnl,
            "reason": reason
        })
        self.active_position = None

    def execute_entry(self, signal: TradeSignal, current_spot: float):
        can_trade, reason = self.rms.can_take_trade()
        if not can_trade:
            print(Fore.YELLOW + f"[Trade Rejected by RMS] {reason}")
            return

        option_type = "CE" if signal.action == "BUY_CE" else "PE"
        contract = self.scrip_master.get_nifty_option(spot_price=current_spot, option_type=option_type, strike_offset=0)
        
        ltp = self.client.get_option_ltp(
            contract["security_id"], current_spot, contract["strike_price"], option_type, contract["expiry"]
        )
        qty = config.lot_size * config.max_lots

        # Calculate Risk/Reward
        sl_points = round(ltp * (config.sl_percent / 100.0), 2)
        target_points = round(ltp * (config.target_percent / 100.0), 2)
        sl_price = round(ltp - sl_points, 2)
        target_price = round(ltp + target_points, 2)

        display_name = contract["custom_symbol"]
        print(Fore.CYAN + f"\n[ENTRY ORDER] {signal.action} -> {display_name} (ID: {contract['security_id']})")
        print(f"  Entry Est: Rs.{ltp:.2f} | SL: Rs.{sl_price:.2f} (-{config.sl_percent}%) | Target: Rs.{target_price:.2f} (+{config.target_percent}%)")

        sig_time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        res = self.client.place_order(
            security_id=contract["security_id"],
            trading_symbol=contract["trading_symbol"],
            transaction_type="BUY",
            quantity=qty,
            price=ltp,
            order_type="MARKET",
            product_type="INTRA"
        )

        exec_time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if res.get("status") == "success":
            self.active_position = ActivePosition(
                security_id=contract["security_id"],
                symbol=display_name,
                option_type=option_type,
                strike_price=contract["strike_price"],
                quantity=qty,
                entry_price=ltp,
                current_price=ltp,
                stop_loss=sl_price,
                target=target_price,
                highest_price=ltp,
                signal_time=sig_time_str,
                order_executed_time=exec_time_str,
                expiry_date=contract["expiry"]
            )

            # Send Mobile Push Notification
            notifier.notify_trade_entry(
                symbol=display_name,
                action=signal.action,
                qty=qty,
                entry_price=ltp,
                sl=sl_price,
                target=target_price,
                spot=current_spot
            )

    def print_dashboard(self, spot: float, signal: TradeSignal):
        now = datetime.datetime.now().strftime("%H:%M:%S")
        status_lines = [
            ["Time / Mode", f"{now} | {self.mode}"],
            ["Nifty Spot", f"Rs.{spot:.2f}"],
            ["ORB Range", f"[{self.strategy.orb_low:.1f} - {self.strategy.orb_high:.1f}]" if self.strategy.orb_formed else "Forming..."],
            ["Signal", f"{signal.action} ({signal.reason})"],
            ["RMS Status", f"Trades: {self.rms.trade_count}/{config.max_trades_per_day} | Net P&L: Rs.{self.rms.realized_pnl:+.2f}"],
        ]

        if self.active_position:
            pos = self.active_position
            pnl_color = Fore.GREEN if pos.pnl >= 0 else Fore.RED
            status_lines.append(["Active Position", f"{pos.symbol} ({pos.quantity} Qty)"])
            status_lines.append(["Position P&L", f"{pnl_color}Rs.{pos.pnl:+.2f} (LTP: Rs.{pos.current_price:.2f} | Entry: Rs.{pos.entry_price:.2f})"])
            status_lines.append(["SL / Target", f"SL: Rs.{pos.stop_loss:.2f} | Target: Rs.{pos.target:.2f}"])
        else:
            status_lines.append(["Active Position", "None (Waiting for valid edge setup)"])

        print("\n" + tabulate(status_lines, tablefmt="rounded_grid"))

    def run_tick(self):
        current_spot = self.client.get_spot_ltp()
        self.simulate_opening_range_if_needed(current_spot)

        # Check Active Position Exits First
        if self.active_position:
            should_exit, reason = self.check_position_exit_conditions(current_spot)
            if should_exit:
                self.execute_exit(reason)

        # Evaluate Entry Signals if No Active Position
        signal = self.strategy.evaluate_signal(current_spot, datetime.datetime.now().strftime("%H:%M:%S"))
        if not self.active_position and signal.action in ["BUY_CE", "BUY_PE"]:
            self.execute_entry(signal, current_spot)

        self.print_dashboard(current_spot, signal)

    def run_loop(self, iterations: int = 10, interval_seconds: int = 3):
        self.is_running = True
        print(Fore.GREEN + f"\n[>] Starting Trading Engine Loop ({iterations} ticks, {interval_seconds}s interval)...\n")
        try:
            for i in range(iterations):
                if not self.is_running or self.rms.is_kill_switch_active:
                    break
                self.run_tick()
                time.sleep(interval_seconds)
        except KeyboardInterrupt:
            print(Fore.YELLOW + "\n[!] Manual Interrupt received. Shutting down gracefully...")
        finally:
            if self.active_position:
                self.execute_exit("System Shutdown Square-Off")
            print(Fore.CYAN + f"\n[Session Finished] Total Trades: {self.rms.trade_count} | Net P&L: Rs.{self.rms.realized_pnl:+.2f}")
