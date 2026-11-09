import os
import sqlite3
import datetime
from typing import List, Dict, Optional
import pandas as pd
from tabulate import tabulate
from colorama import Fore, Style

from tax_calculator import calculate_dhan_options_charges, TradeCharges

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "trading_journal.db")
CSV_PATH = os.path.join(os.path.dirname(__file__), "data", "trading_journal.csv")
EXPERIENCE_LOG_PATH = os.path.join(os.path.dirname(__file__), "data", "EXPERIENCE_LOG.md")
ARCHIVES_DIR = os.path.join(os.path.dirname(__file__), "data", "journal_archives")

class TradingJournal:
    """
    Institutional-Grade Trading Journal:
    - Precise millisecond-level timestamps (Signal, Execution, Exit, PnL Registered)
    - Full Indian Taxation & Brokerage Breakdown (Dhan Brokerage, STT, NSE, GST, Stamp Duty)
    - True Net Take-Home P&L calculation
    - Persistent SQLite, CSV, and Markdown logs
    """
    def __init__(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        os.makedirs(ARCHIVES_DIR, exist_ok=True)
        self.init_db()

    def init_db(self):
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT,
                signal_time TEXT,
                order_executed_time TEXT,
                exit_time TEXT,
                pnl_registered_time TEXT,
                duration_str TEXT,
                symbol TEXT,
                option_type TEXT,
                action TEXT,
                quantity INTEGER,
                entry_price REAL,
                exit_price REAL,
                gross_pnl REAL,
                brokerage REAL,
                stt REAL,
                exchange_charges REAL,
                sebi_charges REAL,
                stamp_duty REAL,
                gst REAL,
                total_charges REAL,
                net_pnl REAL,
                net_pnl_pct REAL,
                exit_reason TEXT,
                mode TEXT,
                trade_rationale TEXT,
                invested_capital REAL
            )
        """)
        # Safe migration for existing DBs
        for col_def in ["trade_rationale TEXT", "invested_capital REAL"]:
            try:
                cursor.execute(f"ALTER TABLE trades ADD COLUMN {col_def}")
            except sqlite3.OperationalError:
                pass
        conn.commit()
        conn.close()

    def clear_all_trades(self):
        """Clears all historical and test trades to restart with a clean slate."""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM trades")
        cursor.execute("DELETE FROM sqlite_sequence WHERE name='trades'")
        conn.commit()
        conn.close()
        try:
            if os.path.exists(CSV_PATH):
                os.remove(CSV_PATH)
            if os.path.exists(EXPERIENCE_LOG_PATH):
                os.remove(EXPERIENCE_LOG_PATH)
        except Exception:
            pass

    def calculate_duration(self, start_iso: str, end_iso: str) -> str:
        try:
            fmt = "%Y-%m-%d %H:%M:%S"
            t_start = datetime.datetime.strptime(start_iso[:19], fmt)
            t_end = datetime.datetime.strptime(end_iso[:19], fmt)
            diff = t_end - t_start
            total_seconds = int(diff.total_seconds())
            mins, secs = divmod(total_seconds, 60)
            hours, mins = divmod(mins, 60)
            if hours > 0:
                return f"{hours}h {mins}m {secs}s"
            return f"{mins}m {secs}s"
        except Exception:
            return "N/A"

    def record_completed_trade(
        self,
        symbol: str,
        option_type: str,
        action: str,
        quantity: int,
        entry_price: float,
        exit_price: float,
        signal_time: str,
        order_executed_time: str,
        exit_time: str,
        exit_reason: str,
        mode: str,
        trade_rationale: str = "",
        invested_capital: float = 0.0
    ) -> TradeCharges:
        now_dt = datetime.datetime.now()
        pnl_registered_time = now_dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        today_str = now_dt.strftime("%Y-%m-%d")
        duration_str = self.calculate_duration(order_executed_time, exit_time)

        # Calculate exact taxes & Dhan charges
        charges = calculate_dhan_options_charges(entry_price, exit_price, quantity)
        if invested_capital <= 0:
            invested_capital = round(entry_price * quantity, 2)

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO trades (
                trade_date, signal_time, order_executed_time, exit_time, pnl_registered_time,
                duration_str, symbol, option_type, action, quantity,
                entry_price, exit_price, gross_pnl, brokerage, stt,
                exchange_charges, sebi_charges, stamp_duty, gst, total_charges,
                net_pnl, net_pnl_pct, exit_reason, mode, trade_rationale, invested_capital
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            today_str, signal_time, order_executed_time, exit_time, pnl_registered_time,
            duration_str, symbol, option_type, action, quantity,
            entry_price, exit_price, charges.gross_pnl, charges.brokerage, charges.stt,
            charges.exchange_charges, charges.sebi_charges, charges.stamp_duty, charges.gst, charges.total_charges,
            charges.net_pnl, charges.net_pnl_pct, exit_reason, mode, trade_rationale, invested_capital
        ))
        conn.commit()
        conn.close()

        # Update CSV & Experience Log
        self.export_csv()
        self.append_experience_log(
            today_str=today_str,
            signal_time=signal_time,
            order_executed_time=order_executed_time,
            exit_time=exit_time,
            pnl_registered_time=pnl_registered_time,
            duration_str=duration_str,
            symbol=symbol,
            action=action,
            quantity=quantity,
            entry_price=entry_price,
            exit_price=exit_price,
            charges=charges,
            exit_reason=exit_reason,
            mode=mode
        )
        return charges

    def archive_current_journal(self) -> Optional[str]:
        """
        Creates a permanent, timestamped snapshot of the current trading journal
        into the dedicated 'data/journal_archives/' directory before any reset.
        Guarantees that trade history and audit records are never lost.
        """
        import shutil
        if not os.path.exists(DB_PATH):
            return None
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM trades")
            count = cursor.fetchone()[0]
            conn.close()
            if count == 0:
                return None

            now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            archive_csv = os.path.join(ARCHIVES_DIR, f"journal_archive_{now_str}.csv")
            archive_db = os.path.join(ARCHIVES_DIR, f"journal_archive_{now_str}.db")

            df = self.get_all_trades_df()
            if not df.empty:
                df.to_csv(archive_csv, index=False)

            shutil.copy2(DB_PATH, archive_db)
            print(f"[TradingJournal] Safely archived {count} trades to {archive_csv}")
            return archive_csv
        except Exception as e:
            print(f"[TradingJournal] Warning: Failed to archive journal: {e}")
            return None

    def clear_all_trades(self):
        """Safely archives current trade records to journal_archives, then resets the active ledger."""
        self.archive_current_journal()
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM trades")
        cursor.execute("DELETE FROM sqlite_sequence WHERE name='trades'")
        conn.commit()
        conn.close()
        try:
            if os.path.exists(CSV_PATH):
                os.remove(CSV_PATH)
            if os.path.exists(EXPERIENCE_LOG_PATH):
                os.remove(EXPERIENCE_LOG_PATH)
        except Exception:
            pass
        self.export_csv()
        return True

    def get_today_summary(self) -> Dict:
        """Restores today's trade count and net P&L after taxes."""
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*), COALESCE(SUM(gross_pnl), 0.0), COALESCE(SUM(total_charges), 0.0), COALESCE(SUM(net_pnl), 0.0)
            FROM trades WHERE trade_date = ?
        """, (today_str,))
        count, gross_pnl, total_charges, net_pnl = cursor.fetchone()
        conn.close()
        return {
            "count": int(count),
            "gross_pnl": float(gross_pnl),
            "total_charges": float(total_charges),
            "net_pnl": float(net_pnl)
        }

    def get_all_trades_df(self) -> pd.DataFrame:
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql_query("SELECT * FROM trades ORDER BY id DESC", conn)
        conn.close()
        return df

    def export_csv(self):
        df = self.get_all_trades_df()
        if not df.empty:
            df.to_csv(CSV_PATH, index=False)

    def append_experience_log(
        self,
        today_str: str,
        signal_time: str,
        order_executed_time: str,
        exit_time: str,
        pnl_registered_time: str,
        duration_str: str,
        symbol: str,
        action: str,
        quantity: int,
        entry_price: float,
        exit_price: float,
        charges: TradeCharges,
        exit_reason: str,
        mode: str
    ):
        result_icon = "🟢 WIN" if charges.net_pnl >= 0 else "🔴 LOSS"
        entry_md = (
            f"\n### {result_icon} | {symbol} ({action}) - {today_str}\n"
            f"- **Execution Timestamps:**\n"
            f"  - 📡 **Signal Detected:** `{signal_time}`\n"
            f"  - ⚡ **Order Executed:** `{order_executed_time}`\n"
            f"  - 🏁 **Order Exited:** `{exit_time}`\n"
            f"  - ⏱️ **Trade Duration:** `{duration_str}`\n"
            f"  - 💾 **P&L Registered In Ledger:** `{pnl_registered_time}`\n"
            f"- **Trade Parameters:**\n"
            f"  - Quantity: `{quantity}` ({quantity // 75} Lots) | Mode: `{mode}`\n"
            f"  - Entry Price: `Rs.{entry_price:.2f}` ➔ Exit Price: `Rs.{exit_price:.2f}`\n"
            f"- **Financial & Tax Breakdown:**\n"
            f"  - **Gross Trade P&L:** `Rs.{charges.gross_pnl:+.2f}`\n"
            f"  - **Brokerage & Taxes Paid:** `Rs.{charges.total_charges:.2f}`\n"
            f"    - *Dhan Brokerage (Buy+Sell):* Rs.{charges.brokerage:.2f}\n"
            f"    - *STT (Securities Transaction Tax):* Rs.{charges.stt:.2f}\n"
            f"    - *NSE Exchange Charges:* Rs.{charges.exchange_charges:.2f}\n"
            f"    - *GST (18%):* Rs.{charges.gst:.2f}\n"
            f"    - *Stamp Duty & SEBI Fee:* Rs.{charges.stamp_duty + charges.sebi_charges:.2f}\n"
            f"  - **Net Take-Home P&L:** **`Rs.{charges.net_pnl:+.2f}` ({charges.net_pnl_pct:+.2f}%)**\n"
            f"- **Exit Reason:** {exit_reason}\n"
            f"---\n"
        )
        if not os.path.exists(EXPERIENCE_LOG_PATH):
            header = "# Nifty Algorithmic Trading Experience Journal\n\nDaily audited log of strategy signals, executions, taxes, and net returns.\n\n---\n"
            with open(EXPERIENCE_LOG_PATH, "w", encoding="utf-8") as f:
                f.write(header)
        with open(EXPERIENCE_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(entry_md)

    def print_performance_analytics(self):
        df = self.get_all_trades_df()
        if df.empty:
            print("\n[Journal] No trades recorded yet. Start the bot to begin logging!")
            return

        total_trades = len(df)
        wins = df[df["net_pnl"] > 0]
        losses = df[df["net_pnl"] <= 0]
        win_count = len(wins)
        loss_count = len(losses)
        win_rate = (win_count / total_trades) * 100.0 if total_trades > 0 else 0.0

        gross_pnl = df["gross_pnl"].sum()
        total_brokerage = df["brokerage"].sum()
        total_stt = df["stt"].sum()
        total_gst = df["gst"].sum()
        total_charges = df["total_charges"].sum()
        net_takehome = df["net_pnl"].sum()

        gross_profit = wins["net_pnl"].sum() if not wins.empty else 0.0
        gross_loss = abs(losses["net_pnl"].sum()) if not losses.empty else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 1.0)

        print(Fore.CYAN + "\n==================================================================")
        print(Fore.CYAN + "      📊 INTRADAY TRADING PERFORMANCE & TAX AUDIT 📊              ")
        print(Fore.CYAN + "==================================================================")

        summary_table = [
            ["Total Trades Taken", total_trades],
            ["Win Count / Loss Count", f"{win_count} Wins / {loss_count} Losses"],
            ["Net Win Rate", f"{win_rate:.1f}%"],
            ["Gross Trading P&L", f"Rs.{gross_pnl:+,.2f}"],
            ["Total Taxes & Charges", f"{Fore.YELLOW}Rs.-{total_charges:,.2f}{Style.RESET_ALL}"],
            ["  • Dhan Brokerage Paid", f"Rs.{total_brokerage:,.2f}"],
            ["  • Total STT Paid", f"Rs.{total_stt:,.2f}"],
            ["  • Total GST Paid (18%)", f"Rs.{total_gst:,.2f}"],
            ["Net Take-Home P&L", f"{Fore.GREEN if net_takehome >= 0 else Fore.RED}Rs.{net_takehome:+,.2f}{Style.RESET_ALL}"],
            ["Profit Factor", f"{profit_factor:.2f}"],
            ["Journal Database", DB_PATH],
            ["Audit Spreadsheet (CSV)", CSV_PATH],
            ["Experience Notes", EXPERIENCE_LOG_PATH],
        ]
        print(tabulate(summary_table, tablefmt="rounded_grid"))

        print(Fore.YELLOW + "\nRecent Completed Trades (With Timestamps & Tax Breakdown):")
        cols = ["order_executed_time", "exit_time", "duration_str", "symbol", "gross_pnl", "total_charges", "net_pnl", "exit_reason"]
        headers = ["Executed At", "Exited At", "Duration", "Contract", "Gross", "Charges", "Net P&L", "Reason"]
        print(tabulate(df.head(5)[cols], headers=headers, tablefmt="simple"))
        print("\n")

journal = TradingJournal()
