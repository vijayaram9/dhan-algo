import sqlite3
conn = sqlite3.connect("data/trading_journal.db")
c = conn.cursor()
for r in c.execute("SELECT id, symbol, quantity, trade_rationale, mode FROM trades ORDER BY id DESC LIMIT 5"):
    print(r)
