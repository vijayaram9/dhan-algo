# -*- coding: utf-8 -*-
with open("contract_selector.py", "r", encoding="utf-8") as f:
    c = f.read()

c = c.replace("self.total_capital = 25000.0", "self.total_capital = float(getattr(config, 'account_capital', 150000.0))")
c = c.replace("self.preferred_risk = 250.0", "self.preferred_risk = self.total_capital * 0.01")
c = c.replace("self.absolute_risk_ceiling = 500.0", "self.absolute_risk_ceiling = float(getattr(config, 'max_daily_loss', 3000.0))")
c = c.replace("available_capital: float = 25000.0", "available_capital: float = 150000.0")

with open("contract_selector.py", "w", encoding="utf-8") as f:
    f.write(c)

print("[+] Patched contract_selector.py successfully")
