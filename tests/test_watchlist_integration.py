import urllib.request
import json

base = 'http://127.0.0.1:5000'

# 1. Check Homepage
req = urllib.request.Request(base)
with urllib.request.urlopen(req) as resp:
    html = resp.read().decode('utf-8')
    assert resp.status == 200
    assert 'sarva_dharma_icon.png' in html, 'Sarva Dharma logo missing from HTML!'
    assert 'Add Stock / Call / Fut' in html, 'Add Stock button missing from HTML!'
    assert 'add-instrument-modal' in html, 'Add instrument modal missing!'
    print('[PASS] 1. Homepage rendered with Sarva Dharma logo & Scrip Manager!')

# 2. Check Static Image
img_req = urllib.request.Request(f'{base}/static/images/sarva_dharma_icon.png')
with urllib.request.urlopen(img_req) as resp:
    assert resp.status == 200
    img_data = resp.read()
    assert len(img_data) > 1000
    print(f'[PASS] 2. Sarva Dharma logo served directly: {len(img_data)} bytes!')

# 3. Check Watchlist API
with urllib.request.urlopen(f'{base}/api/watchlist') as resp:
    data = json.loads(resp.read().decode('utf-8'))
    assert data['success'] == True
    assets_count = len(data['assets'])
    print(f'[PASS] 3. Watchlist API returned {assets_count} default assets!')

# 4. Add Custom Stock (TATAMOTORS)
add_payload = json.dumps({
    'key': 'TATAMOTORS',
    'display_name': 'Tata Motors Ltd',
    'category': 'STOCK',
    'exchange': 'NSE',
    'lot_size': 1,
    'baseline_price': 980.0,
    'strike_step': 10
}).encode('utf-8')
add_req = urllib.request.Request(f'{base}/api/watchlist/add', data=add_payload, headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(add_req) as resp:
    res = json.loads(resp.read().decode('utf-8'))
    assert res['success'] == True
    name = res['asset']['display_name']
    sym = res['asset']['key']
    print(f'[PASS] 4. Successfully added custom asset: {name} ({sym})')

# 5. Evaluate Custom Asset in Market Intelligence
with urllib.request.urlopen(f'{base}/api/market-intelligence?asset=TATAMOTORS') as resp:
    mi = json.loads(resp.read().decode('utf-8'))
    assert mi['status'] == 'success'
    assert mi['asset']['key'] == 'TATAMOTORS'
    spot = mi['spot_price']
    act = mi['verdict']['action']
    print(f'[PASS] 5. Evaluated custom asset intelligence: Spot Rs.{spot}, Decision: {act}')

# 6. Remove Custom Stock
rem_payload = json.dumps({'key': 'TATAMOTORS'}).encode('utf-8')
rem_req = urllib.request.Request(f'{base}/api/watchlist/remove', data=rem_payload, headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(rem_req) as resp:
    res = json.loads(resp.read().decode('utf-8'))
    assert res['success'] == True
    msg = res['message']
    print(f'[PASS] 6. Successfully removed custom asset from desk: {msg}')

print('\nALL 6 TESTS PASSED FLAWLESSLY!')
