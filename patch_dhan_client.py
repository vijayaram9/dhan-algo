# -*- coding: utf-8 -*-
with open("dhan_client.py", "r", encoding="utf-8") as f:
    c = f.read()

c = c.replace('return float(data.get("paper_capital", 25000.0))', 'return float(data.get("paper_capital", 150000.0))')

with open("dhan_client.py", "w", encoding="utf-8") as f:
    f.write(c)

print("[+] Patched dhan_client.py default paper capital to 150000.0")
