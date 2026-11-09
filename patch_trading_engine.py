# -*- coding: utf-8 -*-
"""
patch_trading_engine.py
Fixes:
1. Structural exit bug (2-second PUT exit) - properly indents PE inside spot_is_sane and fixes target condition.
2. Suffocating stop loss distances in SNIPER mode - expands to realistic volatility-adjusted stops with 1:2 R:R.
3. Fee-protected breakeven floor - guarantees no trade exits in loss from broker fees once profitable.
4. Capital-proportional risk ceiling and daily loss limit (Rs.1,500 trade risk / Rs.3,000 daily loss on Rs.1.5L capital).
5. Removes 15-minute freeze on minor scratch trades.
"""
import re

# 1. Patch smart_edge_engine.py
with open("smart_edge_engine.py", "r", encoding="utf-8") as f:
    content = f.read()

# Fix structural stop indentation and logic
old_structural = """        # LAYER 1: Underlying-Based Structural Stop (with sanity guard against corrupted spot quotes)
        spot_is_sane = (current_spot > 0 and abs(current_spot - pos.underlying_entry_spot) / max(1.0, pos.underlying_entry_spot) < 0.50)
        if spot_is_sane:
            if pos.option_type == "CE":
                # Structural Invalidation
                if current_spot <= pos.underlying_structural_sl:
                    return True, f"Layer 1 Structural Invalidation: {getattr(pos, 'asset_key', 'Spot')} {current_spot:.1f} broke swing low {pos.underlying_structural_sl:.1f}"

            # Target 1 (1.5R) reached
            if current_spot >= pos.target_1_spot:
                return True, f"Target 1 (1.5R) Reached @ Spot {current_spot:.1f} (Target: {pos.target_1_spot:.1f})"

            # Trailing Stop: 1 ATR movement after 1R
            risk_pts = pos.underlying_entry_spot - pos.underlying_structural_sl
            if (current_spot - pos.underlying_entry_spot) >= risk_pts:
                pos.is_trailing_active = True
                new_sl = pos.highest_spot - pos.atr_at_entry
                if new_sl > pos.underlying_structural_sl:
                    pos.underlying_structural_sl = new_sl

        elif pos.option_type == "PE":
            if current_spot >= pos.underlying_structural_sl:
                return True, f"Layer 1 Structural Invalidation: {getattr(pos, 'asset_key', 'Spot')} {current_spot:.1f} broke swing high {pos.underlying_structural_sl:.1f}"

            if current_spot <= pos.target_1_spot:
                return True, f"Target 1 (1.5R) Reached @ Spot {current_spot:.1f} (Target: {pos.target_1_spot:.1f})"

            risk_pts = pos.underlying_structural_sl - pos.underlying_entry_spot
            if (pos.underlying_entry_spot - current_spot) >= risk_pts:
                pos.is_trailing_active = True
                new_sl = pos.highest_spot + pos.atr_at_entry
                if new_sl < pos.underlying_structural_sl:
                    pos.underlying_structural_sl = new_sl

        return False, \"\""""

new_structural = """        # Minimum execution stabilization window: Allow at least 15 seconds for trade to breathe
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
                # Structural Invalidation (CE: underlying breaks swing low)
                if pos.underlying_structural_sl > 0 and current_spot <= pos.underlying_structural_sl:
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
                # Structural Invalidation (PE: underlying breaks swing high)
                if pos.underlying_structural_sl > 0 and current_spot >= pos.underlying_structural_sl:
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

        return False, \"\""""

assert old_structural in content, "old_structural not found"
content = content.replace(old_structural, new_structural)
print("[+] Patched structural exit logic in smart_edge_engine.py")

# Fix stop loss distances in SNIPER mode
old_sl_block = """        if trade_style == "SNIPER":
            # 🎯 QUICK HIGH-ACCURACY SCALP SETUP (Tight Stop Loss & Fast Capital Defense)
            if is_bn:
                sl_distance = 8.0  # Max 8 pts stop on BankNifty
                hard_sl = round(max(1.0, contract.estimated_premium - sl_distance), 2)
                if target_premium <= 0:
                    target_premium = round(contract.estimated_premium + 20.0, 2)
            elif asset_k == "SENSEX":
                sl_distance = 8.0  # Max 8 pts stop on Sensex
                hard_sl = round(max(1.0, contract.estimated_premium - sl_distance), 2)
                if target_premium <= 0:
                    target_premium = round(contract.estimated_premium + 22.0, 2)
            elif is_comm:
                sl_distance = 3.0  # Tight stop on Commodities
                hard_sl = round(max(1.0, contract.estimated_premium - sl_distance), 2)
                if target_premium <= 0:
                    target_premium = round(contract.estimated_premium + 10.0, 2)
            else: # NIFTY / Blue-chip Equity Options
                sl_distance = 3.5  # Max 3.5 pts stop on Nifty (~Rs.227 risk on 1 lot)
                hard_sl = round(max(1.0, contract.estimated_premium - sl_distance), 2)
                if target_premium <= 0:
                    target_premium = round(contract.estimated_premium + 12.0, 2)
            max_holding_seconds = 1800  # 30 minutes institutional trade window (No arbitrary 180s dumping)"""

new_sl_block = """        if trade_style == "SNIPER":
            # 🎯 REALISTIC VOLATILITY-ADJUSTED SCALP SETUP (1:2 R:R, Capital-Safe Sizing)
            if is_bn:
                sl_distance = 25.0  # Realistic 25 pts stop on BankNifty (~₹375 on 15 qty)
                hard_sl = round(max(5.0, contract.estimated_premium - sl_distance), 2)
                if target_premium <= 0:
                    target_premium = round(contract.estimated_premium + 50.0, 2)
            elif asset_k == "SENSEX":
                sl_distance = 30.0  # Realistic 30 pts stop on Sensex
                hard_sl = round(max(5.0, contract.estimated_premium - sl_distance), 2)
                if target_premium <= 0:
                    target_premium = round(contract.estimated_premium + 60.0, 2)
            elif is_comm:
                if asset_k == "NATGASMINI":
                    sl_distance = 1.2   # ~₹300 on 250 qty
                    hard_sl = round(max(0.2, contract.estimated_premium - sl_distance), 2)
                    if target_premium <= 0:
                        target_premium = round(contract.estimated_premium + 2.5, 2)
                elif asset_k in ["GOLDM", "SILVERM"]:
                    sl_distance = round(contract.estimated_premium * 0.12, 1) # 12% of premium
                    hard_sl = round(max(5.0, contract.estimated_premium - sl_distance), 2)
                    if target_premium <= 0:
                        target_premium = round(contract.estimated_premium * 1.25, 2)
                else: # CRUDEOILM
                    sl_distance = 18.0  # 18 pts stop on Crude Oil (~₹180 on 10 qty, ~6% of premium)
                    hard_sl = round(max(5.0, contract.estimated_premium - sl_distance), 2)
                    if target_premium <= 0:
                        target_premium = round(contract.estimated_premium + 36.0, 2)
            else: # NIFTY / Blue-chip Equity Options
                sl_distance = 14.0  # Realistic 14 pts stop on Nifty (~₹910 on 65 qty)
                hard_sl = round(max(5.0, contract.estimated_premium - sl_distance), 2)
                if target_premium <= 0:
                    target_premium = round(contract.estimated_premium + 28.0, 2)
            max_holding_seconds = 1800  # 30 minutes institutional trade window"""

assert old_sl_block in content, "old_sl_block not found"
content = content.replace(old_sl_block, new_sl_block)
print("[+] Patched stop loss distances in smart_edge_engine.py")

# Fix trailing stop & fee protection block in sniper
old_fee_block = """            fee_per_unit = round(105.0 / qty, 2)  # brokerage+STT+exch+GST buffer per unit (~1.6 pts on NIFTY)

            # Dynamic breathing room: Let setups breathe through standard bid-ask tick noise
            if is_comm:
                base_be = 5.0   # Breakeven lock at +5.0 pts expansion
                trail_buffer = 3.5
            elif is_bn:
                base_be = 12.0  # Breakeven lock at +12.0 pts expansion
                trail_buffer = 8.0
            else: # NIFTY & Equity Options
                base_be = 5.0   # Breakeven lock at +5.0 pts (survives 2-3 pt spread noise)
                trail_buffer = 3.5

            # 🛡️ DYNAMIC CAPITAL SHIELD (Loss Ceiling in Rupees):
            # Tied to planned risk budget for this position (e.g. 1.0% of Capital = ₹1,500 for ₹1.5L)
            planned_risk_budget = getattr(pos, "trade_risk_inr", 0.0)
            if planned_risk_budget <= 0.0:
                planned_risk_budget = max(1000.0, self.account_capital * 0.01)

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

            # 🔒 IMMEDIATE PROFIT LOCK & CONTINUOUS PEAK TRAILING:
            # As soon as trade moves into profit (+1.2 to +4 pts), IMMEDIATELY lock in net profit floor
            be_lock_price = round(pos.entry_premium + fee_per_unit + 0.3, 2)
            if prem_gain >= base_be and pos.hard_stop_premium < be_lock_price:
                pos.hard_stop_premium = be_lock_price
                pos.is_trailing_active = True
                pos.trailing_step_level = max(pos.trailing_step_level, 1)
                pos.locked_profit_inr = round(0.3 * pos.quantity, 2)
                self.log_sniper_event(
                    f"🔒 IMMEDIATE PROFIT LOCKED (+{prem_gain:.1f} pts): Guaranteed floor @ ₹{pos.hard_stop_premium:.2f} (Trade cannot lose).",
                    "LOCK"
                )
                print(f"[IMMEDIATE PROFIT LOCK] Guaranteed floor locked @ ₹{pos.hard_stop_premium:.2f}")

            # Continuous Peak Watermark Trailing:
            # Trail tightly behind highest price reached once in profit
            if prem_gain >= base_be:
                trail_sl = round(pos.highest_premium - trail_buffer, 2)
                if trail_sl > pos.hard_stop_premium:
                    pos.hard_stop_premium = trail_sl
                    pos.is_trailing_active = True
                    pos.trailing_step_level = max(pos.trailing_step_level, 2)
                    pos.locked_profit_inr = round((trail_sl - pos.entry_premium) * pos.quantity, 2)
                    self.log_sniper_event(
                        f"🚀 TRAILING PROFIT RIDER: Trailing {trail_buffer:.1f} pts behind peak (Peak: ₹{pos.highest_premium:.2f} | Floor: ₹{trail_sl:.2f} | +₹{pos.locked_profit_inr:,.0f} net)",
                        "TRAIL"
                    )"""

new_fee_block = """            # Round-trip brokerage (~₹40) + STT + GST + turnover buffer
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
                    )"""

assert old_fee_block in content, "old_fee_block not found"
content = content.replace(old_fee_block, new_fee_block)
print("[+] Patched fee protection and trailing in smart_edge_engine.py")

# Capital defaults in smart_edge_engine.py
content = re.sub(
    r"self\.account_capital: float = \d+\.\d+",
    "self.account_capital: float = float(getattr(config, 'account_capital', 150000.0))",
    content
)
content = re.sub(
    r"self\.daily_loss_limit: float = config\.max_daily_loss",
    "self.daily_loss_limit: float = max(float(getattr(config, 'max_daily_loss', 3000.0)), self.account_capital * 0.02)",
    content
)
content = re.sub(
    r"self\.max_daily_trades: int = config\.max_trades_per_day",
    "self.max_daily_trades: int = max(25, int(getattr(config, 'max_trades_per_day', 25)))",
    content
)

# Fix consecutive losses freeze (do not freeze for minor friction scratches)
old_recovery = """        elif self.consecutive_losses >= 2 and self.realized_pnl <= 0:
            # Dynamic Cooldown & Recovery Protocol (Instead of locking trader out for the day)
            # Enter a 15-minute patience window, then resume hunting strictly high-conviction (80%+) setups
            self.cooldown_until = datetime.datetime.now() + datetime.timedelta(minutes=15)
            self.in_recovery_mode = True
            self.is_kill_switch_active = False
            self.kill_switch_reason = \"\""""

new_recovery = """        elif self.consecutive_losses >= 3 and self.realized_pnl <= -self.daily_loss_limit * 0.5:
            # Dynamic Cooldown & Recovery Protocol: Only trips on true capital drawdown (> 50% of daily limit),
            # NOT for minor friction scratch trades!
            self.cooldown_until = datetime.datetime.now() + datetime.timedelta(minutes=5)
            self.in_recovery_mode = True
            self.is_kill_switch_active = False
            self.kill_switch_reason = \"\""""

if old_recovery in content:
    content = content.replace(old_recovery, new_recovery)
    print("[+] Patched recovery protocol in smart_edge_engine.py")

with open("smart_edge_engine.py", "w", encoding="utf-8") as f:
    f.write(content)
print("[+] Successfully updated smart_edge_engine.py")
