import re
import json
import sqlite3

# 1. Purge synthetic pricing from dhan_client.py
with open("dhan_client.py", "r", encoding="utf-8") as f:
    dc_text = f.read()

# Replace the synthetic fallback at end of get_option_ltp
old_dc_fallback = '''        # 5. Paper Mode Fair Value Protection:
        # If in PAPER trading mode, synthesize realistic market pricing so trader or algo
        # is never completely halted by broker API rate limits or missing exchange ticks.
        active_mode = mode or getattr(self, "mode", "PAPER")
        if active_mode == "PAPER" and not require_live:
            intr = max(0.0, real_spot - strike) if option_type.upper() == "CE" else max(0.0, strike - real_spot)
            time_val = max(15.0, real_spot * 0.012)
            synth_val = round(intr + time_val, 2)
            self._quote_cache[sec_key] = (synth_val, time.time())
            self.record_last_flashed_price(str(security_id), synth_val, [sec_key])
            return synth_val

        return 0.0'''

new_dc_fallback = '''        # 5. Zero-Tolerance Ground Truth Policy:
        # Live and Paper trading MUST be 100% authentic exchange quotes directly from DhanHQ API.
        # Theoretical approximations, fabricated prices, and synthetic formulas are strictly forbidden.
        return 0.0'''

if old_dc_fallback in dc_text:
    dc_text = dc_text.replace(old_dc_fallback, new_dc_fallback)
    print("[SUCCESS] Eradicated synthetic fallback from dhan_client.py")
else:
    print("[WARN] old_dc_fallback not matched in dhan_client.py")

# Add class-level request throttling lock to DhanTraderClient
if "_req_lock = threading.Lock()" not in dc_text:
    dc_text = dc_text.replace(
        "    _last_request_time = 0.0",
        "    _last_request_time = 0.0\n    _req_lock = threading.Lock()"
    )
    # Add throttle method
    throttle_code = '''    @classmethod
    def throttle_request(cls, min_interval: float = 0.20):
        """Enforces thread-safe rate-limiting across all concurrent Dhan API queries."""
        with cls._req_lock:
            now = time.time()
            elapsed = now - cls._last_request_time
            if elapsed < min_interval:
                time.sleep(min_interval - elapsed)
            cls._last_request_time = time.time()
'''
    dc_text = dc_text.replace("    def __init__(self, mode: str = None):", throttle_code + "\n    def __init__(self, mode: str = None):")
    print("[SUCCESS] Added thread-safe throttle_request to dhan_client.py")

# In get_option_ltp, throttle before calling intraday_minute_data
old_res_m = '''                    res_m = self.dhan.intraday_minute_data(
                        str(security_id), "MCX_COMM", "OPTFUT", today_str, today_str, interval=1
                    )'''

new_res_m = '''                    DhanTraderClient.throttle_request(0.20)
                    res_m = self.dhan.intraday_minute_data(
                        str(security_id), "MCX_COMM", "OPTFUT", today_str, today_str, interval=1
                    )
                    if isinstance(res_m, dict) and "Rate_Limit" in str(res_m):
                        time.sleep(0.6)
                        res_m = self.dhan.intraday_minute_data(
                            str(security_id), "MCX_COMM", "OPTFUT", today_str, today_str, interval=1
                        )'''

if old_res_m in dc_text:
    dc_text = dc_text.replace(old_res_m, new_res_m)
    print("[SUCCESS] Added rate-limiting & backoff to MCX intraday_minute_data in dhan_client.py")

with open("dhan_client.py", "w", encoding="utf-8") as f:
    f.write(dc_text)

# 2. Purge synthetic pricing from contract_selector.py
with open("contract_selector.py", "r", encoding="utf-8") as f:
    cs_text = f.read()

old_cs_synth = '''        if estimated_premium <= 0.10:
            # If in PAPER trading mode, synthesize realistic ATM premium so paper trades are never choked
            if getattr(config, "trading_mode", "PAPER") == "PAPER":
                intr = max(0.0, spot_price - contract_strike) if option_type == "CE" else max(0.0, contract_strike - spot_price)
                estimated_premium = round(intr + max(15.0, spot_price * 0.012), 2)
            else:
                rejection = f"🚨 LIVE DHAN QUOTE REQUIRED: Real-time option premium for {contract_meta.get('custom_symbol')} is ₹{estimated_premium:.2f} (No live market quote from Dhan API). Per strict compliance rules, trade cannot be placed without authentic live marketfeed quotes."
                return SelectedContract('''

new_cs_synth = '''        if estimated_premium <= 0.10:
            # Strictly NO synthetic or mathematical estimates. Genuine Dhan quotes only.
            rejection = f"🚨 AUTHENTIC DHAN QUOTE REQUIRED: Live Dhan tick unavailable for {contract_meta.get('custom_symbol')}. Refusing entry to prevent phantom execution or distorted pricing."
            return SelectedContract('''

if old_cs_synth in cs_text:
    cs_text = cs_text.replace(old_cs_synth, new_cs_synth)
    print("[SUCCESS] Eradicated synthetic fallback from contract_selector.py")
else:
    print("[WARN] old_cs_synth not matched in contract_selector.py")

with open("contract_selector.py", "w", encoding="utf-8") as f:
    f.write(cs_text)

# 3. Clean fake trade from journal (journal.json / database)
try:
    with open("data/trade_journal.json", "r", encoding="utf-8") as f:
        journal_data = json.load(f)
    orig_len = len(journal_data)
    # Filter out trades where entry was ~2273.94 on SILVERM
    journal_data = [t for t in journal_data if not (t.get("symbol", "").startswith("SILVERM") and 2200 < t.get("entry_price", 0) < 2300)]
    with open("data/trade_journal.json", "w", encoding="utf-8") as f:
        json.dump(journal_data, f, indent=2)
    print(f"[SUCCESS] Cleaned trade_journal.json (Removed {orig_len - len(journal_data)} corrupted synthetic trades)")
except Exception as e:
    print(f"[JOURNAL JSON CLEANUP] {e}")

# Also clean sqlite journal if exists
try:
    for db_file in ["data/journal.db", "data/trades.db"]:
        conn = sqlite3.connect(db_file)
        c = conn.cursor()
        c.execute("DELETE FROM trades WHERE symbol LIKE 'SILVERM%' AND entry_price < 3000;")
        conn.commit()
        conn.close()
        print(f"[SUCCESS] Cleaned {db_file}")
except Exception as e:
    print(f"[DB CLEANUP] {e}")

print("\nSynthetic purge and integrity enforcement complete!")
