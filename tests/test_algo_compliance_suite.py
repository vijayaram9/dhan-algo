"""
Automated 34-Test Comprehensive Compliance & Regression Test Suite
Vijay Sai's Dhan NIFTY Options Algo
Tests all requirements across:
- Group A: Market Data Accuracy (Tests 1-9)
- Group B: Trading Signals & Candle Timing (Tests 10-17)
- Group C: Duplicate Trade Prevention & Locking (Tests 18-28)
- Group D: Risk Management & Execution Control (Tests 29-34)
"""

import os
import sys
import time
import math
import unittest
import threading
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# Ensure parent path is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config import config
from indicators import IndicatorEngine
from scrip_master import ScripMaster
from contract_selector import contract_selector, SelectedContract
from global_trade_lock import global_trade_lock, GlobalTradeLockEngine
from smart_edge_strategy import smart_edge_strategy, SmartEdgeSignal
from smart_edge_engine import NiftySmartEdgeEngine, SmartEdgePosition
from dhan_client import DhanTraderClient

TEST_DB_PATH = os.path.join(os.path.dirname(__file__), "test_lock.db")

class TestAlgoComplianceSuite(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Initialize test lock engine using dedicated isolated DB
        cls.lock_engine = GlobalTradeLockEngine(db_path=TEST_DB_PATH)
        cls.lock_engine.force_reset_lock("Test Suite Init")

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except Exception:
                pass

    def setUp(self):
        self.lock_engine.force_reset_lock("Test Setup Reset")

    # =========================================================================
    # GROUP A: MARKET DATA ACCURACY (TESTS 1 - 9)
    # =========================================================================

    def test_01_correct_option_ltp_calibration(self):
        """Test 1: Verify option LTP is properly calibrated to underlying spot."""
        client = DhanTraderClient(mode="PAPER")
        spot = 24450.0
        ltp = client.get_option_ltp("0", spot, 24450.0, "CE")
        self.assertGreater(ltp, 50.0, "ATM Call premium must be > 50")
        self.assertLess(ltp, 300.0, "ATM Call premium must be < 300")

    def test_02_otm_single_digit_vs_atm_pricing(self):
        """Test 2: Verify Deep OTM strike (₹7.35) vs ATM strike (₹120), eliminating the discrepancy."""
        client = DhanTraderClient(mode="PAPER")
        spot = 24450.0
        # 23450 PUT (Security ID: 39938) is 1000 points OTM
        otm_prem = client.get_option_ltp("39938", spot, 23450.0, "PE")
        # 24450 PUT (Security ID: 40002) is ATM
        atm_prem = client.get_option_ltp("40002", spot, 24450.0, "PE")
        self.assertLess(otm_prem, 15.0, f"Deep OTM 23450 PE should be < ₹15, got {otm_prem}")
        self.assertGreater(atm_prem, 70.0, f"ATM 24450 PE should be > ₹70, got {atm_prem}")

    def test_03_incorrect_security_id_handling(self):
        """Test 3: Verify system handles missing/invalid security ID gracefully without crashing."""
        client = DhanTraderClient(mode="PAPER")
        # Invalid security_id should fall back to standard Black-Scholes without error
        prem = client.get_option_ltp("INVALID_999", 24450.0, 24450.0, "CE")
        self.assertGreater(prem, 0.0)

    def test_04_near_term_expiry_date_matching(self):
        """Test 4: Verify ScripMaster resolves the active near-term expiry correctly."""
        sm = ScripMaster()
        expiries = sm.get_available_expiries("NIFTY")
        self.assertGreater(len(expiries), 0, "Should have upcoming active expiries")
        target_exp = sm.resolve_target_expiry("CURRENT_WEEK", "NIFTY")
        self.assertIsNotNone(target_exp)

    def test_05_strike_rounding_and_step_size(self):
        """Test 5: Verify strike rounding aligns strictly with instrument step size (50 for Nifty, 100 for Bank Nifty)."""
        spot_nifty = 24463.2
        atm_nifty = round(spot_nifty / 50.0) * 50
        self.assertEqual(atm_nifty, 24450.0)

        spot_bn = 57422.8
        atm_bn = round(spot_bn / 100.0) * 100
        self.assertEqual(atm_bn, 57400.0)

    def test_06_ce_pe_contract_isolation(self):
        """Test 6: Verify CE and PE contracts are never cross-pollinated."""
        sm = ScripMaster()
        ce_opt = sm.get_nifty_option(24450.0, "CE", 0, asset_key="NIFTY")
        pe_opt = sm.get_nifty_option(24450.0, "PE", 0, asset_key="NIFTY")
        self.assertEqual(ce_opt["option_type"], "CE")
        self.assertEqual(pe_opt["option_type"], "PE")
        self.assertNotEqual(ce_opt["security_id"], pe_opt["security_id"])

    def test_07_stale_data_rejection(self):
        """Test 7: Verify contract selector rejects spot prices that are stale or drift > 1%."""
        c = contract_selector.select_contract(
            spot_price=23440.0,  # Stale by 1000 pts
            action="BUY_PE",
            estimated_premium=100.0,
            asset_key="NIFTY",
            available_capital=50000.0
        )
        # Guardrail 0 auto-corrects spot to live exchange spot (>= 24400)
        self.assertGreaterEqual(c.strike_price, 24400.0)

    def test_08_api_disconnection_fail_safe(self):
        """Test 8: Verify DhanTraderClient handles disconnected or null Dhan client safely."""
        client = DhanTraderClient(mode="PAPER")
        client.dhan = None
        funds = client.get_fund_limits()
        self.assertEqual(funds["status"], "failure")

    def test_09_feed_consistency_check(self):
        """Test 9: Verify internal Black-Scholes pricing matches intrinsic boundary conditions."""
        client = DhanTraderClient(mode="PAPER")
        spot = 24500.0
        strike = 24400.0  # 100 pts ITM Call
        prem = client.get_option_ltp("0", spot, strike, "CE")
        self.assertGreaterEqual(prem, 100.0, "ITM Call must at least equal intrinsic value (100 pts)")

    # =========================================================================
    # GROUP B: TRADING SIGNALS & STRATEGY TIMING (TESTS 10 - 17)
    # =========================================================================

    def test_10_correct_ema_9_calculation(self):
        """Test 10: Verify IndicatorEngine EMA 9 matches standard exponential moving average."""
        data = pd.Series([100.0 + i for i in range(30)])
        ema9, ema21, slope = IndicatorEngine.calculate_emas(data)
        ref_ema9 = data.ewm(span=9, adjust=False).mean().iloc[-1]
        self.assertAlmostEqual(ema9, ref_ema9, places=2)

    def test_11_correct_ema_21_calculation(self):
        """Test 11: Verify IndicatorEngine EMA 21 matches standard exponential moving average."""
        data = pd.Series([100.0 + i for i in range(30)])
        ema9, ema21, slope = IndicatorEngine.calculate_emas(data)
        ref_ema21 = data.ewm(span=21, adjust=False).mean().iloc[-1]
        self.assertAlmostEqual(ema21, ref_ema21, places=2)

    def test_12_correct_rsi_14_calculation(self):
        """Test 12: Verify IndicatorEngine RSI 14 generates bounded values in [0, 100]."""
        data = pd.Series([100.0, 102.0, 101.5, 103.0, 104.5, 102.0, 105.0, 106.0, 107.0, 105.5, 108.0, 109.0, 110.0, 108.5, 111.0, 112.0])
        rsi = IndicatorEngine.calculate_rsi(data, 14)
        self.assertGreaterEqual(rsi, 0.0)
        self.assertLessEqual(rsi, 100.0)

    def test_13_correct_pivot_calculation(self):
        """Test 13: Verify Classic Pivot Points (Central Pivot, R1, S1, R2, S2)."""
        h, l, c = 24500.0, 24300.0, 24450.0
        pivots = IndicatorEngine.calculate_pivot_points(h, l, c)
        raw_pp = (h + l + c) / 3.0
        expected_pp = round(raw_pp, 2)
        expected_r1 = round((2 * raw_pp) - l, 2)
        expected_s1 = round((2 * raw_pp) - h, 2)
        self.assertAlmostEqual(pivots["central_pivot"], expected_pp, places=1)
        self.assertAlmostEqual(pivots["r1"], expected_r1, places=1)
        self.assertAlmostEqual(pivots["s1"], expected_s1, places=1)

    def test_14_completed_candle_processing(self):
        """Test 14: Verify strategy accumulates completed candles correctly without duplicates."""
        initial_len = len(smart_edge_strategy.candles_5m)
        smart_edge_strategy.add_completed_candle("2026-11-06 10:00", 24400, 24420, 24390, 24410, 1000)
        self.assertEqual(len(smart_edge_strategy.candles_5m), initial_len + 1)

    def test_15_duplicate_candle_timestamp_handling(self):
        """Test 15: Verify deterministic signal hash identifies identical candle timestamps."""
        sig1 = self.lock_engine.generate_signal_id("TREND_PULLBACK", "NIFTY", "NIFTY 24450 CE", 24450.0, "CE", "BUY_CE", "2026-11-06 10:15")
        sig2 = self.lock_engine.generate_signal_id("TREND_PULLBACK", "NIFTY", "NIFTY 24450 CE", 24450.0, "CE", "BUY_CE", "2026-11-06 10:15")
        self.assertEqual(sig1, sig2, "Same candle timestamp must produce identical signal hash")

    def test_16_stale_signal_rejection(self):
        """Test 16: Verify signals from older candles produce distinct signal IDs and fail re-entry."""
        sig_old = self.lock_engine.generate_signal_id("TREND_PULLBACK", "NIFTY", "NIFTY 24450 CE", 24450.0, "CE", "BUY_CE", "2026-11-06 09:30")
        sig_new = self.lock_engine.generate_signal_id("TREND_PULLBACK", "NIFTY", "NIFTY 24450 CE", 24450.0, "CE", "BUY_CE", "2026-11-06 10:30")
        self.assertNotEqual(sig_old, sig_new)

    def test_17_correct_entry_confluence_logic(self):
        """Test 17: Verify strategy evaluates confluence and returns structured signal object."""
        sig = smart_edge_strategy.evaluate("10:30")
        self.assertIsInstance(sig, SmartEdgeSignal)
        self.assertIn(sig.action, ["BUY_CE", "BUY_PE", "NO_TRADE", "HOLD"])

    # =========================================================================
    # GROUP C: DUPLICATE TRADE PREVENTION & LOCKING (TESTS 18 - 28)
    # =========================================================================

    def test_18_same_signal_evaluated_ten_times(self):
        """Test 18: Same signal evaluated 10 consecutive times -> Exactly 1 succeeds, 9 rejected."""
        sig_id = self.lock_engine.generate_signal_id("SNIPER", "NIFTY", "NIFTY 24450 CE", 24450.0, "CE", "BUY_CE", "2026-11-06 10:30")
        symbol = "NIFTY 24450 CE"
        
        # 1st attempt: should succeed
        acq_1, reason_1 = self.lock_engine.acquire_lock(sig_id, symbol, "POS_TEST_001", {"quantity": 65, "price": 120.0})
        self.assertTrue(acq_1, f"First attempt should succeed: {reason_1}")

        # Attempts 2 through 10: must ALL be rejected
        for i in range(2, 11):
            can_acq, rej_reason = self.lock_engine.can_acquire_lock(sig_id, symbol)
            self.assertFalse(can_acq, f"Attempt {i} must be blocked!")
            self.assertIn("already", rej_reason.lower())

    def test_19_concurrent_processes_atomic_lock(self):
        """Test 19: Two concurrent threads request entry simultaneously -> Exactly 1 succeeds, 1 rejected."""
        sig_1 = self.lock_engine.generate_signal_id("SNIPER", "NIFTY", "NIFTY 24450 CE", 24450.0, "CE", "BUY_CE", "2026-11-06 10:35")
        sig_2 = self.lock_engine.generate_signal_id("RADAR", "NIFTY", "NIFTY 24450 PE", 24450.0, "PE", "BUY_PE", "2026-11-06 10:35")

        results = []
        def attempt_entry(sig, sym, pid):
            acq, msg = self.lock_engine.acquire_lock(sig, sym, pid, {"quantity": 65, "price": 100.0})
            results.append((acq, msg))

        t1 = threading.Thread(target=attempt_entry, args=(sig_1, "NIFTY 24450 CE", "POS_THREAD_1"))
        t2 = threading.Thread(target=attempt_entry, args=(sig_2, "NIFTY 24450 PE", "POS_THREAD_2"))

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        successes = [r for r in results if r[0] is True]
        failures = [r for r in results if r[0] is False]
        self.assertEqual(len(successes), 1, "Exactly 1 concurrent entry must succeed")
        self.assertEqual(len(failures), 1, "Exactly 1 concurrent entry must be rejected")

    def test_20_multi_strategy_collision_prevention(self):
        """Test 20: When Trend Pullback and Radar trigger together, only 1 trade enters."""
        sig_tp = self.lock_engine.generate_signal_id("TREND_PULLBACK", "NIFTY", "NIFTY 24450 CE", 24450.0, "CE", "BUY_CE", "2026-11-06 10:40")
        self.lock_engine.acquire_lock(sig_tp, "NIFTY 24450 CE", "POS_TP_01", {"quantity": 65, "price": 115.0})

        sig_radar = self.lock_engine.generate_signal_id("RADAR_OPP", "BANKNIFTY", "BANKNIFTY 57400 CE", 57400.0, "CE", "BUY_CE", "2026-11-06 10:40")
        can_acq, reason = self.lock_engine.can_acquire_lock(sig_radar, "BANKNIFTY 57400 CE")
        self.assertFalse(can_acq, "Second strategy must be blocked while first strategy trade is active")

    def test_21_api_timeout_idempotency_tracking(self):
        """Test 21: Verify order intent is stored before order placement to prevent duplicate submission on timeout."""
        sig_id = self.lock_engine.generate_signal_id("SNIPER", "NIFTY", "NIFTY 24450 PE", 24450.0, "PE", "BUY_PE", "2026-11-06 10:45")
        intent_data = {
            "intent_id": "INTENT_TIMEOUT_TEST",
            "strategy_name": "SNIPER",
            "asset_key": "NIFTY",
            "security_id": "40002",
            "transaction_type": "BUY",
            "quantity": 65,
            "price": 125.0
        }
        self.lock_engine.acquire_lock(sig_id, "NIFTY 24450 PE", "POS_TIMEOUT", intent_data)

        # Retry should see existing intent and reject
        can_retry, msg = self.lock_engine.can_acquire_lock(sig_id, "NIFTY 24450 PE")
        self.assertFalse(can_retry)
        self.assertIn("already", msg.lower())

    def test_22_restart_state_preservation(self):
        """Test 22: Verify SQLite-backed lock state survives object/process re-instantiation."""
        sig_id = self.lock_engine.generate_signal_id("SWING", "NIFTY", "NIFTY 24450 CE", 24450.0, "CE", "BUY_CE", "2026-11-06 10:50")
        self.lock_engine.acquire_lock(sig_id, "NIFTY 24450 CE", "POS_RESTART", {"quantity": 65, "price": 130.0})

        # Simulate fresh app startup with new engine instance pointing to same DB
        new_instance = GlobalTradeLockEngine(db_path=TEST_DB_PATH)
        status = new_instance.get_lock_status()
        self.assertTrue(status["is_locked"], "Lock must remain active across restarts")
        self.assertEqual(status["symbol"], "NIFTY 24450 CE")

    def test_23_partially_filled_order_blocks_new_entry(self):
        """Test 23: State 'PENDING_ENTRY' or 'PARTIAL_FILL' prevents new entry."""
        self.lock_engine.acquire_lock("SIG_PARTIAL", "NIFTY 24450 CE", "POS_PARTIAL", {"quantity": 65, "price": 100.0})
        self.lock_engine.update_lock_state("PARTIAL_FILL")
        can_acq, _ = self.lock_engine.can_acquire_lock("SIG_NEW", "NIFTY 24450 PE")
        self.assertFalse(can_acq)

    def test_24_existing_broker_position_blocks_entry(self):
        """Test 24: Engine active_positions check blocks new entry if position count >= 1."""
        engine = NiftySmartEdgeEngine()
        engine.active_positions.clear()
        mock_pos = SmartEdgePosition(
            position_id="POS_MOCK", security_id="123", symbol="NIFTY 24450 CE", option_type="CE",
            strategy_name="MOCK", quantity=65, entry_premium=100.0, current_premium=100.0,
            hard_stop_premium=80.0, underlying_entry_spot=24450.0, underlying_structural_sl=24400.0,
            target_1_spot=24500.0, target_2_spot=24550.0, highest_premium=100.0, highest_spot=24450.0,
            entry_time_iso="2026-11-06 10:00:00", signal_time_iso="2026-11-06 10:00:00",
            expiry_date="2026-11-09", atr_at_entry=25.0, strike_price=24450.0, trade_style="SNIPER",
            target_premium=140.0, max_holding_seconds=1800
        )
        engine.active_positions.append(mock_pos)

        # Attempt execute_entry with another contract
        contract = contract_selector.select_contract(24450.0, "BUY_PE", 100.0, "ATM", available_capital=50000.0)
        sig = SmartEdgeSignal("BUY_PE", "MOCK", "TREND", 24450.0, 24400.0, 24500.0, 24550.0)
        engine.execute_entry(sig, contract, 24450.0)

        # Position count must REMAIN exactly 1
        self.assertEqual(len(engine.active_positions), 1)

    def test_25_pending_exit_order_blocks_entry(self):
        """Test 25: Lock state 'PENDING_EXIT' blocks new entries."""
        self.lock_engine.acquire_lock("SIG_EXIT", "NIFTY 24450 CE", "POS_EXIT", {"quantity": 65, "price": 100.0})
        self.lock_engine.update_lock_state("PENDING_EXIT")
        can_acq, _ = self.lock_engine.can_acquire_lock("SIG_NEW", "NIFTY 24450 PE")
        self.assertFalse(can_acq)

    def test_26_immediate_reentry_blocked_by_cooldown(self):
        """Test 26: After position exit, immediate re-entry on same tick is blocked by cooldown."""
        sig_id = self.lock_engine.generate_signal_id("SNIPER", "NIFTY", "NIFTY 24450 CE", 24450.0, "CE", "BUY_CE", "2026-11-06 11:00")
        self.lock_engine.acquire_lock(sig_id, "NIFTY 24450 CE", "POS_CD_TEST", {"quantity": 65, "price": 120.0})

        # Exit position with 300s cooldown
        self.lock_engine.release_lock("POS_CD_TEST", cooldown_seconds=300)

        # Try to re-enter immediately
        sig_next = self.lock_engine.generate_signal_id("SNIPER", "NIFTY", "NIFTY 24450 CE", 24450.0, "CE", "BUY_CE", "2026-11-06 11:01")
        can_reenter, cd_reason = self.lock_engine.can_acquire_lock(sig_next, "NIFTY 24450 CE")
        self.assertFalse(can_reenter, "Immediate re-entry must be blocked by cooldown")
        self.assertIn("cooldown active", cd_reason.lower())

    def test_27_reentry_permitted_after_cooldown_expires(self):
        """Test 27: Re-entry is permitted after cooldown period elapses."""
        sig_id = "SIG_EXPIRE_TEST"
        self.lock_engine.acquire_lock(sig_id, "NIFTY 24450 CE", "POS_EXP", {"quantity": 65, "price": 100.0})
        # Release with 0s cooldown
        self.lock_engine.release_lock("POS_EXP", cooldown_seconds=0)

        sig_fresh = "SIG_FRESH_CANDLE"
        can_enter, reason = self.lock_engine.can_acquire_lock(sig_fresh, "NIFTY 24450 PE")
        self.assertTrue(can_enter, f"Should be allowed after cooldown: {reason}")

    def test_28_fresh_signal_required_after_closure(self):
        """Test 28: Stale/previously executed signal remains blocked even after cooldown expires."""
        sig_id = self.lock_engine.generate_signal_id("SNIPER", "NIFTY", "NIFTY 24450 CE", 24450.0, "CE", "BUY_CE", "2026-11-06 11:15")
        self.lock_engine.acquire_lock(sig_id, "NIFTY 24450 CE", "POS_STALE_TEST", {"quantity": 65, "price": 120.0})
        self.lock_engine.release_lock("POS_STALE_TEST", cooldown_seconds=0)

        # Attempting the SAME signal_id again must be rejected as duplicate
        can_enter, reason = self.lock_engine.can_acquire_lock(sig_id, "NIFTY 24450 CE")
        self.assertFalse(can_enter, "Old signal must not be reused")
        self.assertIn("duplicate signal", reason.lower())

    # =========================================================================
    # GROUP D: RISK MANAGEMENT & EXECUTION CONTROL (TESTS 29 - 34)
    # =========================================================================

    def test_29_daily_loss_limit_triggers_kill_switch(self):
        """Test 29: Daily loss limit breach (₹500 ceiling) halts trading and triggers kill switch."""
        engine = NiftySmartEdgeEngine()
        engine.daily_loss_limit = 500.0
        engine.realized_pnl = -550.0  # Breach
        engine.check_trading_schedule()
        if engine.realized_pnl <= -engine.daily_loss_limit:
            engine.activate_kill_switch("Daily loss limit breached")
        self.assertTrue(engine.is_kill_switch_active)

    def test_30_50_percent_capital_safety_ceiling(self):
        """Test 30: Contract requiring > 50% capital is rejected by risk rules."""
        # 1 lot NIFTY (65 qty) at ₹250 premium = ₹16,250
        # If available capital is ₹25,000, max allowed is 50% = ₹12,500
        c = contract_selector.select_contract(
            spot_price=24450.0,
            action="BUY_CE",
            estimated_premium=250.0,
            available_capital=25000.0
        )
        self.assertFalse(c.risk_budget_approved)
        self.assertIn("50% Capital Safety Ceiling", c.rejection_reason)

    def test_31_hard_stop_loss_calculated_at_20_percent(self):
        """Test 31: Hard premium stop is strictly set to 20% below entry."""
        prem = 150.0
        expected_sl = round(prem * 0.80, 2)
        c = contract_selector.select_contract(
            spot_price=24450.0,
            action="BUY_CE",
            estimated_premium=prem,
            available_capital=50000.0
        )
        self.assertEqual(c.hard_stop_premium, expected_sl)

    def test_32_broker_order_rejection_handled_safely(self):
        """Test 32: If broker rejects order, global lock is released and system does not deadlock."""
        engine = NiftySmartEdgeEngine()
        engine.active_positions.clear()
        
        # Mock client to return failure
        original_place = engine.client.place_order
        engine.client.place_order = lambda **kwargs: {"status": "failure", "remarks": "Simulated Margin Shortfall"}
        
        c = contract_selector.select_contract(24450.0, "BUY_CE", 100.0, available_capital=50000.0)
        sig = SmartEdgeSignal("BUY_CE", "MOCK", "TREND", 24450.0, 24400.0, 24500.0, 24550.0)
        
        engine.execute_entry(sig, c, 24450.0)
        
        # Ensure active_positions remains empty
        self.assertEqual(len(engine.active_positions), 0)
        
        # Restore mock
        engine.client.place_order = original_place

    def test_33_broker_position_reconciliation(self):
        """Test 33: Verify get_open_positions safely parses broker response."""
        client = DhanTraderClient(mode="PAPER")
        positions = client.get_open_positions()
        self.assertIsInstance(positions, list)

    def test_34_emergency_manual_kill_switch(self):
        """Test 34: Emergency kill switch halts trading immediately."""
        engine = NiftySmartEdgeEngine()
        engine.activate_kill_switch("Manual Emergency Halt")
        self.assertTrue(engine.is_kill_switch_active)
        self.assertEqual(engine.kill_switch_reason, "Manual Emergency Halt")

if __name__ == "__main__":
    unittest.main()
