from smart_edge_engine import smart_edge_engine

sec_id = "1148637"
spot_price = 79800.0
contract_strike = 79800.0
option_type = "PE"
exp_date = "2026-11-12"

print("Client dhan is not None:", smart_edge_engine.client.dhan is not None)
print("Testing quote_data directly:")
res = smart_edge_engine.client.dhan.quote_data({"BSE_FNO": [int(sec_id)]})
print("Direct quote_data response:", res)

prem = smart_edge_engine.client.get_option_ltp(
    sec_id, spot_price, contract_strike, option_type, expiry_date=exp_date
)
print("Returned from get_option_ltp:", prem)
