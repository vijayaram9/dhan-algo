from dhan_client import get_dhan_client

client = get_dhan_client()
prem = client.get_option_ltp("577812", 189700.0, 190000.0, "CE", "2026-11-23", segment="MCX_COMM", require_live=True)
print("get_option_ltp with require_live=True:", prem)
prem_any = client.get_option_ltp("577812", 189700.0, 190000.0, "CE", "2026-11-23", segment="MCX_COMM")
print("get_option_ltp default:", prem_any)
