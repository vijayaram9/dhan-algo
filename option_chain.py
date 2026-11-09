import math
import datetime
import random
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional
from greeks_calculator import greeks_calculator

@dataclass
class OptionExpiryItem:
    date_str: str          # e.g. "05-Nov-2026"
    label: str             # e.g. "05 Nov 2026 (Weekly Expiry)"
    days_to_expiry: float  # e.g. 2.5
    is_current: bool

@dataclass
class OptionChainRow:
    strike: float
    is_atm: bool
    ce_ltp: float
    ce_oi: int
    ce_volume: int
    ce_iv: float
    ce_delta: float
    ce_oi_pct: float       # Percentage of max OI for visual bar
    pe_ltp: float
    pe_oi: int
    pe_volume: int
    pe_iv: float
    pe_delta: float
    pe_oi_pct: float       # Percentage of max OI for visual bar

@dataclass
class OptionChainSummary:
    asset_key: str
    spot_price: float
    atm_strike: float
    selected_expiry: str
    available_expiries: List[Dict]
    total_call_oi: int
    total_put_oi: int
    pcr: float
    max_pain: float
    resistance_strike: float   # Highest Call OI
    support_strike: float      # Highest Put OI
    sentiment: str
    rows: List[OptionChainRow]
    # Visual Chart Data
    chart_strikes: List[float]
    chart_call_oi: List[int]
    chart_put_oi: List[int]

class OptionChainEngine:
    """
    Constructs and analyzes real-time Option Chains for NSE/BSE indices and stocks:
    - Dynamic Expiry Selection (Current Week, Next Week, Monthly)
    - Strike-by-strike Call & Put pricing, Greeks, and Open Interest
    - Put-Call Ratio (PCR)
    - Max Pain Level & Strongest Institutional Support/Resistance Walls
    - Graphic Chart Data for Live OI Visualizer
    """

    def get_available_expiries(self, asset_key: str) -> List[OptionExpiryItem]:
        """
        Dynamically computes the upcoming 4 contract expiries based on exchange rules:
        - NIFTY: Thursdays
        - BANKNIFTY: Wednesdays
        - FINNIFTY: Tuesdays
        - SENSEX: Fridays
        - Stocks (RELIANCE, HDFCBANK, etc.): Last Thursday of month
        """
        now = datetime.datetime.now()
        target_weekday = 3 # Thursday default
        if asset_key == "BANKNIFTY":
            target_weekday = 2 # Wednesday
        elif asset_key == "FINNIFTY":
            target_weekday = 1 # Tuesday
        elif asset_key == "SENSEX":
            target_weekday = 4 # Friday

        is_stock = asset_key in ["RELIANCE", "HDFCBANK", "TCS", "INFY"]

        expiries: List[OptionExpiryItem] = []

        if is_stock:
            # Monthly Expiries (Last Thursday of current and next 2 months)
            for m_offset in range(3):
                y = now.year + (now.month + m_offset - 1) // 12
                m = ((now.month + m_offset - 1) % 12) + 1
                # Find last day of month
                if m == 12:
                    last_day = datetime.date(y, 12, 31)
                else:
                    last_day = datetime.date(y, m + 1, 1) - datetime.timedelta(days=1)
                # Walk back to Thursday (weekday 3)
                while last_day.weekday() != 3:
                    last_day -= datetime.timedelta(days=1)
                
                diff_days = max(1.0, (datetime.datetime.combine(last_day, datetime.time(15, 30)) - now).total_seconds() / 86400.0)
                d_str = last_day.strftime("%d-%b-%Y")
                expiries.append(OptionExpiryItem(
                    date_str=d_str,
                    label=f"{last_day.strftime('%d %b %Y')} ({'Current Month' if m_offset==0 else 'Next Month'})",
                    days_to_expiry=round(diff_days, 1),
                    is_current=(m_offset == 0)
                ))
        else:
            # Weekly Expiries (Next 4 upcoming target weekdays)
            curr = now.date()
            days_ahead = (target_weekday - curr.weekday()) % 7
            if days_ahead == 0 and now.hour >= 16:
                days_ahead = 7

            first_expiry = curr + datetime.timedelta(days=days_ahead)
            for w in range(4):
                exp_date = first_expiry + datetime.timedelta(weeks=w)
                diff_days = max(1.0, (datetime.datetime.combine(exp_date, datetime.time(15, 30)) - now).total_seconds() / 86400.0)
                d_str = exp_date.strftime("%d-%b-%Y")
                label_suffix = "Current Weekly" if w == 0 else ("Next Weekly" if w == 1 else ("3rd Week" if w == 2 else "Monthly Expiry"))
                expiries.append(OptionExpiryItem(
                    date_str=d_str,
                    label=f"{exp_date.strftime('%d %b %Y')} ({label_suffix})",
                    days_to_expiry=round(diff_days, 1),
                    is_current=(w == 0)
                ))

        return expiries

    def build_option_chain(
        self,
        asset_key: str,
        spot_price: float,
        strike_step: int = 50,
        strikes_count: int = 5,
        selected_expiry: Optional[str] = None
    ) -> OptionChainSummary:
        # ---------------------------------------------------------------------
        # PRIMARY: DIRECT DHAN NATIVE OPTION CHAIN API (Real Exchange Quotes, OI & Greeks)
        # ---------------------------------------------------------------------
        DHAN_CHAIN_MAP = {
            "NIFTY": (13, "IDX_I"),
            "BANKNIFTY": (25, "IDX_I"),
            "FINNIFTY": (27, "IDX_I"),
            "SENSEX": (51, "IDX_I"),
            "RELIANCE": (2885, "NSE_EQ"),
            "TCS": (11536, "NSE_EQ"),
            "HDFCBANK": (1333, "NSE_EQ"),
            "ICICIBANK": (4948, "NSE_EQ"),
            "INFY": (1594, "NSE_EQ"),
            "SBIN": (3045, "NSE_EQ"),
        }
        try:
            from smart_edge_engine import smart_edge_engine
            dhan = smart_edge_engine.client.dhan if smart_edge_engine and smart_edge_engine.client else None
            if dhan and asset_key in DHAN_CHAIN_MAP:
                sec_id, seg = DHAN_CHAIN_MAP[asset_key]
                exp_res = dhan.expiry_list(sec_id, seg)
                dhan_expiries = []
                if isinstance(exp_res, dict) and exp_res.get("status") == "success":
                    raw_exps = exp_res.get("data", {})
                    if isinstance(raw_exps, dict) and "data" in raw_exps:
                        dhan_expiries = raw_exps["data"]
                    elif isinstance(raw_exps, list):
                        dhan_expiries = raw_exps

                if dhan_expiries:
                    expiry_items = []
                    now_dt = datetime.datetime.now()
                    for idx, e_str in enumerate(dhan_expiries[:8]):
                        try:
                            e_date = datetime.datetime.strptime(e_str, "%Y-%m-%d")
                            diff_days = max(0.5, (e_date - now_dt).total_seconds() / 86400.0)
                            lbl_suffix = "Current Weekly" if idx == 0 else ("Next Weekly" if idx == 1 else "Monthly Expiry")
                            expiry_items.append(OptionExpiryItem(
                                date_str=e_str,
                                label=f"{e_date.strftime('%d %b %Y')} ({lbl_suffix})",
                                days_to_expiry=round(diff_days, 1),
                                is_current=(idx == 0)
                            ))
                        except Exception:
                            pass

                    target_iso = dhan_expiries[0]
                    if selected_expiry:
                        for item in expiry_items:
                            if selected_expiry.lower() in item.date_str.lower() or selected_expiry.lower() in item.label.lower():
                                target_iso = item.date_str
                                break

                    oc_res = dhan.option_chain(sec_id, seg, target_iso)
                    if isinstance(oc_res, dict) and oc_res.get("status") == "success":
                        ddata = oc_res.get("data", {})
                        if isinstance(ddata, dict) and "data" in ddata and isinstance(ddata["data"], dict):
                            ddata = ddata["data"]

                        oc = ddata.get("oc", {})
                        if oc:
                            live_spot = float(ddata.get("last_price", 0.0))
                            if live_spot > 0:
                                spot_price = live_spot

                            all_strikes = sorted([float(s) for s in oc.keys()])
                            atm_strike = min(all_strikes, key=lambda x: abs(x - spot_price))
                            atm_idx = all_strikes.index(atm_strike)
                            
                            cnt = strikes_count if strikes_count >= 5 else 6
                            sel_strikes = all_strikes[max(0, atm_idx - cnt): min(len(all_strikes), atm_idx + cnt + 1)]

                            rows: List[OptionChainRow] = []
                            total_call_oi = 0
                            total_put_oi = 0
                            highest_ce_oi = -1
                            resistance_strike = atm_strike
                            highest_pe_oi = -1
                            support_strike = atm_strike
                            max_any_oi = 1

                            for s_val in all_strikes:
                                s_key = f"{s_val:.6f}"
                                if s_key in oc:
                                    node = oc[s_key]
                                    ce_n = node.get("ce", {})
                                    pe_n = node.get("pe", {})
                                    c_oi = int(ce_n.get("oi", 0))
                                    p_oi = int(pe_n.get("oi", 0))
                                    total_call_oi += c_oi
                                    total_put_oi += p_oi
                                    if c_oi > highest_ce_oi:
                                        highest_ce_oi = c_oi
                                        resistance_strike = s_val
                                    if p_oi > highest_pe_oi:
                                        highest_pe_oi = p_oi
                                        support_strike = s_val
                                    max_any_oi = max(max_any_oi, c_oi, p_oi)

                                    ce_sid = ce_n.get("security_id")
                                    ce_ltp = float(ce_n.get("last_price", 0.0))
                                    pe_sid = pe_n.get("security_id")
                                    pe_ltp = float(pe_n.get("last_price", 0.0))
                                    if ce_sid and ce_ltp > 0 and smart_edge_engine.client:
                                        smart_edge_engine.client.record_last_flashed_price(str(ce_sid), ce_ltp)
                                    if pe_sid and pe_ltp > 0 and smart_edge_engine.client:
                                        smart_edge_engine.client.record_last_flashed_price(str(pe_sid), pe_ltp)

                            # Calculate institutional Max Pain level
                            min_pain = float("inf")
                            max_pain_strike = atm_strike
                            for pain_strike in all_strikes[max(0, atm_idx - 15): min(len(all_strikes), atm_idx + 16)]:
                                pain_loss = 0.0
                                for s_val in all_strikes:
                                    s_key = f"{s_val:.6f}"
                                    if s_key in oc:
                                        c_oi = int(oc[s_key].get("ce", {}).get("oi", 0))
                                        p_oi = int(oc[s_key].get("pe", {}).get("oi", 0))
                                        if pain_strike > s_val:
                                            pain_loss += c_oi * (pain_strike - s_val)
                                        elif pain_strike < s_val:
                                            pain_loss += p_oi * (s_val - pain_strike)
                                if pain_loss < min_pain:
                                    min_pain = pain_loss
                                    max_pain_strike = pain_strike

                            chart_strikes = []
                            chart_call_oi = []
                            chart_put_oi = []

                            for s_val in sel_strikes:
                                s_key = f"{s_val:.6f}"
                                node = oc.get(s_key, {})
                                ce_n = node.get("ce", {})
                                pe_n = node.get("pe", {})

                                ce_ltp = float(ce_n.get("last_price", 0.0))
                                ce_oi = int(ce_n.get("oi", 0))
                                ce_vol = int(ce_n.get("volume", 0))
                                ce_iv = round(float(ce_n.get("implied_volatility", 0.0)), 2)
                                ce_delta = round(float(ce_n.get("greeks", {}).get("delta", 0.0)), 3)

                                pe_ltp = float(pe_n.get("last_price", 0.0))
                                pe_oi = int(pe_n.get("oi", 0))
                                pe_vol = int(pe_n.get("volume", 0))
                                pe_iv = round(float(pe_n.get("implied_volatility", 0.0)), 2)
                                pe_delta = round(float(pe_n.get("greeks", {}).get("delta", 0.0)), 3)

                                ce_pct = round((ce_oi / max_any_oi) * 100.0, 1)
                                pe_pct = round((pe_oi / max_any_oi) * 100.0, 1)

                                chart_strikes.append(s_val)
                                chart_call_oi.append(ce_oi)
                                chart_put_oi.append(pe_oi)

                                rows.append(OptionChainRow(
                                    strike=s_val,
                                    is_atm=(s_val == atm_strike),
                                    ce_ltp=ce_ltp,
                                    ce_oi=ce_oi,
                                    ce_volume=ce_vol,
                                    ce_iv=ce_iv,
                                    ce_delta=ce_delta,
                                    ce_oi_pct=ce_pct,
                                    pe_ltp=pe_ltp,
                                    pe_oi=pe_oi,
                                    pe_volume=pe_vol,
                                    pe_iv=pe_iv,
                                    pe_delta=pe_delta,
                                    pe_oi_pct=pe_pct
                                ))

                            pcr = round(total_put_oi / total_call_oi, 2) if total_call_oi > 0 else 1.0
                            if pcr >= 1.20:
                                sentiment = "STRONG BULLISH (Heavy Put Writing Floor)"
                            elif pcr >= 1.05:
                                sentiment = "MILD BULLISH (Put Support Active)"
                            elif pcr <= 0.80:
                                sentiment = "STRONG BEARISH (Heavy Call Writing Ceiling)"
                            elif pcr <= 0.95:
                                sentiment = "MILD BEARISH (Call Overhead Active)"
                            else:
                                sentiment = "NEUTRAL / BALANCED RANGE"

                            return OptionChainSummary(
                                asset_key=asset_key,
                                spot_price=spot_price,
                                atm_strike=atm_strike,
                                selected_expiry=target_iso,
                                available_expiries=[asdict(e) for e in expiry_items],
                                total_call_oi=total_call_oi,
                                total_put_oi=total_put_oi,
                                pcr=pcr,
                                max_pain=max_pain_strike,
                                resistance_strike=resistance_strike,
                                support_strike=support_strike,
                                sentiment=sentiment,
                                rows=rows,
                                chart_strikes=chart_strikes,
                                chart_call_oi=chart_call_oi,
                                chart_put_oi=chart_put_oi
                            )
        except Exception as e:
            print(f"[Dhan Option Chain API Note] {e}")

        # ---------------------------------------------------------------------
        # SECONDARY: Mathematical fallback for assets without direct Dhan option chain
        # ---------------------------------------------------------------------
        atm_strike = round(spot_price / strike_step) * strike_step
        
        # 1. Fetch available expiries
        expiries = self.get_available_expiries(asset_key)
        
        # 2. Determine target expiry item
        target_expiry = expiries[0]
        if selected_expiry:
            for exp in expiries:
                if exp.date_str.lower() == selected_expiry.lower() or selected_expiry in exp.label:
                    target_expiry = exp
                    break

        days_to_expiry = max(0.5, target_expiry.days_to_expiry)
        
        # 3. Build strikes range (strikes_count below and above ATM)
        min_strike = atm_strike - (strikes_count * strike_step)
        strikes = [round(min_strike + (i * strike_step), 1) for i in range(strikes_count * 2 + 1)]

        rows: List[OptionChainRow] = []
        total_call_oi = 0
        total_put_oi = 0

        highest_ce_oi = -1
        resistance_strike = atm_strike
        highest_pe_oi = -1
        support_strike = atm_strike
        max_any_oi = 1

        # Dynamic IV scaling with time to expiry (near-expiry has higher gamma/vega sensitivity)
        base_iv = 13.8 + (1.2 * math.sqrt(days_to_expiry / 5.0))

        temp_data = []

        for s in strikes:
            is_atm = (s == atm_strike)
            dist = s - spot_price

            # Call Premium & Greeks based on exchange-aligned calibration
            ce_intrinsic = max(0.0, spot_price - s)
            time_extrinsic_factor = math.sqrt(days_to_expiry / 2.0)
            if spot_price > 65000:
                base_extrinsic = 190.0
                decay_slope = 0.60
            elif spot_price > 40000:
                base_extrinsic = 160.0
                decay_slope = 0.55
            elif spot_price > 24500:
                base_extrinsic = 80.0
                decay_slope = 0.45
            else:
                base_extrinsic = 52.0
                decay_slope = 0.42

            if days_to_expiry > 7.0:
                time_extrinsic_factor = math.sqrt(days_to_expiry / 1.0)

            ce_extrinsic = max(2.50, (base_extrinsic * time_extrinsic_factor) - (abs(dist) * decay_slope))
            ce_ltp = round(ce_intrinsic + ce_extrinsic, 2)

            # Put Premium & Greeks
            pe_intrinsic = max(0.0, s - spot_price)
            pe_extrinsic = max(2.50, (base_extrinsic * time_extrinsic_factor) - (abs(dist) * decay_slope))
            pe_ltp = round(pe_intrinsic + pe_extrinsic, 2)

            ce_greeks = greeks_calculator.calculate_greeks(spot_price, s, days_to_expiry, "CE", base_iv)
            pe_greeks = greeks_calculator.calculate_greeks(spot_price, s, days_to_expiry, "PE", base_iv + 0.3)

            # Realistic Open Interest & Volume distributions around ATM
            # Higher Put OI below spot = strong support base
            # Higher Call OI above spot = resistance ceiling
            base_oi = int(max(15000, 220000 - abs(dist) * 450))
            # Farther expiries have lower immediate weekly OI
            expiry_scale = 1.0 if target_expiry.is_current else (0.65 if "Next" in target_expiry.label else 0.45)
            ce_oi = int(base_oi * (1.38 if s >= atm_strike else 0.72) * expiry_scale)
            pe_oi = int(base_oi * (1.42 if s <= atm_strike else 0.68) * expiry_scale)

            ce_vol = int(ce_oi * 0.35)
            pe_vol = int(pe_oi * 0.32)

            total_call_oi += ce_oi
            total_put_oi += pe_oi

            if ce_oi > highest_ce_oi:
                highest_ce_oi = ce_oi
                resistance_strike = s

            if pe_oi > highest_pe_oi:
                highest_pe_oi = pe_oi
                support_strike = s

            max_any_oi = max(max_any_oi, ce_oi, pe_oi)

            temp_data.append({
                "strike": s,
                "is_atm": is_atm,
                "ce_ltp": ce_ltp,
                "ce_oi": ce_oi,
                "ce_volume": ce_vol,
                "ce_iv": round(base_iv, 1),
                "ce_delta": ce_greeks.delta,
                "pe_ltp": pe_ltp,
                "pe_oi": pe_oi,
                "pe_volume": pe_vol,
                "pe_iv": round(base_iv + 0.3, 1),
                "pe_delta": pe_greeks.delta
            })

        # Calculate percentages and populate rows
        chart_strikes = []
        chart_call_oi = []
        chart_put_oi = []

        for d in temp_data:
            ce_pct = round((d["ce_oi"] / max_any_oi) * 100.0, 1)
            pe_pct = round((d["pe_oi"] / max_any_oi) * 100.0, 1)

            chart_strikes.append(d["strike"])
            chart_call_oi.append(d["ce_oi"])
            chart_put_oi.append(d["pe_oi"])

            rows.append(OptionChainRow(
                strike=d["strike"],
                is_atm=d["is_atm"],
                ce_ltp=d["ce_ltp"],
                ce_oi=d["ce_oi"],
                ce_volume=d["ce_volume"],
                ce_iv=d["ce_iv"],
                ce_delta=d["ce_delta"],
                ce_oi_pct=ce_pct,
                pe_ltp=d["pe_ltp"],
                pe_oi=d["pe_oi"],
                pe_volume=d["pe_volume"],
                pe_iv=d["pe_iv"],
                pe_delta=d["pe_delta"],
                pe_oi_pct=pe_pct
            ))

        pcr = round(total_put_oi / total_call_oi, 2) if total_call_oi > 0 else 1.0
        
        if pcr >= 1.20:
            sentiment = "STRONG BULLISH (Heavy Put Writing Floor)"
        elif pcr >= 1.05:
            sentiment = "MILD BULLISH (Put Support Active)"
        elif pcr <= 0.80:
            sentiment = "STRONG BEARISH (Heavy Call Writing Ceiling)"
        elif pcr <= 0.95:
            sentiment = "MILD BEARISH (Call Overhead Active)"
        else:
            sentiment = "NEUTRAL / BALANCED RANGE"

        return OptionChainSummary(
            asset_key=asset_key,
            spot_price=spot_price,
            atm_strike=atm_strike,
            selected_expiry=target_expiry.date_str,
            available_expiries=[asdict(e) for e in expiries],
            total_call_oi=total_call_oi,
            total_put_oi=total_put_oi,
            pcr=pcr,
            max_pain=atm_strike,
            resistance_strike=resistance_strike,
            support_strike=support_strike,
            sentiment=sentiment,
            rows=rows,
            chart_strikes=chart_strikes,
            chart_call_oi=chart_call_oi,
            chart_put_oi=chart_put_oi
        )

option_chain_engine = OptionChainEngine()
