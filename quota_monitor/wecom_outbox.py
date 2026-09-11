"""Public quota delivery state, separate from discovery episodes. No secrets stored."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

from .core import is_available
from .notification_window import filter_notification_window

HK = timezone(timedelta(hours=8))


def enqueue(pending, changes, *, now, enabled):
    result = list(pending) if isinstance(pending, list) else []
    rows = changes.get("newly_available", [])
    if enabled and rows:
        event = hashlib.sha256(json.dumps([now, rows], sort_keys=True).encode()).hexdigest()
        result.append(dict(id=event, rows=rows, created_at=now, expires_at=now+900,
                           next_attempt_at=now, attempts=0, delivered=[]))
    return result


def deliver(pending, snapshot, urls, *, now, format_message, send, persist):
    """Persist before send; retry only unsent groups, at most one event per poll."""
    today = datetime.fromtimestamp(now, HK).date()
    kept = []
    for item in pending:
        if not isinstance(item, dict) or now >= item.get("expires_at", 0):
            continue
        rows = [row for row in item.get("rows", []) if is_available(snapshot.get(tuple(row[0]), ""))]
        rows = filter_notification_window({"newly_available": rows}, today=today)["newly_available"]
        if rows:
            kept.append(dict(item, rows=rows))
    if not persist(kept):
        return kept, "persistence_failed"
    status = "waiting"
    for item in kept:
        if not urls or now < item["next_attempt_at"]:
            continue
        item["attempts"] += 1
        for url in urls:
            group = hashlib.sha256(url.encode()).hexdigest()
            if group in item["delivered"]:
                continue
            try:
                ok = send(url, format_message({"newly_available": item["rows"]}))
            except Exception:
                ok = False
            if ok:
                item["delivered"].append(group)
                persist(kept)  # Lost ACK may duplicate, but never repeat booking.
        if all(hashlib.sha256(u.encode()).hexdigest() in item["delivered"] for u in urls):
            kept.remove(item)
            status = "OK"
        else:
            item["next_attempt_at"] = now + min(300, 30 * 2 ** min(item["attempts"]-1, 4))
            status = "retry_pending"
        persist(kept)
        break
    return kept, status
