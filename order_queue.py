import os
import sqlite3
import json
import threading
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), 'order_queue.db')
_LOCK = threading.Lock()

def _init_db():
    with _LOCK:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS intents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy TEXT NOT NULL,
                intent_type TEXT NOT NULL,  -- ENTRY or EXIT
                payload TEXT NOT NULL,      -- JSON blob with order params
                status TEXT NOT NULL DEFAULT 'PENDING',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        ''')
        conn.commit()
        conn.close()

_init_db()

def enqueue_intent(strategy: str, intent_type: str, payload: dict) -> int:
    """Add a new order intent to the queue.
    Returns the generated intent id.
    """
    now = datetime.utcnow().isoformat()
    with _LOCK:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO intents (strategy, intent_type, payload, status, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (strategy, intent_type.upper(), json.dumps(payload), 'PENDING', now, now)
        )
        intent_id = cur.lastrowid
        conn.commit()
        conn.close()
    return intent_id

def get_pending_intents(limit: int = 10):
    """Fetch pending intents ordered by creation time."""
    with _LOCK:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "SELECT id, strategy, intent_type, payload FROM intents WHERE status='PENDING' ORDER BY created_at ASC LIMIT ?",
            (limit,)
        )
        rows = cur.fetchall()
        conn.close()
    intents = []
    for row in rows:
        intent_id, strategy, intent_type, payload_json = row
        intents.append({
            'id': intent_id,
            'strategy': strategy,
            'intent_type': intent_type,
            'payload': json.loads(payload_json)
        })
    return intents

def mark_intent_done(intent_id: int, success: bool = True, note: str = ''):
    """Mark an intent as COMPLETED or FAILED.
    """
    new_status = 'COMPLETED' if success else 'FAILED'
    now = datetime.utcnow().isoformat()
    with _LOCK:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "UPDATE intents SET status=?, updated_at=? WHERE id=?",
            (new_status, now, intent_id)
        )
        conn.commit()
        conn.close()

def purge_completed(older_than_days: int = 30):
    """Delete completed/failed intents older than N days to keep DB small."""
    cutoff = datetime.utcnow().timestamp() - older_than_days * 86400
    with _LOCK:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM intents WHERE status IN ('COMPLETED','FAILED') AND strftime('%s', created_at) < ?",
            (int(cutoff),)
        )
        conn.commit()
        conn.close()
