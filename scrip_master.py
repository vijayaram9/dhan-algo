import os
import datetime
from typing import List, Optional
import pandas as pd
import requests

from config import config

SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
CACHE_DIR = os.path.join(os.path.dirname(__file__), "data")

class ScripMaster:
    """
    Downloads, caches, and indexes Dhan's daily scrip master.
    Provides precise contract and expiry date matching.
    """
    _cached_df = None
    _cached_date = None
    _cached_expiries = {}
    _cached_options = {}

    def __init__(self):
        os.makedirs(CACHE_DIR, exist_ok=True)
        self.today_str = datetime.date.today().strftime("%Y-%m-%d")
        self.cache_file = os.path.join(CACHE_DIR, f"scrip_master_{self.today_str}.csv")
        if ScripMaster._cached_df is not None and ScripMaster._cached_date == self.today_str:
            self.df = ScripMaster._cached_df
        else:
            self.df = None

    def load_master(self, force_refresh: bool = False) -> pd.DataFrame:
        if not force_refresh and ScripMaster._cached_df is not None and ScripMaster._cached_date == self.today_str:
            self.df = ScripMaster._cached_df
            return self.df

        if not force_refresh and os.path.exists(self.cache_file):
            print(f"[*] Loading cached scrip master from {self.cache_file}...")
            self.df = pd.read_csv(self.cache_file, low_memory=False)
            ScripMaster._cached_df = self.df
            ScripMaster._cached_date = self.today_str
            return self.df

        print(f"[*] Downloading fresh scrip master from Dhan API...")
        try:
            resp = requests.get(SCRIP_MASTER_URL, timeout=30)
            resp.raise_for_status()
            with open(self.cache_file, "wb") as f:
                f.write(resp.content)
            self.df = pd.read_csv(self.cache_file, low_memory=False)
            ScripMaster._cached_df = self.df
            ScripMaster._cached_date = self.today_str
            print(f"[*] Scrip master cached successfully ({len(self.df)} instruments).")
            return self.df
        except Exception as e:
            print(f"[!] Error fetching scrip master: {e}")
            if os.path.exists(self.cache_file):
                self.df = pd.read_csv(self.cache_file, low_memory=False)
                ScripMaster._cached_df = self.df
                ScripMaster._cached_date = self.today_str
                return self.df
            raise

    def get_available_expiries(self, asset_key: str = "NIFTY") -> List[str]:
        """Returns all upcoming option expiry dates for the specific asset sorted chronologically."""
        if asset_key in ScripMaster._cached_expiries:
            return ScripMaster._cached_expiries[asset_key]
        if self.df is None:
            self.load_master()
        df = self.df
        col_symbol = next((c for c in df.columns if "TRADING_SYMBOL" in c.upper()), "SEM_TRADING_SYMBOL")
        col_instrument = next((c for c in df.columns if "INSTRUMENT" in c.upper()), "SEM_INSTRUMENT_NAME")
        col_expiry = next((c for c in df.columns if "EXPIRY_DATE" in c.upper()), "SEM_EXPIRY_DATE")
        col_exchange = next((c for c in df.columns if "EXCH_ID" in c.upper()), "SEM_EXM_EXCH_ID")

        is_commodity = asset_key in ["CRUDEOILM", "NATGASMINI", "GOLDM", "SILVERM"]
        is_stock = asset_key in ["RELIANCE", "HDFCBANK", "ICICIBANK", "TCS", "INFY", "SBIN"]

        if asset_key == "FINNIFTY":
            prefix_filter = df[col_symbol].astype(str).str.startswith("FINNIFTY")
            inst_type = "OPTIDX"
        elif asset_key == "BANKNIFTY":
            prefix_filter = df[col_symbol].astype(str).str.startswith("BANKNIFTY")
            inst_type = "OPTIDX"
        elif asset_key == "SENSEX":
            prefix_filter = df[col_symbol].astype(str).str.startswith("SENSEX")
            inst_type = "OPTIDX"
        elif asset_key == "NIFTYIT":
            prefix_filter = df[col_symbol].astype(str).str.startswith("NIFTYIT")
            inst_type = "OPTIDX"
        elif asset_key == "CRUDEOILM":
            prefix_filter = df[col_symbol].astype(str).str.startswith("CRUDEOIL") & (df[col_exchange].astype(str) == "MCX")
            inst_type = "OPTFUT"
        elif asset_key == "NATGASMINI":
            prefix_filter = df[col_symbol].astype(str).str.startswith("NATGAS") & (df[col_exchange].astype(str) == "MCX")
            inst_type = "OPTFUT"
        elif asset_key == "GOLDM":
            prefix_filter = df[col_symbol].astype(str).str.startswith("GOLD") & (df[col_exchange].astype(str) == "MCX")
            inst_type = "OPTFUT"
        elif asset_key == "SILVERM":
            prefix_filter = df[col_symbol].astype(str).str.startswith("SILVER") & (df[col_exchange].astype(str) == "MCX")
            inst_type = "OPTFUT"
        elif is_stock:
            prefix_filter = df[col_symbol].astype(str).str.startswith(asset_key)
            inst_type = "OPTSTK"
        else:
            prefix_filter = (df[col_symbol].astype(str).str.startswith("NIFTY-") | df[col_symbol].astype(str).str.startswith("NIFTY "))
            inst_type = "OPTIDX"

        c = df[prefix_filter & (df[col_instrument].astype(str) == inst_type)]
        if c.empty:
            c = df[prefix_filter]
        expiries = sorted(c[col_expiry].dropna().unique())
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        active = [exp for exp in expiries if exp[:10] >= today]
        res = active if active else sorted(c[col_expiry].dropna().unique())
        ScripMaster._cached_expiries[asset_key] = res
        return res

    def resolve_target_expiry(self, expiry_preference: Optional[str] = None, asset_key: str = "NIFTY") -> str:
        """
        Resolves the exact expiry timestamp string based on preference for the specific asset:
        - 'CURRENT_WEEK' (default): nearest active expiry date
        - 'NEXT_WEEK': second upcoming weekly expiry
        - 'MONTHLY': last expiry of the current calendar month
        - Exact date string: e.g. '2026-11-23'
        """
        pref = (expiry_preference or config.expiry_type).strip().upper()
        active_expiries = self.get_available_expiries(asset_key)
        if not active_expiries:
            # Fallback to general active expiries
            active_expiries = self.get_available_expiries("NIFTY")
        if not active_expiries:
            return datetime.datetime.now().strftime("%Y-%m-%d")

        if pref == "CURRENT_WEEK" or not pref:
            return active_expiries[0]

        if pref == "NEXT_WEEK":
            return active_expiries[1] if len(active_expiries) > 1 else active_expiries[0]

        if pref == "MONTHLY":
            current_month = datetime.date.today().strftime("%Y-%m")
            month_expiries = [exp for exp in active_expiries if exp.startswith(current_month)]
            if month_expiries:
                return month_expiries[-1]
            return active_expiries[-1]

        for exp in active_expiries:
            if pref in exp:
                return exp

        return active_expiries[0]

    def get_nifty_option(
        self,
        spot_price: float,
        option_type: str = "CE",
        strike_offset: int = 0,
        expiry_preference: Optional[str] = None,
        asset_key: str = "NIFTY"
    ) -> dict:
        """
        Finds the exact option contract matching the asset, strike, and expiry.
        """
        if self.df is None:
            self.load_master()

        # Step size based on asset
        step = 50.0
        if asset_key in ["BANKNIFTY", "SENSEX", "GOLDM", "NIFTYIT"]:
            step = 100.0
        elif asset_key in ["FINNIFTY", "CRUDEOILM"]:
            step = 50.0
        elif asset_key == "SILVERM":
            step = 500.0
        elif asset_key == "NATGASMINI":
            step = 5.0

        base_strike = round(spot_price / step) * step
        target_strike = base_strike + (strike_offset * step)

        # Resolve target expiry for specific asset
        target_expiry = self.resolve_target_expiry(expiry_preference, asset_key)

        cache_key = (asset_key, option_type.upper(), float(target_strike), str(target_expiry))
        if cache_key in ScripMaster._cached_options:
            return ScripMaster._cached_options[cache_key]

        df = self.df
        col_symbol = next((c for c in df.columns if "TRADING_SYMBOL" in c.upper()), "SEM_TRADING_SYMBOL")
        col_custom = next((c for c in df.columns if "CUSTOM_SYMBOL" in c.upper()), "SEM_CUSTOM_SYMBOL")
        col_sec_id = next((c for c in df.columns if "SECURITY_ID" in c.upper()), "SEM_SMST_SECURITY_ID")
        col_strike = next((c for c in df.columns if "STRIKE" in c.upper()), "SEM_STRIKE_PRICE")
        col_opttype = next((c for c in df.columns if "OPTION_TYPE" in c.upper()), "SEM_OPTION_TYPE")
        col_expiry = next((c for c in df.columns if "EXPIRY_DATE" in c.upper()), "SEM_EXPIRY_DATE")
        col_lot = next((c for c in df.columns if "LOT_UNITS" in c.upper()), "SEM_LOT_UNITS")
        col_exchange = next((c for c in df.columns if "EXCH_ID" in c.upper()), "SEM_EXM_EXCH_ID")

        # Distinguish prefix cleanly across indices, stocks, and MCX commodities
        clean_key = str(asset_key).upper()
        if clean_key == "FINNIFTY":
            prefix_filter = df[col_symbol].astype(str).str.startswith("FINNIFTY")
        elif clean_key == "BANKNIFTY":
            prefix_filter = df[col_symbol].astype(str).str.startswith("BANKNIFTY")
        elif clean_key == "SENSEX":
            prefix_filter = df[col_symbol].astype(str).str.startswith("SENSEX")
        elif clean_key in ("CRUDEOILM", "NATGASMINI", "GOLDM", "SILVERM"):
            # EXACT underlying match. A prefix match like startswith("CRUDEOIL") also hits the
            # full-size CRUDEOIL (100 bbl) contract, which was being traded instead of CRUDEOILM.
            if "SM_SYMBOL_NAME" in df.columns:
                prefix_filter = (df["SM_SYMBOL_NAME"].astype(str).str.upper() == clean_key) & \
                                (df[col_symbol].astype(str).str.upper().str.startswith(f"{clean_key}-"))
            else:
                prefix_filter = df[col_symbol].astype(str).str.upper().str.startswith(f"{clean_key}-")
        elif clean_key != "NIFTY":
            # Individual stock options (RELIANCE, TCS, HDFCBANK, ICICIBANK, INFY, SBIN, etc.)
            prefix_filter = df[col_symbol].astype(str).str.startswith(f"{clean_key}-") | df[col_symbol].astype(str).str.startswith(f"{clean_key} ")
        else:
            # Strictly NIFTY 50 (not FINNIFTY or BANKNIFTY)
            prefix_filter = df[col_symbol].astype(str).str.startswith("NIFTY-") | df[col_symbol].astype(str).str.startswith("NIFTY ")

        filtered = df[
            prefix_filter &
            (df[col_expiry].astype(str) == target_expiry) &
            (df[col_opttype].astype(str).str.upper() == option_type.upper())
        ].copy()

        if filtered.empty:
            # Fallback to the NEAREST non-expired expiry for the asset (never a random/expired one)
            filtered = df[
                prefix_filter &
                (df[col_opttype].astype(str).str.upper() == option_type.upper())
            ].copy()
            if not filtered.empty:
                exp_dates = pd.to_datetime(filtered[col_expiry], errors="coerce")
                today_ts = pd.Timestamp(datetime.date.today())
                live = filtered[exp_dates >= today_ts]
                if not live.empty:
                    nearest = pd.to_datetime(live[col_expiry], errors="coerce").min()
                    filtered = live[pd.to_datetime(live[col_expiry], errors="coerce") == nearest].copy()

        filtered[col_strike] = pd.to_numeric(filtered[col_strike], errors="coerce")
        strike_match = filtered[filtered[col_strike] == float(target_strike)]

        if strike_match.empty and not filtered.empty:
            strike_diffs = (filtered[col_strike] - target_strike).abs()
            nearest_idx = strike_diffs.idxmin()
            strike_match = filtered.loc[[nearest_idx]]

        if not strike_match.empty:
            selected = strike_match.iloc[0]
            custom_name = str(selected[col_custom]) if col_custom in selected and pd.notna(selected[col_custom]) else str(selected[col_symbol])
            comm_lots = {"CRUDEOILM": 10, "NATGASMINI": 250, "GOLDM": 10, "SILVERM": 5}
            if asset_key in comm_lots:
                lot_size = comm_lots[asset_key]
            else:
                lot_size = int(selected[col_lot]) if col_lot in selected and pd.notna(selected[col_lot]) and int(selected[col_lot]) > 1 else config.lot_size

            exch = str(selected[col_exchange]).upper() if col_exchange in selected and pd.notna(selected[col_exchange]) else ""
            seg_code = str(selected.get("SEM_SEGMENT", "")).upper()
            if exch == "MCX":
                dhan_seg = "MCX_COMM"
            elif exch == "BSE":
                dhan_seg = "BSE_FNO" if seg_code == "D" else "BSE_EQ"
            elif exch == "NSE":
                dhan_seg = "NSE_FNO" if seg_code == "D" else "NSE_EQ"
            else:
                dhan_seg = "MCX_COMM" if asset_key in comm_lots else ("BSE_FNO" if asset_key == "SENSEX" else "NSE_FNO")

            res_contract = {
                "security_id": str(selected[col_sec_id]),
                "trading_symbol": str(selected[col_symbol]),
                "custom_symbol": custom_name,
                "strike_price": float(selected[col_strike]),
                "option_type": option_type.upper(),
                "expiry": str(selected[col_expiry])[:10],
                "lot_size": lot_size,
                "segment": dhan_seg
            }
            ScripMaster._cached_options[cache_key] = res_contract
            return res_contract
        else:
            # Clean programmatic fallback
            exp_date_str = datetime.datetime.now().strftime("%d %b").upper()
            lot_map = {
                "NIFTY": 65, "BANKNIFTY": 30, "FINNIFTY": 40, "SENSEX": 20, "NIFTYIT": 25,
                "CRUDEOILM": 10, "NATGASMINI": 250, "GOLDM": 10, "SILVERM": 5,
                "RELIANCE": 250, "HDFCBANK": 550, "ICICIBANK": 700, "TCS": 175, "INFY": 400, "SBIN": 750
            }
            fb_seg = "MCX_COMM" if asset_key in ["CRUDEOILM", "NATGASMINI", "GOLDM", "SILVERM"] else ("BSE_FNO" if asset_key == "SENSEX" else "NSE_FNO")
            fallback_res = {
                "security_id": "0",
                "trading_symbol": f"{asset_key}-{int(target_strike)}-{option_type.upper()}",
                "custom_symbol": f"{asset_key} {exp_date_str} {int(target_strike)} {'CALL' if option_type.upper() == 'CE' else 'PUT'}",
                "strike_price": float(target_strike),
                "option_type": option_type.upper(),
                "expiry": datetime.datetime.now().strftime("%Y-%m-%d"),
                "lot_size": lot_map.get(asset_key, 65),
                "segment": fb_seg
            }
            ScripMaster._cached_options[cache_key] = fallback_res
            return fallback_res

if __name__ == "__main__":
    sm = ScripMaster()
    sm.load_master()
    print("Upcoming Active Expiries:", sm.get_available_expiries()[:5])
    
    # Test current weekly expiry
    call_this_week = sm.get_nifty_option(spot_price=23330, option_type="CE", expiry_preference="CURRENT_WEEK")
    print(f"\nCurrent Week ({call_this_week['expiry']}): {call_this_week['custom_symbol']} (ID: {call_this_week['security_id']})")
    
    # Test next week expiry
    call_next_week = sm.get_nifty_option(spot_price=23330, option_type="CE", expiry_preference="NEXT_WEEK")
    print(f"Next Week ({call_next_week['expiry']}): {call_next_week['custom_symbol']} (ID: {call_next_week['security_id']})")
