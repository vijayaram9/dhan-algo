with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_block = '''@app.route("/api/sniper-scan")
def api_sniper_scan():
    """
    Symmetrically scans all 7 instruments (NIFTY, BANKNIFTY, FINNIFTY, SENSEX, NIFTYIT, RELIANCE, HDFCBANK)
    for immediate high-velocity Call and Put opportunities that fit strictly within the user's active capital.
    Identifies the #1 Top Pick for 1-click execution.
    """
    try:'''

new_block = '''_cached_sniper_scan = None
_cached_sniper_time = 0.0

@app.route("/api/sniper-scan")
def api_sniper_scan():
    """
    Symmetrically scans all 7 instruments (NIFTY, BANKNIFTY, FINNIFTY, SENSEX, NIFTYIT, RELIANCE, HDFCBANK)
    for immediate high-velocity Call and Put opportunities that fit strictly within the user's active capital.
    Identifies the #1 Top Pick for 1-click execution.
    """
    global _cached_sniper_scan, _cached_sniper_time
    now_ts = time.time()
    if _cached_sniper_scan is not None and (now_ts - _cached_sniper_time) < 1.5:
        return jsonify(_cached_sniper_scan)
    try:'''

old_return = '''        return jsonify({
            "success": True,
            "active_capital": active_cap,
            "trading_mode": config.trading_mode,
            "top_pick": top_pick,
            "candidates": candidates[:10],
            "total_qualified": len(candidates),
            "timestamp": datetime.datetime.now().strftime("%I:%M:%S %p")
        })'''

new_return = '''        payload = {
            "success": True,
            "active_capital": active_cap,
            "trading_mode": config.trading_mode,
            "top_pick": top_pick,
            "candidates": candidates[:10],
            "total_qualified": len(candidates),
            "timestamp": datetime.datetime.now().strftime("%I:%M:%S %p")
        }
        _cached_sniper_scan = payload
        _cached_sniper_time = now_ts
        return jsonify(payload)'''

if old_block in content and old_return in content:
    content = content.replace(old_block, new_block, 1)
    content = content.replace(old_return, new_return, 1)
    with open('app.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print("Successfully patched app.py for sniper cache!")
else:
    print("Could not find exact block in app.py")
