import sqlite3, sys
conn = sqlite3.connect("data/trading_journal.db")
c = conn.cursor()
rows = list(c.execute("SELECT id, trade_date, signal_time, exit_time, symbol, action, quantity, entry_price, exit_price, gross_pnl, net_pnl, exit_reason FROM trades ORDER BY id DESC LIMIT 5"))
for r in rows:
    clean_r = [str(x).encode('ascii', errors='replace').decode('ascii') for x in r]
    print(" | ".join(clean_r))
