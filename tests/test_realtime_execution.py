import urllib.request
import json
import time
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

base = 'http://127.0.0.1:5000'

# 1. Check Homepage HTML
req = urllib.request.Request(base)
with urllib.request.urlopen(req) as resp:
    html = resp.read().decode('utf-8')
    assert resp.status == 200
    assert 'top-glance-hud' in html, 'Top Glanceable HUD missing!'
    assert 'trading-toast' in html, 'Trading Toast container missing!'
    assert 'btn-toggle-radar-sync' in html, 'Radar live sync toggle missing!'
    assert 'radar-filter-indian' in html, 'Indian Market Prominence filter missing!'
    print('[PASS] 1. Homepage rendered with Top Real-Time Levels HUD & Indian Market Prominence!')

# 2. Test Instant Paper Capital Update API
cap_data = json.dumps({'amount': 50000.0}).encode('utf-8')
cap_req = urllib.request.Request(f'{base}/api/set-paper-capital', data=cap_data, headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(cap_req) as resp:
    res = json.loads(resp.read().decode('utf-8'))
    assert res['success'] == True
    assert res['paper_capital'] == 50000.0
    msg = res['message']
    print(f'[PASS] 2. Set paper capital to Rs.50,000 verified: {msg}')

# 3. Test Sniper Execution in Paper Mode (Instant 1-Click Scalp)
sniper_data = json.dumps({'asset': 'NIFTY', 'action': 'BUY_CE', 'mode': 'PAPER', 'lots': 1}).encode('utf-8')
sniper_req = urllib.request.Request(f'{base}/api/trigger-best-sniper', data=sniper_data, headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(sniper_req) as resp:
    res = json.loads(resp.read().decode('utf-8'))
    assert res['success'] == True, f"Sniper dispatch failed: {res}"
    msg3 = res['message']
    print(f'[PASS] 3. 1-Click Sniper Scalp executed: {msg3}')

# 4. Verify Active Position in Engine Status
with urllib.request.urlopen(f'{base}/api/status') as resp:
    stat = json.loads(resp.read().decode('utf-8'))
    pos = stat.get('active_position')
    assert pos is not None, 'Active position should be tracking!'
    sym = pos['symbol']
    pnl = pos['pnl']
    print(f'[PASS] 4. Position actively tracking in engine: {sym} | P&L: Rs.{pnl}')

# 5. Test Square-off
sq_req = urllib.request.Request(f'{base}/api/square-off', data=b'{}', headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(sq_req) as resp:
    res = json.loads(resp.read().decode('utf-8'))
    assert res['success'] == True
    msg5 = res['message']
    print(f'[PASS] 5. Square-off executed successfully: {msg5}')

# 6. Test Multi-Asset Opportunities Scanner with Range / Trend Adaptive Logic
with urllib.request.urlopen(f'{base}/api/opportunities') as resp:
    opps = json.loads(resp.read().decode('utf-8'))
    assert opps['success'] == True
    assert len(opps['opportunities']) > 0
    top = opps['opportunities'][0]
    t_name = top['display_name']
    t_dir = top['direction']
    t_strat = top['recommended_strategy']
    print(f'[PASS] 6. Opportunity radar active: #1 Top Pick {t_name} ({t_dir}) | Strategy: {t_strat}')

# 7. Reset Session for Clean Slate
reset_req = urllib.request.Request(f'{base}/api/reset-session', data=b'{}', headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(reset_req) as resp:
    res = json.loads(resp.read().decode('utf-8'))
    assert res['success'] == True
    print('[PASS] 7. Session reset clean!')

print('\nALL 7 TESTS PASSED FLAWLESSLY!')
