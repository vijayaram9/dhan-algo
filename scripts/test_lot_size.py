import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from scrip_master import ScripMaster
from contract_selector import contract_selector

sm = ScripMaster()
c1 = sm.get_nifty_option(spot_price=8500.0, option_type="CE", asset_key="CRUDEOILM")
print("CRUDEOILM get_nifty_option:", c1)

sel = contract_selector.select_contract(spot_price=8500.0, action="BUY_CE", estimated_premium=360.0, strike_preference="ATM", asset_key="CRUDEOILM", available_capital=100000.0)
print("CRUDEOILM select_contract:", sel.custom_symbol, "lot_size:", sel.lot_size, "number_of_lots:", sel.number_of_lots)

# What if asset_key was omitted?
sel_no_key = contract_selector.select_contract(spot_price=8500.0, action="BUY_CE", estimated_premium=360.0, strike_preference="ATM", available_capital=100000.0)
print("No asset_key select_contract:", sel_no_key.custom_symbol, "lot_size:", sel_no_key.lot_size, "number_of_lots:", sel_no_key.number_of_lots)
