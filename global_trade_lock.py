"""
Global Multi-Instrument Trade Lock & Order Idempotency Engine
Provides process-safe, SQLite-backed atomic locking, duplicate signal detection,
order idempotency tracking, multi-asset concurrent trading (up to max_concurrent_positions),
configurable per-instrument cooldown management, and rejection auditing.
"""

import os
import time
import sqlite3
import hashlib
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from config import config

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "trading_journal.db")

class GlobalTradeLockEngine:
    """
    Process-safe, atomic execution control ensuring:
    1. Multi-position concurrency: Allows up to max_concurrent_positions (default 5) across different instruments.
    2. Strict duplicate signal prevention (same candle / signal / contract never enters twice simultaneously).
    3. Order idempotency & persistent intent tracking.
    4. Per-instrument cooldown period after position exit.
    5. Complete audit logging of all rejected duplicate entries.
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_tables()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def _init_tables(self):
        """Initializes multi-instrument atomic lock, order intents, and rejection audit tables."""
        conn = self._get_connection()
        try:
            # Active concurrent position locks table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS active_trade_locks (
                    position_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    asset_key TEXT DEFAULT '',
                    signal_id TEXT DEFAULT '',
                    state TEXT NOT NULL DEFAULT 'OPEN',
                    locked_at TEXT DEFAULT '',
                    updated_at TEXT DEFAULT ''
                );
            """)

            # Cooldown tracking table per symbol / asset
            conn.execute("""
                CREATE TABLE IF NOT EXISTS instrument_cooldowns (
                    key TEXT PRIMARY KEY, -- symbol or asset_key
                    cooldown_until TEXT NOT NULL,
                    reason TEXT DEFAULT ''
                );
            """)

            # Legacy single-row lock table kept for 100% backward compatibility
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trade_execution_locks (
                    lock_id INTEGER PRIMARY KEY,
                    is_locked INTEGER NOT NULL DEFAULT 0,
                    active_position_id TEXT DEFAULT '',
                    symbol TEXT DEFAULT '',
                    state TEXT NOT NULL DEFAULT 'IDLE',
                    locked_at TEXT DEFAULT '',
                    updated_at TEXT DEFAULT '',
                    cooldown_until TEXT DEFAULT ''
                );
            """)
            conn.execute("""
                INSERT OR IGNORE INTO trade_execution_locks 
                (lock_id, is_locked, active_position_id, symbol, state, locked_at, updated_at, cooldown_until)
                VALUES (1, 0, '', '', 'IDLE', '', '', '');
            """)

            # Order intents table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS order_intents (
                    intent_id TEXT PRIMARY KEY,
                    signal_id TEXT UNIQUE NOT NULL,
                    strategy_name TEXT NOT NULL,
                    asset_key TEXT NOT NULL,
                    trading_symbol TEXT NOT NULL,
                    security_id TEXT NOT NULL,
                    transaction_type TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    price REAL NOT NULL,
                    broker_order_id TEXT DEFAULT '',
                    order_status TEXT NOT NULL DEFAULT 'SUBMITTED',
                    candle_timestamp TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)

            # Duplicate rejections audit table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS duplicate_trade_rejections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    signal_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    requested_action TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    rejection_reason TEXT NOT NULL,
                    active_trade_id TEXT DEFAULT ''
                );
            """)
        finally:
            conn.close()

    def generate_signal_id(
        self,
        strategy_name: str,
        asset_key: str,
        symbol: str,
        strike: float,
        option_type: str,
        action: str,
        candle_timestamp: str
    ) -> str:
        """Deterministic 16-character signal hash to prevent duplicate signal entries on the same bar."""
        raw = f"{strategy_name}:{asset_key}:{symbol}:{float(strike):.1f}:{option_type.upper()}:{action.upper()}:{candle_timestamp}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def can_acquire_lock(self, signal_id: str, symbol: str, asset_key: str = "") -> Tuple[bool, str]:
        """
        Verifies entry-blocking conditions with multi-instrument support:
        1. Is max_concurrent_positions capacity reached?
        2. Is this exact contract/symbol already active in another trade?
        3. Is this specific symbol/asset under cooldown?
        4. Has this exact signal already been executed or attempted?
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            max_conc = getattr(config, "max_concurrent_positions", 5)

            # 1. Check active position count
            cursor.execute("SELECT COUNT(*) as cnt FROM active_trade_locks WHERE state = 'OPEN';")
            active_cnt = cursor.fetchone()["cnt"]
            if active_cnt >= max_conc:
                reason = f"Max concurrent positions limit ({active_cnt}/{max_conc}) reached across algo. Manage or close an open trade first."
                return False, reason

            # 2. Check if the exact contract/symbol is already active
            cursor.execute("SELECT position_id, symbol FROM active_trade_locks WHERE symbol = ? AND state = 'OPEN';", (symbol,))
            existing = cursor.fetchone()
            if existing:
                reason = f"Duplicate trade blocked: Contract '{symbol}' is already actively open (Position ID: {existing['position_id']})."
                return False, reason

            # 3. Check Cooldown timer for this symbol and asset
            now = datetime.now()
            cursor.execute("""
                SELECT key, cooldown_until FROM instrument_cooldowns 
                WHERE key IN (?, ?) AND cooldown_until > ?;
            """, (symbol, asset_key or symbol, now.strftime("%Y-%m-%d %H:%M:%S")))
            cd_row = cursor.fetchone()
            if cd_row:
                try:
                    cd_dt = datetime.strptime(cd_row["cooldown_until"], "%Y-%m-%d %H:%M:%S")
                    remaining = (cd_dt - now).total_seconds()
                    if remaining > 0:
                        reason = f"Cooldown active for {cd_row['key']}: {remaining:.1f}s remaining after previous exit. Standing down to prevent churning."
                        return False, reason
                except Exception:
                    pass

            # 4. Duplicate signal check in order intents
            if signal_id:
                cursor.execute("""
                    SELECT intent_id, trading_symbol, order_status, created_at 
                    FROM order_intents 
                    WHERE signal_id = ? AND order_status IN ('SUBMITTED', 'PENDING', 'OPEN', 'FILLED');
                """, (signal_id,))
                intent = cursor.fetchone()
                if intent:
                    reason = f"Duplicate signal detected: Signal ID {signal_id} currently active for '{intent['trading_symbol']}' at {intent['created_at']} (Status: {intent['order_status']}). Repeated entry blocked."
                    return False, reason

            return True, "Lock available. Multi-instrument pre-entry safety gates passed."
        finally:
            conn.close()

    def acquire_lock(
        self,
        signal_id: str,
        symbol: str,
        position_id: str,
        intent_data: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, str]:
        """
        Atomically acquires a multi-instrument trade lock using SQLite BEGIN IMMEDIATE transaction.
        Returns (True, 'Locked successfully') or (False, rejection_reason).
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("BEGIN IMMEDIATE;")

            max_conc = getattr(config, "max_concurrent_positions", 5)
            cursor.execute("SELECT COUNT(*) as cnt FROM active_trade_locks WHERE state = 'OPEN';")
            active_cnt = cursor.fetchone()["cnt"]
            if active_cnt >= max_conc:
                cursor.execute("COMMIT;")
                reason = f"Concurrency capacity full: {active_cnt}/{max_conc} trades currently active."
                self.log_rejection(signal_id, symbol, "ENTRY", reason, "")
                return False, reason

            cursor.execute("SELECT position_id FROM active_trade_locks WHERE symbol = ? AND state = 'OPEN';", (symbol,))
            if cursor.fetchone():
                cursor.execute("COMMIT;")
                reason = f"Duplicate conflict: Active lock already held for contract '{symbol}'."
                self.log_rejection(signal_id, symbol, "ENTRY", reason, "")
                return False, reason

            now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            asset_k = intent_data.get("asset_key", "") if intent_data else ""

            # Record Order Intent first (Idempotency)
            if intent_data:
                intent_id = intent_data.get("intent_id") or f"INTENT_{int(time.time() * 1000)}"
                cursor.execute("""
                    INSERT INTO order_intents (
                        intent_id, signal_id, strategy_name, asset_key, trading_symbol,
                        security_id, transaction_type, quantity, price, broker_order_id,
                        order_status, candle_timestamp, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (
                    intent_id,
                    signal_id,
                    intent_data.get("strategy_name", "UNKNOWN"),
                    asset_k or "NIFTY",
                    symbol,
                    str(intent_data.get("security_id", "0")),
                    intent_data.get("transaction_type", "BUY"),
                    int(intent_data.get("quantity", 65)),
                    float(intent_data.get("price", 0.0)),
                    str(intent_data.get("broker_order_id", "")),
                    intent_data.get("order_status", "SUBMITTED"),
                    intent_data.get("candle_timestamp", now_iso),
                    now_iso,
                    now_iso
                ))

            # Insert into active multi-trade locks
            cursor.execute("""
                INSERT OR REPLACE INTO active_trade_locks 
                (position_id, symbol, asset_key, signal_id, state, locked_at, updated_at)
                VALUES (?, ?, ?, ?, 'OPEN', ?, ?);
            """, (position_id, symbol, asset_k, signal_id, now_iso, now_iso))

            # Sync legacy lock row for backward compatibility
            cursor.execute("""
                UPDATE trade_execution_locks SET
                    is_locked = 1,
                    active_position_id = ?,
                    symbol = ?,
                    state = 'OPEN',
                    locked_at = ?,
                    updated_at = ?,
                    cooldown_until = ''
                WHERE lock_id = 1;
            """, (position_id, symbol, now_iso, now_iso))

            cursor.execute("COMMIT;")
            return True, f"Multi-trade lock acquired for {symbol} (Position ID: {position_id})"
        except Exception as e:
            try:
                conn.execute("ROLLBACK;")
            except Exception:
                pass
            return False, f"Failed to acquire lock due to DB error: {e}"
        finally:
            conn.close()

    def update_lock_state(self, state: str, position_id: Optional[str] = None):
        """Updates internal lock state (e.g. 'PENDING_EXIT', 'OPEN')."""
        conn = self._get_connection()
        try:
            now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if position_id:
                conn.execute("""
                    UPDATE active_trade_locks SET state = ?, updated_at = ? WHERE position_id = ?;
                """, (state, now_iso, position_id))
            conn.execute("""
                UPDATE trade_execution_locks SET state = ?, updated_at = ? WHERE lock_id = 1;
            """, (state, now_iso))
        finally:
            conn.close()

    def release_lock(self, position_id: str, cooldown_seconds: int = 60) -> bool:
        """
        Releases trade lock for a specific closed position (or all if position_id='ALL'),
        and atomically engages cooldown for that instrument.
        """
        conn = self._get_connection()
        try:
            now = datetime.now()
            now_iso = now.strftime("%Y-%m-%d %H:%M:%S")
            cooldown_dt = now + timedelta(seconds=max(0, cooldown_seconds))
            cooldown_iso = cooldown_dt.strftime("%Y-%m-%d %H:%M:%S") if cooldown_seconds > 0 else ""

            if position_id == "ALL":
                conn.execute("DELETE FROM active_trade_locks;")
                conn.execute("""
                    UPDATE trade_execution_locks SET
                        is_locked = 0,
                        active_position_id = '',
                        symbol = '',
                        state = CASE WHEN ? != '' THEN 'COOLDOWN' ELSE 'IDLE' END,
                        updated_at = ?,
                        cooldown_until = ?
                    WHERE lock_id = 1;
                """, (cooldown_iso, now_iso, cooldown_iso))
                conn.execute("""
                    UPDATE order_intents SET order_status = 'CLOSED', updated_at = ?
                    WHERE order_status IN ('SUBMITTED', 'PENDING', 'OPEN', 'FILLED');
                """, (now_iso,))
            else:
                cursor = conn.cursor()
                cursor.execute("SELECT symbol, asset_key FROM active_trade_locks WHERE position_id = ?;", (position_id,))
                row = cursor.fetchone()
                if row:
                    sym = row["symbol"]
                    ak = row["asset_key"]
                    if cooldown_seconds > 0:
                        cursor.execute("""
                            INSERT OR REPLACE INTO instrument_cooldowns (key, cooldown_until, reason)
                            VALUES (?, ?, ?);
                        """, (sym, cooldown_iso, f"Exit cooldown for {position_id}"))
                        if ak:
                            cursor.execute("""
                                INSERT OR REPLACE INTO instrument_cooldowns (key, cooldown_until, reason)
                                VALUES (?, ?, ?);
                            """, (ak, cooldown_iso, f"Exit cooldown for {position_id}"))

                cursor.execute("DELETE FROM active_trade_locks WHERE position_id = ?;", (position_id,))
                
                # Check remaining active positions
                cursor.execute("SELECT position_id, symbol FROM active_trade_locks WHERE state = 'OPEN' LIMIT 1;")
                remaining = cursor.fetchone()
                if remaining:
                    cursor.execute("""
                        UPDATE trade_execution_locks SET
                            is_locked = 1,
                            active_position_id = ?,
                            symbol = ?,
                            state = 'OPEN',
                            updated_at = ?
                        WHERE lock_id = 1;
                    """, (remaining["position_id"], remaining["symbol"], now_iso))
                else:
                    cursor.execute("""
                        UPDATE trade_execution_locks SET
                            is_locked = 0,
                            active_position_id = '',
                            symbol = '',
                            state = CASE WHEN ? != '' THEN 'COOLDOWN' ELSE 'IDLE' END,
                            updated_at = ?,
                            cooldown_until = ?
                        WHERE lock_id = 1;
                    """, (cooldown_iso, now_iso, cooldown_iso))

            return True
        except Exception as e:
            print(f"[GlobalTradeLock] Error releasing lock: {e}")
            return False
        finally:
            conn.close()

    def force_reset_lock(self, reason: str = "Manual User Reset"):
        """Emergency administrative unlock."""
        conn = self._get_connection()
        try:
            now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("DELETE FROM active_trade_locks;")
            conn.execute("DELETE FROM instrument_cooldowns;")
            conn.execute("""
                UPDATE trade_execution_locks SET
                    is_locked = 0,
                    active_position_id = '',
                    symbol = '',
                    state = 'IDLE',
                    locked_at = '',
                    updated_at = ?,
                    cooldown_until = ''
                WHERE lock_id = 1;
            """, (now_iso,))
            conn.execute("""
                UPDATE order_intents SET order_status = 'CLOSED', updated_at = ?
                WHERE order_status IN ('SUBMITTED', 'PENDING', 'OPEN', 'FILLED');
            """, (now_iso,))
            print(f"[GlobalTradeLock] Lock forced reset: {reason}")
        finally:
            conn.close()

    def is_cooldown_active(self, asset_or_symbol: str = "") -> Tuple[bool, float]:
        """Returns (is_active, remaining_seconds) for a specific instrument or global."""
        conn = self._get_connection()
        try:
            now = datetime.now()
            cursor = conn.cursor()
            if asset_or_symbol:
                cursor.execute("""
                    SELECT cooldown_until FROM instrument_cooldowns 
                    WHERE key = ? AND cooldown_until > ?;
                """, (asset_or_symbol, now.strftime("%Y-%m-%d %H:%M:%S")))
                row = cursor.fetchone()
                if row:
                    try:
                        cd_dt = datetime.strptime(row["cooldown_until"], "%Y-%m-%d %H:%M:%S")
                        return True, max(0.0, (cd_dt - now).total_seconds())
                    except Exception:
                        pass

            # Legacy global cooldown fallback
            cursor.execute("SELECT cooldown_until FROM trade_execution_locks WHERE lock_id = 1;")
            row = cursor.fetchone()
            if row and row["cooldown_until"]:
                try:
                    cd_dt = datetime.strptime(row["cooldown_until"], "%Y-%m-%d %H:%M:%S")
                    if now < cd_dt:
                        return True, max(0.0, (cd_dt - now).total_seconds())
                except Exception:
                    pass
            return False, 0.0
        finally:
            conn.close()

    def log_rejection(
        self,
        signal_id: str,
        symbol: str,
        requested_action: str,
        reason: str,
        active_trade_id: str = ""
    ):
        """Audits every rejected duplicate or locked trade into SQLite."""
        conn = self._get_connection()
        try:
            now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("""
                INSERT INTO duplicate_trade_rejections (
                    timestamp, signal_id, symbol, requested_action, decision, rejection_reason, active_trade_id
                ) VALUES (?, ?, ?, ?, 'REJECTED', ?, ?);
            """, (now_iso, signal_id or "NONE", symbol, requested_action, reason, active_trade_id))
        except Exception as e:
            print(f"[GlobalTradeLock] Error logging rejection: {e}")
        finally:
            conn.close()

    def get_lock_status(self) -> Dict[str, Any]:
        """Returns structured lock state for UI badges and API health checks."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as cnt FROM active_trade_locks WHERE state = 'OPEN';")
            active_cnt = cursor.fetchone()["cnt"]
            max_conc = getattr(config, "max_concurrent_positions", 5)

            cursor.execute("SELECT position_id, symbol, asset_key, locked_at FROM active_trade_locks WHERE state = 'OPEN';")
            active_list = [dict(r) for r in cursor.fetchall()]

            cooldown_active, cooldown_rem = self.is_cooldown_active()
            is_full = (active_cnt >= max_conc)
            state = "FULL" if is_full else ("OPEN" if active_cnt > 0 else ("COOLDOWN" if cooldown_active else "IDLE"))

            return {
                "is_locked": is_full,
                "state": state,
                "active_count": active_cnt,
                "max_concurrent_positions": max_conc,
                "active_positions": active_list,
                "symbol": active_list[0]["symbol"] if active_list else "",
                "active_position_id": active_list[0]["position_id"] if active_list else "",
                "locked_at": active_list[0]["locked_at"] if active_list else "",
                "cooldown_active": cooldown_active,
                "cooldown_remaining_seconds": int(cooldown_rem)
            }
        finally:
            conn.close()

    def get_status(self) -> Dict[str, Any]:
        """Alias for get_lock_status."""
        return self.get_lock_status()

    def get_recent_rejections(self, limit: int = 15) -> List[Dict[str, Any]]:
        """Returns recent duplicate rejection audit records."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT timestamp, signal_id, symbol, requested_action, decision, rejection_reason, active_trade_id
                FROM duplicate_trade_rejections
                ORDER BY id DESC
                LIMIT ?;
            """, (limit,))
            rows = cursor.fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

# Singleton instance
global_trade_lock = GlobalTradeLockEngine()
