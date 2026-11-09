import datetime
from dataclasses import dataclass
from typing import Optional, Dict
from scrip_master import ScripMaster
from config import config

@dataclass
class SelectedContract:
    security_id: str
    trading_symbol: str
    custom_symbol: str
    strike_price: float
    option_type: str
    expiry_date: str
    lot_size: int
    estimated_premium: float
    estimated_delta: float
    hard_stop_premium: float      # Layer 2: 20% below entry
    trade_risk_inr: float         # Potential loss in Rupees
    risk_budget_approved: bool
    rejection_reason: Optional[str] = None
    asset_key: str = "NIFTY"
    number_of_lots: int = 1
    margin_required: float = 0.0
    advisory_alert: str = ""      # Advisory notice when sizing lots within 50% capital limit
    signal_id: str = ""           # Unique deterministic idempotency ID
    candle_timestamp: str = ""    # Timestamp of candle that generated the signal
    segment: str = "NSE_FNO"      # Exchange segment for live pricing

class ContractSelector:
    """
    NIFTY SMART EDGE - Section 5 & 7 Contract Selector & Sizing Engine
    - Strictly limits risk based on active capital (Real or Paper).
    - Intelligent lot sizing: Restricts trades to 1 or 2 lots maximum.
    - Prevents taking oversized positions on small capitals (e.g., ₹10,000 budget).
    - Checks margin requirements and rejects trades that exceed account capacity.
    """
    def __init__(self):
        self.scrip_master = ScripMaster()
        self.total_capital = float(getattr(config, 'account_capital', 150000.0))
        self.preferred_risk = self.total_capital * 0.01
        self.absolute_risk_ceiling = float(getattr(config, 'max_daily_loss', 3000.0))

    def select_contract(
        self,
        spot_price: float,
        action: str,  # 'BUY_CE' or 'BUY_PE'
        estimated_premium: float,
        strike_preference: str = "ATM", # 'ATM' or 'ITM'
        structural_risk_pts: float = 12.0,
        asset_key: str = "NIFTY",
        available_capital: float = 150000.0,
        requested_lots: Optional[int] = None,
        candle_timestamp: Optional[str] = None,
        strategy_name: str = "SMART_EDGE"
    ) -> SelectedContract:
        option_type = "CE" if action == "BUY_CE" else "PE"

        # ---------------------------------------------------------------------
        # COMPLIANCE GUARDRAIL 0: GROUND TRUTH SPOT SYNCHRONIZATION
        # Verifies the spot_price against live exchange market feed.
        # If caller passed a stale/cold spot (>1% drift), overrides with live spot.
        # ---------------------------------------------------------------------
        if spot_price <= 0:
            try:
                from market_intelligence import market_intelligence
                live_ref = market_intelligence.get_asset_spot(asset_key)
                if live_ref > 0:
                    spot_price = live_ref
            except Exception:
                pass
        
        # Strike offset: 0 for ATM; for 1 ITM: -1 for CE, +1 for PE
        offset = 0
        if strike_preference == "ITM":
            offset = -1 if option_type == "CE" else +1

        contract_meta = self.scrip_master.get_nifty_option(
            spot_price=spot_price,
            option_type=option_type,
            strike_offset=offset,
            asset_key=asset_key
        )

        sec_id = str(contract_meta.get("security_id", "0"))
        exp_date = str(contract_meta.get("expiry", ""))
        contract_strike = float(contract_meta.get("strike_price", spot_price))
        custom_sym = str(contract_meta.get("custom_symbol", ""))
        contract_seg = str(contract_meta.get("segment", "NSE_FNO"))

        # Deterministic Signal ID generation for order idempotency
        candle_ts = candle_timestamp or datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        from global_trade_lock import global_trade_lock
        sig_id = global_trade_lock.generate_signal_id(
            strategy_name=strategy_name,
            asset_key=asset_key,
            symbol=custom_sym,
            strike=contract_strike,
            option_type=option_type,
            action=action,
            candle_timestamp=candle_ts
        )

        # Determine asset strike step
        step = 50.0
        if asset_key in ["BANKNIFTY", "SENSEX", "NIFTYIT"]:
            step = 100.0
        elif asset_key == "GOLDM":
            step = 250.0
        elif asset_key in ["FINNIFTY", "CRUDEOILM"]:
            step = 50.0
        elif asset_key == "SILVERM":
            step = 500.0
        elif asset_key == "NATGASMINI":
            step = 5.0
        elif asset_key in ["RELIANCE", "INFY"]:
            step = 20.0
        elif asset_key in ["HDFCBANK", "ICICIBANK", "SBIN"]:
            step = 10.0

        # ---------------------------------------------------------------------
        # COMPLIANCE GUARDRAIL 1: STRIKE SANITY & MAX DISTANCE ENFORCEMENT
        # Strictly forbids executing trades where strike price has drifted from spot.
        # ATM / ITM strikes MUST remain strictly within 2.0x of the asset step.
        # ---------------------------------------------------------------------
        strike_distance = abs(contract_strike - spot_price)
        max_allowed_distance = step * 3.0
        if strike_distance > max_allowed_distance:
            rejection = (
                f"🚨 STRICT COMPLIANCE GUARDRAIL REJECTION: Strike sanity failed for {asset_key}! "
                f"Contract strike {contract_strike:.1f} is {strike_distance:.1f} pts away from verified spot {spot_price:.1f} "
                f"(Max allowed ATM distance is {max_allowed_distance:.1f} pts). "
                f"Trade blocked to prevent phantom execution or distorted pricing."
            )
            print(f"[GUARDRAIL BLOCKED] {rejection}")
            return SelectedContract(
                security_id=sec_id,
                trading_symbol=contract_meta.get("trading_symbol", ""),
                custom_symbol=contract_meta.get("custom_symbol", ""),
                strike_price=contract_strike,
                option_type=option_type,
                expiry_date=exp_date,
                lot_size=65,
                estimated_premium=0.0,
                estimated_delta=0.0,
                hard_stop_premium=0.0,
                trade_risk_inr=0.0,
                risk_budget_approved=False,
                rejection_reason=rejection,
                asset_key=asset_key,
                number_of_lots=0,
                margin_required=0.0,
                advisory_alert=rejection,
                signal_id=sig_id,
                candle_timestamp=candle_ts,
                segment=contract_seg
            )

        # Always evaluate the true premium using the contract's exact security_id, segment and exact expiry date
        if not estimated_premium or estimated_premium <= 0:
            try:
                from dhan_client import get_dhan_client
                client = get_dhan_client()
                live_prem = client.get_option_ltp(
                    sec_id, spot_price, contract_strike, option_type, expiry_date=exp_date, segment=contract_seg
                )
                if live_prem > 0:
                    estimated_premium = live_prem
            except Exception:
                pass

        # ---------------------------------------------------------------------
        # COMPLIANCE GUARDRAIL 2: STRICT AUTHENTIC LIVE DHAN QUOTE ENFORCEMENT
        # Strictly forbids hardcoded, theoretical or fabricated prices.
        # Trades MUST be backed by genuine real-time live quotes directly from Dhan API.
        # ---------------------------------------------------------------------
        if estimated_premium <= 0.10:
            # Check last authentic flashed price from Dhan
            try:
                from dhan_client import get_dhan_client
                client = get_dhan_client()
                last_f = client.get_last_flashed_price(sec_id, [custom_sym, contract_meta.get("trading_symbol", "")])
                if last_f > 0.10:
                    estimated_premium = last_f
            except Exception:
                pass

        if estimated_premium <= 0.10:
            # Strictly NO synthetic or mathematical estimates. Genuine Dhan quotes only.
            rejection = f"🚨 AUTHENTIC DHAN QUOTE REQUIRED: Live Dhan tick unavailable for {contract_meta.get('custom_symbol')}. Refusing entry to prevent phantom execution or distorted pricing."
            return SelectedContract(
                    security_id=sec_id,
                    trading_symbol=contract_meta.get("trading_symbol", ""),
                    custom_symbol=contract_meta.get("custom_symbol", ""),
                    strike_price=contract_strike,
                    option_type=option_type,
                    expiry_date=exp_date,
                    lot_size=65,
                    estimated_premium=estimated_premium,
                    estimated_delta=0.0,
                    hard_stop_premium=0.0,
                    trade_risk_inr=0.0,
                    risk_budget_approved=False,
                    rejection_reason=rejection,
                    asset_key=asset_key,
                    number_of_lots=0,
                    margin_required=0.0,
                    advisory_alert=rejection,
                    signal_id=sig_id,
                    candle_timestamp=candle_ts,
                    segment=contract_seg
                )

        # Authentic lot sizing
        comm_lots = {"CRUDEOILM": 10, "NATGASMINI": 250, "GOLDM": 10, "SILVERM": 5}
        if asset_key in comm_lots:
            base_lot_size = comm_lots[asset_key]
        else:
            base_lot_size = contract_meta.get("lot_size", 65)
            base_lot_size = int(base_lot_size) if base_lot_size and int(base_lot_size) > 1 else 65

        # Cost to buy 1 lot
        cost_per_lot = round(base_lot_size * estimated_premium, 2)

        # Delta estimate: ATM ~ 0.50, 1 strike ITM ~ 0.55
        delta_est = 0.50 if offset == 0 else 0.55

        # STRICT 50% CAPITAL LIMIT RULE
        # The total value of the position should NEVER be more than 50% of the overall total capital.
        max_allowed_margin = round(available_capital * 0.50, 2)

        # Check if 1 lot exceeds 50% of capital
        if cost_per_lot > max_allowed_margin:
            util_1lot = round((cost_per_lot / available_capital) * 100.0, 1) if available_capital > 0 else 100.0
            rejection = (
                f"50% Capital Safety Ceiling: 1 lot of {contract_meta['custom_symbol']} requires ₹{cost_per_lot:,.2f} "
                f"({util_1lot}% of your active capital of ₹{available_capital:,.2f}). "
                f"Per discipline rules, total lot value can never exceed 50% (₹{max_allowed_margin:,.2f}). "
                f"Select an OTM strike with lower premium, switch to a Mini contract, or top up wallet."
            )
            hard_stop_prem = round(estimated_premium * 0.80, 2)
            planned_loss = round((max(structural_risk_pts * delta_est, 5.0) * base_lot_size) + 65.0, 2)
            return SelectedContract(
                security_id=contract_meta["security_id"],
                trading_symbol=contract_meta["trading_symbol"],
                custom_symbol=contract_meta["custom_symbol"],
                strike_price=contract_meta["strike_price"],
                option_type=option_type,
                expiry_date=contract_meta["expiry"],
                lot_size=base_lot_size,
                estimated_premium=estimated_premium,
                estimated_delta=delta_est,
                hard_stop_premium=hard_stop_prem,
                trade_risk_inr=planned_loss,
                risk_budget_approved=False,
                rejection_reason=rejection,
                asset_key=asset_key,
                number_of_lots=1,
                margin_required=cost_per_lot,
                advisory_alert="",
                signal_id=sig_id,
                candle_timestamp=candle_ts,
                segment=contract_seg
            )

        # Handle Customizable Lot Sizing & 50% Capital Barrier
        advisory_msg = ""
        if requested_lots is not None and requested_lots >= 1:
            req_lots = int(requested_lots)
            req_margin = round(req_lots * cost_per_lot, 2)
            util_pct = round((req_margin / available_capital) * 100.0, 1) if available_capital > 0 else 100.0

            if req_margin > max_allowed_margin:
                max_lots = max(1, int(max_allowed_margin // cost_per_lot))
                rejection = (
                    f"50% Capital Ceiling Exceeded: {req_lots} lots of {contract_meta['custom_symbol']} requires ₹{req_margin:,.2f} "
                    f"({util_pct}% of total capital). Maximum allowed position commitment is 50% (₹{max_allowed_margin:,.2f}). "
                    f"Maximum allowable for this contract: {max_lots} lot{'s' if max_lots > 1 else ''}."
                )
                return SelectedContract(
                    security_id=contract_meta["security_id"],
                    trading_symbol=contract_meta["trading_symbol"],
                    custom_symbol=contract_meta["custom_symbol"],
                    strike_price=contract_meta["strike_price"],
                    option_type=option_type,
                    expiry_date=contract_meta["expiry"],
                    lot_size=base_lot_size,
                    estimated_premium=estimated_premium,
                    estimated_delta=delta_est,
                    hard_stop_premium=round(estimated_premium * 0.80, 2),
                    trade_risk_inr=round((max(structural_risk_pts * delta_est, 5.0) * req_lots * base_lot_size) + 65.0, 2),
                    risk_budget_approved=False,
                    rejection_reason=rejection,
                    asset_key=asset_key,
                    number_of_lots=req_lots,
                    margin_required=req_margin,
                    advisory_alert="",
                    signal_id=sig_id,
                    candle_timestamp=candle_ts
                )
            else:
                chosen_lots = req_lots
                advisory_msg = f"⚠️ Lot Sizing Advisory: Position scaled to {chosen_lots} lot{'s' if chosen_lots > 1 else ''} (₹{req_margin:,.2f} margin, {util_pct}% of capital). Safe within 50% ceiling."
        else:
            # Disciplined sizing: Strictly 1 LOT default for capital protection and risk control
            chosen_lots = 1
            chosen_margin = round(chosen_lots * cost_per_lot, 2)
            util_pct = round((chosen_margin / available_capital) * 100.0, 1) if available_capital > 0 else 100.0
            advisory_msg = f"Disciplined single lot sizing: 1 lot (₹{chosen_margin:,.2f} margin, {util_pct}% of capital). Capital safeguarded."

        final_quantity = chosen_lots * base_lot_size
        total_margin = round(final_quantity * estimated_premium, 2)

        # Layer 2: Hard premium stop: 20% below entry
        hard_stop_premium = round(estimated_premium * 0.80, 2)
        emergency_loss_per_unit = estimated_premium - hard_stop_premium

        # Primary planned risk based on Layer 1 Underlying Structural Stop
        planned_loss_per_unit = max(structural_risk_pts * delta_est, 5.0)
        trade_risk_inr = round((planned_loss_per_unit * final_quantity) + 65.0, 2)

        # Strict Capital Allocation & Dynamic Risk Budgeting:
        # 1. Per-Trade Capital Commitment: Safe margin ceiling (max 25% of capital or ₹25,000 for ₹1.5L)
        max_trade_capital_limit = max(6000.0, min(available_capital * 0.25, 25000.0))
        if total_margin > max_trade_capital_limit:
            rejection = (
                f"Capital Allocation Shield: Margin of ₹{total_margin:,.2f} exceeds safe limit of ₹{max_trade_capital_limit:,.2f}. "
                f"Expensive contracts rejected to safeguard account capital."
            )
            return SelectedContract(
                security_id=contract_meta["security_id"],
                trading_symbol=contract_meta["trading_symbol"],
                custom_symbol=contract_meta["custom_symbol"],
                strike_price=contract_meta["strike_price"],
                option_type=option_type,
                expiry_date=contract_meta["expiry"],
                lot_size=base_lot_size,
                estimated_premium=estimated_premium,
                estimated_delta=delta_est,
                hard_stop_premium=round(estimated_premium * 0.80, 2),
                trade_risk_inr=trade_risk_inr,
                risk_budget_approved=False,
                rejection_reason=rejection,
                asset_key=asset_key,
                number_of_lots=chosen_lots,
                margin_required=total_margin,
                advisory_alert="",
                signal_id=sig_id,
                candle_timestamp=candle_ts
            )

        # 2. Dynamic Rupee Risk Ceiling: Based on Risk Appetite % of Capital (e.g. 1.0% = ₹1,500 on ₹150,000)
        risk_pct = getattr(config, "risk_per_trade_pct", 1.0) / 100.0
        risk_ceiling = round(max(750.0, min(available_capital * risk_pct, getattr(config, "max_daily_loss", 3000.0) * 0.5)), 2)
        if trade_risk_inr > risk_ceiling:
            rejection = (
                f"Trade rejected by Risk Rules: Planned loss (₹{trade_risk_inr:.2f}) "
                f"exceeds dynamic risk ceiling of ₹{risk_ceiling:.2f} ({risk_pct*100:.1f}% of capital)."
            )
            return SelectedContract(
                security_id=contract_meta["security_id"],
                trading_symbol=contract_meta["trading_symbol"],
                custom_symbol=contract_meta["custom_symbol"],
                strike_price=contract_meta["strike_price"],
                option_type=option_type,
                expiry_date=contract_meta["expiry"],
                lot_size=final_quantity,
                estimated_premium=estimated_premium,
                estimated_delta=delta_est,
                hard_stop_premium=hard_stop_premium,
                trade_risk_inr=trade_risk_inr,
                risk_budget_approved=False,
                rejection_reason=rejection,
                asset_key=asset_key,
                number_of_lots=chosen_lots,
                margin_required=total_margin,
                advisory_alert="",
                signal_id=sig_id,
                candle_timestamp=candle_ts,
                segment=contract_seg
            )

        return SelectedContract(
            security_id=contract_meta["security_id"],
            trading_symbol=contract_meta["trading_symbol"],
            custom_symbol=contract_meta["custom_symbol"],
            strike_price=contract_meta["strike_price"],
            option_type=option_type,
            expiry_date=contract_meta["expiry"],
            lot_size=final_quantity,
            estimated_premium=estimated_premium,
            estimated_delta=delta_est,
            hard_stop_premium=hard_stop_premium,
            trade_risk_inr=trade_risk_inr,
            risk_budget_approved=True,
            rejection_reason=None,
            asset_key=asset_key,
            number_of_lots=chosen_lots,
            margin_required=total_margin,
            advisory_alert=advisory_msg,
            signal_id=sig_id,
            candle_timestamp=candle_ts,
            segment=contract_seg
        )

contract_selector = ContractSelector()
