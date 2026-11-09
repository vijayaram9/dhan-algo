import os
import sys
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

client_id = os.getenv("DHAN_CLIENT_ID", "").strip()
access_token = os.getenv("DHAN_ACCESS_TOKEN", "").strip()

print("\n=======================================================")
print("        🔍 DHAN API CREDENTIALS VERIFIER 🔍            ")
print("=======================================================")

if not client_id or client_id == "YOUR_CLIENT_ID":
    print("\n[!] Error: DHAN_CLIENT_ID is missing or still set to placeholder in .env")
    print("    Please open D:\\Automation Projects\\dhan-nifty-algo\\.env and paste your Client ID.")
    sys.exit(1)

if not access_token or access_token == "YOUR_ACCESS_TOKEN":
    print("\n[!] Error: DHAN_ACCESS_TOKEN is missing or still set to placeholder in .env")
    print("    Please open D:\\Automation Projects\\dhan-nifty-algo\\.env and paste your Access Token.")
    sys.exit(1)

print(f"[*] Client ID detected: {client_id}")
print(f"[*] Access Token detected: {access_token[:8]}...{access_token[-6:]} (Length: {len(access_token)})")
print("[*] Testing live connection to Dhan HQ servers...")

try:
    from dhanhq import dhanhq, DhanContext
    
    # In dhanhq 2.x, initialize via DhanContext
    try:
        ctx = DhanContext(client_id, access_token)
        dhan = dhanhq(ctx)
    except Exception:
        dhan = dhanhq(client_id, access_token)
    
    # 1. Fetch Fund Limits
    funds = dhan.get_fund_limits()
    if funds.get("status") == "success":
        data = funds.get("data", {})
        avail = data.get("availabelBalance", 0.0)
        collateral = data.get("collateralAmount", 0.0)
        print("\n[SUCCESS] Connected to DhanHQ Successfully!")
        print(f"  * Available Cash Balance : Rs.{avail:,.2f}")
        print(f"  * Collateral Amount      : Rs.{collateral:,.2f}")
    else:
        print(f"\n[!] Dhan API returned error: {funds.get('remarks', funds)}")
        sys.exit(1)

    # 2. Fetch Open Positions
    pos = dhan.get_positions()
    if pos.get("status") == "success":
        positions = pos.get("data", [])
        print(f"  • Current Open Positions : {len(positions)}")
    
    print("\n[✓] Your Dhan account is fully connected and ready for automated trading.")
    print("=======================================================\n")

except Exception as e:
    print(f"\n[!] Connection exception: {e}")
    sys.exit(1)
