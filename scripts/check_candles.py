import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from dhan_client import DhanTraderClient
import datetime

client = DhanTraderClient()
dhan = client.dhan
today_str = datetime.date.today().strftime("%Y-%m-%d")

# Let's inspect raw return
m = dhan.intraday_minute_data(security_id="582921", exchange_segment="MCX_COMM", instrument_type="OPTFUT", from_date=today_str, to_date=today_str, interval=1)
print("OPTFUT result:", m)

# What about instrument_type="COMMODITY" or "FUTCOM" or "INDEX" or "EQUITY"?
for itype in ["COMMODITY", "FUTCOM", "OPTCOM", "INDEX"]:
    m2 = dhan.intraday_minute_data(security_id="582921", exchange_segment="MCX_COMM", instrument_type=itype, from_date=today_str, to_date=today_str, interval=1)
    if m2.get("status") == "success" and m2.get("data"):
        print(f"{itype} SUCCESS: keys={list(m2['data'].keys())}, count={len(m2['data'].get('close', []))}")
