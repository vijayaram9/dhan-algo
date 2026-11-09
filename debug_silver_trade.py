from scrip_master import ScripMaster
from dhan_client import get_dhan_client
from datetime import date
import pandas as pd

sm = ScripMaster()
client = get_dhan_client()
today = date.today().strftime("%Y-%m-%d")

# Find the contract in ScripMaster
opt = sm.get_nifty_option(189700.0, "CE", 0, None, "SILVERM")
print("Selected contract in ScripMaster:")
for k, v in opt.items():
    print(f"  {k}: {v}")

sec_id = opt.get("security_id")
seg = opt.get("segment")

# Query Dhan for live intraday candles of this option contract
res = client.dhan.intraday_minute_data(str(sec_id), seg, "OPTFUT", today, today, interval=1)
if isinstance(res, dict) and res.get("status") == "success":
    d = res.get("data", {})
    closes = d.get("close", [])
    times = d.get("start_Time", [])
    print(f"\nDhan live candle close for {sec_id}:")
    print(f"  Total 1m bars: {len(closes)}")
    if closes:
        print(f"  Latest close: {closes[-1]}")
        # Print last 5 bars
        for t, c in list(zip(times, closes))[-5:]:
            print(f"    Time: {t} | Close: {c}")
else:
    print(f"\nDhan intraday_minute_data failed: {res}")
