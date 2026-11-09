# -*- coding: utf-8 -*-
with open("smart_edge_engine.py", "r", encoding="utf-8") as f:
    c = f.read()

# 1. Require strict 80%+ institutional confluence (never trigger on weak 70% chop)
c = c.replace(
    'if not top_opp.get("is_actionable") or top_opp.get("confluence_score", 0) < 70:',
    'if not top_opp.get("is_actionable") or top_opp.get("confluence_score", 0) < 80:'
)

# 2. Strict VWAP validation (No loose 0.5% counter-trend buffer)
old_vwap = """                    # Allow 0.5% buffer around VWAP for high-probability pullback / continuation entries
                    if opp_action == "BUY_CE" and opp_spot < (real_vwap * 0.995):
                        continue
                    if opp_action == "BUY_PE" and opp_spot > (real_vwap * 1.005):
                        continue"""

new_vwap = """                    # Strict VWAP Directional Alignment:
                    # Never buy Calls below VWAP (resisting ceiling). Never buy Puts above VWAP (supporting floor).
                    if opp_action == "BUY_CE" and opp_spot < real_vwap:
                        continue
                    if opp_action == "BUY_PE" and opp_spot > real_vwap:
                        continue"""

c = c.replace(old_vwap, new_vwap)

with open("smart_edge_engine.py", "w", encoding="utf-8") as f:
    f.write(c)

print("[+] Successfully patched strict 80%+ confluence and strict VWAP rules in smart_edge_engine.py")
