import threading
import json
import random
import time
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
from config import config

try:
    from dhanhq import dhanhq, DhanContext
except ImportError:
    dhanhq = None
    DhanContext = None

class DhanTraderClient:
    """
    Unified client supporting both simulated Paper Trading and Live Dhan Execution.
    Always maintains live Dhan connection to fetch exact account cash balance & limits.
    """
    _shared_quote_cache = {}
    _shared_last_flashed_prices = {}
    _rate_limit_until = 0.0
    _last_request_time = 0.0
    _req_lock = threading.Lock()

    @classmethod
    def throttle_request(cls, min_interval: float = 0.20):
        """Enforces thread-safe rate-limiting across all concurrent Dhan API queries."""
        with cls._req_lock:
            now = time.time()
            elapsed = now - cls._last_request_time
            if elapsed < min_interval:
                time.sleep(min_interval - elapsed)
            cls._last_request_time = time.time()

    def __init__(self, mode: str = None):
        self.mode = mode.upper() if mode else config.trading_mode
        self.client_id = config.client_id
        self.access_token = config.access_token
        self.dhan = None
        self.paper_positions = {}
        self.order_history = []
        self.paper_capital: float = self._load_paper_capital()
        self._quote_cache = DhanTraderClient._shared_quote_cache
        # Authentic Dhan Exchange Memory: Maps security_id / contract_key -> {"price": float, "timestamp": float}
        self._last_flashed_prices = DhanTraderClient._shared_last_flashed_prices

        # Connect to Dhan API if credentials are provided so live funds & limits always reflect real wallet balance
        self.init_dhan()

    def record_last_flashed_price(self, security_id: str, price: float, extra_keys: list = None):
        """Records an authentic Dhan exchange tick to guarantee continuous non-zero execution pricing."""
        if price <= 0.05:
            return
        now_ts = time.time()
        entry = {"price": round(price, 2), "timestamp": now_ts}
        if security_id:
            self._last_flashed_prices[str(security_id)] = entry
        if extra_keys:
            for k in extra_keys:
                if k:
                    self._last_flashed_prices[str(k)] = entry

    def get_last_flashed_price(self, security_id: str, fallback_keys: list = None) -> float:
        """
        Retrieves the most recent authentic exchange price flashed by Dhan for this security.
        Guarantees that momentary network drops or rate-limits never result in zero valuation or zero execution.
        """
        s_id = str(security_id)
        if s_id in self._last_flashed_prices:
            p = self._last_flashed_prices[s_id].get("price", 0.0)
            if p > 0.05:
                return p
        if fallback_keys:
            for k in fallback_keys:
                k_str = str(k)
                if k_str in self._last_flashed_prices:
                    p = self._last_flashed_prices[k_str].get("price", 0.0)
                    if p > 0.05:
                        return p
        return 0.0

    def init_dhan(self) -> bool:
        """Initializes or reinitializes the DhanHQ client."""
        if self.client_id and self.access_token and self.client_id != "YOUR_CLIENT_ID" and dhanhq:
            try:
                if DhanContext:
                    ctx = DhanContext(self.client_id, self.access_token)
                    self.dhan = dhanhq(ctx)
                else:
                    self.dhan = dhanhq(self.client_id, self.access_token)
                
                # Fast timeout (8s instead of default 60s) for responsive verification
                if hasattr(self, 'dhan') and self.dhan and hasattr(self.dhan, 'dhan_http'):
                    self.dhan.dhan_http.timeout = 8

                print(f"[Dhan] Initialized Dhan API connection for Client ID: {self.client_id} (Trading Mode: {self.mode})")
                return True
            except Exception as e:
                print(f"[Dhan] Error initializing DhanHQ client: {e}")
                self.dhan = None
                return False
        else:
            print("[Dhan] Running without live DhanHQ credentials.")
            self.dhan = None
            return False

    def update_credentials(self, client_id: str, access_token: str) -> dict:
        """
        Updates Dhan credentials, tests the session immediately with DhanHQ servers,
        and persists them to .env upon successful validation.
        """
        client_id = (client_id or "").strip().strip('"').strip("'").replace("\r", "").replace("\n", "").replace(" ", "")
        access_token = (access_token or "").strip().strip('"').strip("'").replace("\r", "").replace("\n", "").replace(" ", "")
        if not client_id or not access_token:
            return {"success": False, "message": "Client ID and Access Token are required."}

        # Backup old credentials in case of failure
        old_id, old_token = self.client_id, self.access_token
        self.client_id = client_id
        self.access_token = access_token

        if not self.init_dhan():
            self.client_id, self.access_token = old_id, old_token
            return {"success": False, "message": "Failed to instantiate DhanHQ client with provided credentials."}

        # Test fund limits to ensure token is valid and active
        fund_res = self.get_fund_limits()
        if fund_res.get("status") != "success":
            err_msg = "Token validation failed with Dhan."
            if isinstance(fund_res.get("remarks"), dict):
                err_msg = fund_res["remarks"].get("error_message") or str(fund_res["remarks"])
            elif fund_res.get("remarks"):
                err_msg = str(fund_res.get("remarks"))
            
            # Revert
            self.client_id, self.access_token = old_id, old_token
            self.init_dhan()
            return {"success": False, "message": f"Dhan session error: {err_msg}"}

        # Extract cash balance directly from fund_res without a redundant second network call
        cash = 0.0
        data_block = fund_res.get("data", {})
        if isinstance(data_block, dict):
            cash = float(data_block.get("availabelBalance", data_block.get("availableBalance", 0.0)))

        # Persist new credentials to config and .env
        config.client_id = client_id
        config.access_token = access_token
        try:
            import os
            env_path = os.path.join(os.path.dirname(__file__), ".env")
            if os.path.exists(env_path):
                with open(env_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                new_lines = []
                has_id = False
                has_tok = False
                for line in lines:
                    if line.startswith("DHAN_CLIENT_ID="):
                        new_lines.append(f"DHAN_CLIENT_ID={client_id}\n")
                        has_id = True
                    elif line.startswith("DHAN_ACCESS_TOKEN="):
                        new_lines.append(f"DHAN_ACCESS_TOKEN={access_token}\n")
                        has_tok = True
                    else:
                        new_lines.append(line)
                if not has_id:
                    new_lines.append(f"DHAN_CLIENT_ID={client_id}\n")
                if not has_tok:
                    new_lines.append(f"DHAN_ACCESS_TOKEN={access_token}\n")
                with open(env_path, "w", encoding="utf-8") as f:
                    f.writelines(new_lines)
        except Exception as e:
            print(f"[Dhan] Error saving credentials to .env: {e}")

        return {
            "success": True,
            "message": f"Dhan Session Active! Client ID: {client_id} verified.",
            "cash_balance": cash
        }

    def _get_paper_state_file(self) -> str:
        import os
        return os.path.join(os.path.dirname(__file__), "paper_state.json")

    def _load_paper_capital(self) -> float:
        import os
        path = self._get_paper_state_file()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return float(data.get("paper_capital", 150000.0))
            except Exception:
                pass
        return 25000.0

    def set_paper_capital(self, amount: float):
        import os
        self.paper_capital = float(amount)
        path = self._get_paper_state_file()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"paper_capital": self.paper_capital, "updated_at": datetime.now().isoformat()}, f, indent=2)
            print(f"[DhanClient] Paper trading capital set to: ₹{self.paper_capital:,.2f}")
        except Exception as e:
            print(f"[DhanClient] Error saving paper capital: {e}")

    def get_fund_limits(self) -> dict:
        """
        Fetches live real account fund limits directly from DhanHQ API.
        Does NOT return hardcoded values. Reflects real-time wallet top-ups.
        """
        if self.dhan:
            try:
                res = self.dhan.get_fund_limits()
                if isinstance(res, dict) and res.get("status") == "success":
                    return res
                return res if isinstance(res, dict) else {"status": "failure", "remarks": str(res)}
            except Exception as e:
                return {"status": "failure", "remarks": str(e), "data": {"availabelBalance": 0.0}}
        return {
            "status": "failure",
            "remarks": "Dhan credentials not configured or dhanhq library unavailable.",
            "data": {"availabelBalance": 0.0}
        }

    def get_real_cash_balance(self) -> float:
        """
        Extracts exact available cash balance from live DhanHQ funds API.
        """
        res = self.get_fund_limits()
        if res.get("status") == "success":
            data = res.get("data", {})
            # Dhan API fields: 'availabelBalance' or 'sodLimit'
            val = data.get("availabelBalance", data.get("sodLimit", 0.0))
            try:
                return float(val)
            except (ValueError, TypeError):
                return 0.0
        return 0.0

    def get_order_list(self) -> list:
        """
        Fetches live orders directly from DhanHQ API if connected in LIVE mode.
        Falls back to in-memory order history.
        """
        if self.dhan:
            try:
                res = self.dhan.get_order_list()
                if isinstance(res, dict) and res.get("status") == "success":
                    return res.get("data", [])
                elif isinstance(res, list):
                    return res
            except Exception as e:
                print(f"[!] Error fetching DhanHQ order list: {e}")
        return self.order_history

    def get_trade_book(self) -> list:
        """
        Fetches live executed trade book directly from DhanHQ API if connected.
        """
        if self.dhan:
            try:
                res = self.dhan.get_trade_book()
                if isinstance(res, dict) and res.get("status") == "success":
                    return res.get("data", [])
                elif isinstance(res, list):
                    return res
            except Exception as e:
                print(f"[!] Error fetching DhanHQ trade book: {e}")
        return []

    def get_positions(self) -> list:
        """
        Fetches live open positions directly from DhanHQ API.
        """
        if self.dhan:
            try:
                res = self.dhan.get_positions()
                if isinstance(res, dict) and res.get("status") == "success":
                    return res.get("data", [])
                elif isinstance(res, list):
                    return res
            except Exception as e:
                print(f"[!] Error fetching DhanHQ positions: {e}")
        return []

    def get_open_positions(self) -> list:
        """Alias for get_positions() to satisfy broker audit checks."""
        return self.get_positions()

    def get_spot_ltp(self) -> float:
        """
        Retrieves the exact real-time live Nifty 50 spot price directly from DhanHQ API.
        Pulls directly from live NSE market feeds to match Dhan screens.
        """
        # 1. Primary: Direct DhanHQ MarketFeed Quote
        if self.dhan:
            try:
                res = self.dhan.quote_data({"IDX_I": [13]})
                if isinstance(res, dict) and res.get("status") == "success":
                    data = res.get("data", {})
                    if "data" in data and isinstance(data["data"], dict):
                        data = data["data"]
                    d = data.get("IDX_I", {}).get("13", {})
                    lp = float(d.get("last_price", 0.0))
                    if lp > 0:
                        val = round(lp, 2)
                        self.record_last_flashed_price("13", val)
                        return val
            except Exception:
                pass

        # 2. Secondary: If market closed or quote silent, pull authentic close from Dhan intraday candles
        if self.dhan:
            try:
                today_str = datetime.now().strftime("%Y-%m-%d")
                res = self.dhan.intraday_minute_data(
                    security_id="13", exchange_segment="IDX_I", instrument_type="INDEX",
                    from_date=(datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"), to_date=today_str, interval=5
                )
                if isinstance(res, dict) and res.get("status") == "success":
                    closes = res.get("data", {}).get("close", [])
                    if closes:
                        last_c = round(float(closes[-1]), 2)
                        self.record_last_flashed_price("13", last_c)
                        return last_c
            except Exception:
                pass

        # 3. Authentic exchange memory from last flashed Dhan quote
        last_flashed = self.get_last_flashed_price("13")
        if last_flashed > 0:
            return last_flashed

        try:
            from market_intelligence import market_intelligence
            live_spot = market_intelligence.get_asset_spot("NIFTY")
            if live_spot > 0:
                return live_spot
        except Exception:
            pass

        return 0.0

    def get_dhan_intraday_candles(self, security_id: str = "13", exchange_segment: str = "IDX_I", instrument_type: str = "INDEX", interval: int = 5) -> list:
        """
        Retrieves authentic intraday OHLCV candles directly from DhanHQ API.
        """
        if not self.dhan:
            return []
        try:
            today_str = datetime.now().strftime("%Y-%m-%d")
            res = self.dhan.intraday_minute_data(
                security_id=str(security_id),
                exchange_segment=exchange_segment,
                instrument_type=instrument_type,
                from_date=today_str,
                to_date=today_str,
                interval=interval
            )
            if isinstance(res, dict) and res.get("status") == "success" and "data" in res:
                data = res["data"]
                opens = data.get("open", [])
                highs = data.get("high", [])
                lows = data.get("low", [])
                closes = data.get("close", [])
                vols = data.get("volume", [0] * len(opens))
                times = data.get("timestamp", [0] * len(opens))
                candles = []
                for o, h, l, c, v, t in zip(opens, highs, lows, closes, vols, times):
                    candles.append({
                        "open": float(o),
                        "high": float(h),
                        "low": float(l),
                        "close": float(c),
                        "volume": int(v) if v else 0,
                        "timestamp": t
                    })
                return candles
        except Exception as e:
            print(f"[Dhan] Error fetching intraday candles: {e}")
        return []

    def get_option_ltp(self, security_id: str, spot_price: float, strike: float, option_type: str, expiry_date: str = "", mode: str = None, require_live: bool = False, segment: Optional[str] = None) -> float:
        """
        Dynamically retrieves the authentic live market option premium directly from DhanHQ API.
        If require_live is True, strictly returns ONLY verified Dhan live quotes; never falls back to mathematical estimation.
        """
        now_ts = time.time()
        sec_key = f"{security_id}_{strike}_{option_type}"

        # 1. High-speed quote cache (2.5s TTL to prevent broker rate-limiting)
        if sec_key in self._quote_cache:
            cached_val, cached_time = self._quote_cache[sec_key]
            if now_ts - cached_time < 2.5 and cached_val > 0:
                return cached_val

        # 2. Harmonize underlying spot with ground-truth exchange price
        real_spot = spot_price
        if real_spot < 24000.0 and strike >= 23000.0:
            try:
                from market_intelligence import market_intelligence
                cur_live = market_intelligence.get_asset_spot("NIFTY")
                if cur_live >= 24000.0:
                    real_spot = cur_live
            except Exception:
                pass

        # 3. Fetch Real Market Quote directly from DhanHQ API (for both PAPER simulation and LIVE trading)
        if self.dhan and security_id and str(security_id).isdigit() and int(security_id) > 0:
            sec_int = int(security_id)
            if segment:
                target_seg = segment
            elif 400000 <= sec_int <= 700000:
                target_seg = "MCX_COMM"
            elif strike > 70000 or real_spot > 70000 or sec_int > 1000000:
                target_seg = "BSE_FNO"
            else:
                target_seg = "NSE_FNO"

            today_str = datetime.now().strftime("%Y-%m-%d")

            # Route MCX Commodity Options directly to intraday_minute_data
            # (Dhan does not service ticker_data packets for MCX commodities)
            if target_seg == "MCX_COMM":
                try:
                    DhanTraderClient.throttle_request(0.20)
                    itype = "OPTFUT"
                    res_m = self.dhan.intraday_minute_data(
                        str(security_id), target_seg, itype, today_str, today_str, interval=1
                    )
                    if isinstance(res_m, dict) and "Rate_Limit" in str(res_m):
                        time.sleep(0.5)
                        res_m = self.dhan.intraday_minute_data(
                            str(security_id), target_seg, itype, today_str, today_str, interval=1
                        )
                    if isinstance(res_m, dict) and res_m.get("status") == "success":
                        d_m = res_m.get("data", {})
                        if isinstance(d_m, dict):
                            closes = d_m.get("close", [])
                            if closes and float(closes[-1]) > 0:
                                val = round(float(closes[-1]), 2)
                                self._quote_cache[sec_key] = (val, time.time())
                                self.record_last_flashed_price(str(security_id), val, [sec_key])
                                return val
                except Exception:
                    pass
            else:
                # NSE and BSE Options (NIFTY, BANKNIFTY, FINNIFTY, SENSEX, TCS, RELIANCE, etc.):
                # Use lightweight, real-time sub-second ticker_data feed directly from Dhan with fast retry
                try:
                    DhanTraderClient.throttle_request(0.05)
                    for attempt in range(2):
                        res = self.dhan.ticker_data({target_seg: [sec_int]})
                        if not (isinstance(res, dict) and res.get("status") == "success"):
                            res = self.dhan.quote_data({target_seg: [sec_int]})

                        if isinstance(res, dict) and res.get("status") == "success":
                            data = res.get("data", {})
                            if isinstance(data, dict):
                                if "data" in data and isinstance(data["data"], dict):
                                    data = data["data"]
                                seg_data = data.get(target_seg, {})
                                sec_dict = seg_data.get(str(security_id), {}) or seg_data.get(int(security_id), {}) or data.get(str(security_id), {})
                                ltp = float(sec_dict.get("last_price", 0.0))
                                if ltp > 0:
                                    val = round(ltp, 2)
                                    self._quote_cache[sec_key] = (val, time.time())
                                    self.record_last_flashed_price(str(security_id), val, [sec_key])
                                    return val
                        time.sleep(0.25)
                except Exception:
                    pass

                # Fallback to intraday minute data for NSE if quote packet had a momentary hitch
                try:
                    itype = "OPTIDX" if target_seg == "NSE_FNO" and (strike > 10000 or real_spot > 10000) else "OPTSTK"
                    res_c = self.dhan.intraday_minute_data(
                        str(security_id), target_seg, itype, today_str, today_str, interval=1
                    )
                    if isinstance(res_c, dict) and res_c.get("status") == "success":
                        d_c = res_c.get("data", {})
                        if isinstance(d_c, dict):
                            closes = d_c.get("close", [])
                            if closes and float(closes[-1]) > 0:
                                val = round(float(closes[-1]), 2)
                                self._quote_cache[sec_key] = (val, time.time())
                                self.record_last_flashed_price(str(security_id), val, [sec_key])
                                return val
                except Exception:
                    pass

        # 4. Authentic Exchange Memory Policy:
        # If Dhan API has a momentary network delay, rate-limit, or off-quote tick,
        # return the last authentic price flashed from Dhan for this contract.
        last_flashed = self.get_last_flashed_price(security_id, [sec_key])
        if last_flashed > 0.05:
            return last_flashed

        # 5. Zero-Tolerance Ground Truth Policy:
        # Live and Paper trading MUST be 100% authentic exchange quotes directly from DhanHQ API.
        # Theoretical approximations, fabricated prices, and synthetic formulas are strictly forbidden.
        return 0.0

    def get_options_batch_ltp(self, query_list: list) -> dict:
        """
        Fetches live option premiums for multiple contracts in a SINGLE batch API call to DhanHQ.
        query_list: list of tuples (security_id, strike, option_type, segment)
        Returns: dict mapping security_id (str) -> live_ltp (float)
        """
        now_ts = time.time()
        results = {}
        missing_by_seg = {}

        # 1. Check quote cache first
        for item in query_list:
            sec_id = str(item[0])
            strike = float(item[1])
            opt_type = str(item[2])
            sec_int = int(sec_id) if sec_id.isdigit() else 0
            if len(item) > 3 and item[3]:
                seg = item[3]
            elif 400000 <= sec_int <= 700000:
                seg = "MCX_COMM"
            elif strike > 70000 or sec_int > 1000000:
                seg = "BSE_FNO"
            else:
                seg = "NSE_FNO"
            sec_key = f"{sec_id}_{strike}_{opt_type}"

            if sec_key in self._quote_cache:
                cached_val, cached_time = self._quote_cache[sec_key]
                if now_ts - cached_time < 2.0 and cached_val > 0:
                    results[sec_id] = cached_val
                    continue

            if sec_id.isdigit() and int(sec_id) > 0:
                missing_by_seg.setdefault(seg, []).append(int(sec_id))

        if not missing_by_seg or not self.dhan:
            return results

        # 2. Query all missing quotes in single batch call per segment
        try:
            now_cur = time.time()
            elapsed = now_cur - getattr(DhanTraderClient, "_last_request_time", 0.0)
            if elapsed < 1.05:
                time.sleep(1.05 - elapsed)
            DhanTraderClient._last_request_time = time.time()

            res = self.dhan.ticker_data(missing_by_seg)
            if not (isinstance(res, dict) and res.get("status") == "success"):
                res = self.dhan.quote_data(missing_by_seg)

            if isinstance(res, dict) and res.get("status") == "success":
                data = res.get("data", {})
                if isinstance(data, dict):
                    if "data" in data and isinstance(data["data"], dict):
                        data = data["data"]
                    for seg, sec_map in data.items():
                        if isinstance(sec_map, dict):
                            for s_id, q_info in sec_map.items():
                                lp = float(q_info.get("last_price", 0.0))
                                if lp > 0:
                                    val = round(lp, 2)
                                    results[str(s_id)] = val
                                    self.record_last_flashed_price(str(s_id), val)
                                    # Also populate self._quote_cache for single lookups
                                    for item in query_list:
                                        if str(item[0]) == str(s_id):
                                            k = f"{s_id}_{float(item[1])}_{str(item[2])}"
                                            self._quote_cache[k] = (val, now_ts)
                                            self.record_last_flashed_price(str(s_id), val, [k])
            elif isinstance(res, dict) and ("805" in str(res) or "Too many requests" in str(res)):
                DhanTraderClient._rate_limit_until = now_ts + 2.0
        except Exception:
            pass

        # Fallback to last authentic flashed Dhan price for any missing items
        for item in query_list:
            s_id_str = str(item[0])
            if s_id_str not in results or results[s_id_str] <= 0.05:
                k = f"{s_id_str}_{float(item[1])}_{str(item[2])}"
                last_flashed = self.get_last_flashed_price(s_id_str, [k])
                if last_flashed > 0.05:
                    results[s_id_str] = last_flashed

        return results

    def place_order(
        self,
        security_id: str,
        trading_symbol: str,
        transaction_type: str,  # 'BUY' or 'SELL'
        quantity: int,
        price: float = 0.0,
        order_type: str = "MARKET",
        product_type: str = "INTRA",
        mode: str = None
    ) -> dict:
        target_mode = (mode or self.mode).upper()
        order_id = f"ORD_{int(time.time() * 1000)}"

        # ZERO-PRICE & PHANTOM TRADE PREVENTION:
        # Never allow an order to be executed at ₹0.0. Resolve to Dhan's authentic last flashed price.
        if price <= 0.05:
            last_flashed = self.get_last_flashed_price(security_id, [trading_symbol])
            if last_flashed > 0.05:
                price = last_flashed
            else:
                print(f"[ORDER BLOCKED] Cannot execute {transaction_type} {trading_symbol} at ₹{price:.2f} (no authentic Dhan price has flashed).")
                return {
                    "status": "failure",
                    "remarks": f"Zero Price Guardrail: Blocked execution for {trading_symbol} at ₹0.0 (no authentic Dhan exchange quote available)."
                }

        if target_mode == "LIVE" and self.dhan:
            try:
                dhan_tx = self.dhan.BUY if transaction_type == "BUY" else self.dhan.SELL
                dhan_ot = self.dhan.MARKET if order_type == "MARKET" else self.dhan.LIMIT
                dhan_pt = self.dhan.INTRA if product_type == "INTRA" else self.dhan.MARGIN

                # Dynamic segment routing for MCX Commodities, BSE, and NSE
                sym_upper = str(trading_symbol).upper()
                if sym_upper.startswith(("CRUDEOIL", "NATGAS", "GOLD", "SILVER")):
                    exch_seg = getattr(self.dhan, "MCX", "MCX_COMM")
                elif sym_upper.startswith("SENSEX"):
                    exch_seg = getattr(self.dhan, "BSE_FNO", "BSE_FNO")
                else:
                    exch_seg = getattr(self.dhan, "NSE_FNO", "NSE_FNO")

                res = self.dhan.place_order(
                    security_id=security_id,
                    exchange_segment=exch_seg,
                    transaction_type=dhan_tx,
                    quantity=quantity,
                    order_type=dhan_ot,
                    product_type=dhan_pt,
                    price=price
                )
                if isinstance(res, dict) and res.get("status") == "success":
                    data_obj = res.get("data", {})
                    oid = data_obj.get("orderId", order_id) if isinstance(data_obj, dict) else order_id
                    print(f"[LIVE ORDER PLACED SUCCESS] Order ID: {oid}")
                    return {"status": "success", "order_id": oid, "raw": res}
                else:
                    err_msg = ""
                    if isinstance(res, dict):
                        rem = res.get("remarks")
                        err_msg = rem.get("error_message") if isinstance(rem, dict) else str(rem)
                    print(f"[LIVE ORDER REJECTED BY DHAN] {err_msg or res}")
                    return {"status": "failure", "remarks": err_msg or str(res)}
            except Exception as e:
                print(f"[!] Live order execution exception: {e}")
                # IDEMPOTENCY RECONCILIATION: Check broker order book before assuming failure
                try:
                    orders_res = self.dhan.get_order_list()
                    if isinstance(orders_res, dict) and orders_res.get("status") == "success":
                        order_items = orders_res.get("data", [])
                        for item in order_items:
                            # If matching recent order for this security within pending/traded status
                            if str(item.get("securityId")) == str(security_id) and item.get("orderStatus") in ["CONFIRMED", "PENDING", "TRADED"]:
                                reconciled_id = item.get("orderId", order_id)
                                print(f"[RECONCILIATION SUCCESS] Order found on broker after timeout: {reconciled_id}")
                                return {"status": "success", "order_id": reconciled_id, "raw": item}
                except Exception as rec_err:
                    print(f"[!] Order reconciliation lookup error: {rec_err}")
                return {"status": "failure", "remarks": f"Network exception: {e}. Reconciled with broker."}

        # Paper Execution - Strictly executes at authentic exchange quote tick
        exec_price = max(0.05, round(price, 2))
        self.record_last_flashed_price(str(security_id), exec_price, [trading_symbol])

        order_record = {
            "order_id": order_id,
            "security_id": security_id,
            "trading_symbol": trading_symbol,
            "transaction_type": transaction_type,
            "quantity": quantity,
            "price": exec_price,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "TRADED",
            "mode": "PAPER"
        }
        self.order_history.append(order_record)
        print(f"[PAPER ORDER EXECUTED] {transaction_type} {quantity}x {trading_symbol} @ ₹{exec_price:.2f}")
        return {"status": "success", "order_id": order_id, "data": order_record}

# Shared global DhanTraderClient instance to prevent multiple client logins and unify cache/rate-limiting
_shared_client_instance: Optional[DhanTraderClient] = None

def get_dhan_client(mode: str = None) -> DhanTraderClient:
    global _shared_client_instance
    if _shared_client_instance is None:
        _shared_client_instance = DhanTraderClient(mode=mode)
    elif mode and _shared_client_instance.mode != mode.upper():
        _shared_client_instance.mode = mode.upper()
    return _shared_client_instance
