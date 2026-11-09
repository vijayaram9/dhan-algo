import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from dhan_client import DhanTraderClient

client = DhanTraderClient()
dhan = client.dhan

print("Dhan client initialized.")

# Let's check option chain for Crude Oil
# Crude Oil under_security_id: 573423 or 573424?
# Let's search scrip master or test option chain
try:
    chain = dhan.option_chain(under_security_id=573423, under_exchange_segment="MCX_COMM", expiry="2026-11-17")
    data = chain.get("data", {})
    oc = data.get("oc", {})
    print(f"Option chain fetched for 573423, total strikes: {len(oc)}")
    # Look for 8550 strike or nearby strikes
    for strike, sdata in list(oc.items())[:10]:
        ce = sdata.get("ce", {})
        pe = sdata.get("pe", {})
        if "8550" in str(strike) or "8500" in str(strike) or "8600" in str(strike):
            print(f"Strike {strike}:")
            print(f"  CE: ltp={ce.get('last_price')}, top_bid={ce.get('top_bid_price')}, top_ask={ce.get('top_ask_price')}, sec_id={ce.get('security_id')}")
            print(f"  PE: ltp={pe.get('last_price')}, top_bid={pe.get('top_bid_price')}, top_ask={pe.get('top_ask_price')}, sec_id={pe.get('security_id')}")
except Exception as e:
    print(f"Error fetching option chain: {e}")

# Also test quote_data for the sec_id
try:
    q = dhan.quote_data({'MCX_COMM': [582921, 582922]})
    print(f"quote_data result: {q}")
except Exception as e:
    print(f"quote_data error: {e}")

# Also test intraday_minute_data for 582921
try:
    import datetime
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    m = dhan.intraday_minute_data(security_id="582921", exchange_segment="MCX_COMM", instrument_type="OPTFUT", from_date=today_str, to_date=today_str, interval=1)
    print(f"intraday_minute_data for 582921: status={m.get('status')}")
    m_data = m.get('data', {})
    if 'close' in m_data and len(m_data['close']) > 0:
        print(f"Latest 1m close: {m_data['close'][-1]} at {m_data['start_time'][-1] if 'start_time' in m_data else 'now'}")
except Exception as e:
    print(f"intraday error: {e}")
