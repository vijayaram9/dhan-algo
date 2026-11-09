from contract_selector import contract_selector
from smart_edge_engine import smart_edge_engine

spot = 79800.0
c = contract_selector.select_contract(
    spot_price=spot,
    action="BUY_PE",
    estimated_premium=0.0,
    strike_preference="ATM",
    asset_key="SENSEX",
    available_capital=25000.0
)

print("Selected contract:")
print("  custom_symbol:", c.custom_symbol)
print("  security_id:", c.security_id)
print("  strike_price:", c.strike_price)
print("  estimated_premium:", c.estimated_premium)
print("  expiry_date:", c.expiry_date)
print("  risk_budget_approved:", c.risk_budget_approved)
