from dhan_client import get_dhan_client
import datetime

client = get_dhan_client()
today_str = datetime.datetime.now().strftime("%Y-%m-%d")

res_m = client.dhan.intraday_minute_data(
    "577812", "MCX_COMM", "OPTFUT", today_str, today_str, interval=1
)
print("Raw res_m:", res_m)
