import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import py_compile
for f in ["smart_edge_engine.py", "scrip_master.py", "contract_selector.py", "app.py"]:
    py_compile.compile(f, doraise=True)
print("COMPILE OK")

from scrip_master import ScripMaster
ScripMaster._cached_options = {}
sm = ScripMaster()
for k, spot in [("CRUDEOILM", 8500), ("GOLDM", 142100), ("SILVERM", 186950), ("NATGASMINI", 282.7)]:
    for t in ["CE", "PE"]:
        c = sm.get_nifty_option(spot_price=spot, option_type=t, asset_key=k)
        print(k, t, c["trading_symbol"], c["security_id"], c["expiry"], "lot", c["lot_size"])

# Simulate the exact 06-Nov trade #2 path with new trailing rules
from smart_edge_engine import SmartEdgePosition
import inspect
print("Position fields ok:", "trailing_step_level" in inspect.signature(SmartEdgePosition).parameters)
