with open('app.py', 'r', encoding='utf-8') as f:
    text = f.read()

target = '            if engine.active_position:\n                return jsonify({\n                    "success": False,\n                    "error": f"Position already active: {engine.active_position.symbol}. Manage or exit it first."\n                }), 400'

replacement = '''            if engine.active_position:
                if data.get("replace"):
                    old_sym = engine.active_position.symbol
                    engine.log_sniper_event(f"⚡ User replaced active trade {old_sym} with new scalp: {chosen_asset} {chosen_action}", "REPLACE")
                    engine.execute_exit("⚡ Scalp Swapped / Replaced by User")
                else:
                    return jsonify({
                        "success": False,
                        "has_active_position": True,
                        "active_symbol": engine.active_position.symbol,
                        "error": f"Position already active: {engine.active_position.symbol}. Manage or exit it first."
                    }), 400'''

if target in text:
    text = text.replace(target, replacement, 1)
    with open('app.py', 'w', encoding='utf-8') as f:
        f.write(text)
    print("app.py patched with replace handler!")
else:
    # Check normalized CRLF/LF
    target_lf = target.replace('\r\n', '\n')
    text_lf = text.replace('\r\n', '\n')
    if target_lf in text_lf:
        text_lf = text_lf.replace(target_lf, replacement, 1)
        with open('app.py', 'w', encoding='utf-8') as f:
            f.write(text_lf)
        print("app.py patched with replace handler (normalized LF)!")
    else:
        print("Target not found in app.py")
