import sqlite3
import os

db_path = os.path.join("data", "trading_journal.db")
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT id, symbol, entry_price, exit_price, gross_pnl, net_pnl FROM trades;")
    rows = c.fetchall()
    print("Trades in DB:")
    for r in rows:
        print(r)
    
    # Delete the synthetic silver trade
    c.execute("DELETE FROM trades WHERE symbol LIKE 'SILVERM%' AND entry_price < 3000;")
    deleted = c.rowcount
    conn.commit()
    conn.close()
    print(f"\nDeleted {deleted} synthetic trade(s) from trading_journal.db")
    
    # Also regenerate trading_journal.csv
    from journal import journal
    journal.export_csv()
    print("Regenerated trading_journal.csv")
