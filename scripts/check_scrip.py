import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from scrip_master import scrip_master

c1 = scrip_master.find_by_security_id(582921)
print(f"582921: {c1}")
c2 = scrip_master.find_by_security_id(582922)
print(f"582922: {c2}")

# Let's search all CRUDEOIL options expiring Nov 2026
matches = scrip_master.search(name="CRUDEOIL", segment="MCX_COMM")
print(f"Total MCX CRUDEOIL contracts: {len(matches)}")
for m in matches[:10]:
    print(m)
