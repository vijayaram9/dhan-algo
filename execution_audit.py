"""
Execution audit layer.
Wraps DhanTraderClient.place_order so every order intent is:
  1. Recorded in order_queue.db (persistent audit trail) with a unique intent id.
  2. Checked against a duplicate-order guard (same security + side within a short window).
  3. Marked COMPLETED / FAILED with the broker response.
Existing strategy logic and order parameters are NOT changed.
"""
import time
import threading
from order_queue import enqueue_intent, mark_intent_done

DUPLICATE_WINDOW_SEC = 5
_recent = {}
_lock = threading.Lock()


def install(client, strategy_name: str = "SMART_EDGE"):
    if getattr(client, "_audit_installed", False):
        return client
    original = client.place_order

    def audited_place_order(*args, **kwargs):
        sec_id = str(kwargs.get("security_id", ""))
        side = str(kwargs.get("transaction_type", "")).upper()
        key = (sec_id, side)
        now = time.time()
        with _lock:
            last = _recent.get(key, 0)
            if side == "BUY" and now - last < DUPLICATE_WINDOW_SEC:
                print(f"[ExecutionAudit] Blocked duplicate BUY for {sec_id} ({now - last:.1f}s after previous).")
                return {"status": "failure", "remarks": "Duplicate order blocked by execution guard"}
            _recent[key] = now

        payload = {k: v for k, v in kwargs.items() if isinstance(v, (str, int, float, bool, type(None)))}
        try:
            intent_id = enqueue_intent(strategy_name, "ENTRY" if side == "BUY" else "EXIT", payload)
        except Exception as e:
            print(f"[ExecutionAudit] Queue write failed (order still sent): {e}")
            intent_id = None

        try:
            res = original(*args, **kwargs)
        except Exception:
            if intent_id:
                mark_intent_done(intent_id, success=False)
            raise
        if intent_id:
            ok = isinstance(res, dict) and res.get("status") == "success"
            mark_intent_done(intent_id, success=ok)
        return res

    client.place_order = audited_place_order
    client._audit_installed = True
    return client
