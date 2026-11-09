import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from dhan_client import DhanTraderClient
import datetime

client = DhanTraderClient()
dhan = client.dhan
today_str = datetime.date.today().strftime("%Y-%m-%d")

m = dhan.intraday_minute_data(security_id="582921", exchange_segment="MCX_COMM", instrument_type="OPTFUT", from_date=today_str, to_date=today_str, interval=1)
if m.get("status") == "success" and "data" in m:
    data = m["data"]
    times = data.get("timestamp", []) or data.get("start_time", [])
    opens = data.get("open", [])
    highs = data.get("high", [])
    lows = data.get("low", [])
    closes = data.get("close", [])
    vols = data.get("volume", [])
    
    print(f"Total candles: {len(closes)}")
    for t, o, h, l, c, v in zip(times, opens, highs, lows, closes, vols):
        dt = datetime.datetime.fromtimestamp(t)
        dt_str = dt.strftime("%H:%M:%S")
        if "19:3" in dt_str or "19:4" in dt_str or "19:5" in dt_str or "20:0" in dt_str:
            print(f"Time: {dt_str} | Open: {o} | High: {h} | Low: {l} | Close: {c} | Vol: {v}")
