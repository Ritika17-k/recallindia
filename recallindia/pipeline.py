"""End-to-end flows shared by the command line and the API."""
from . import alerts
from .config import ALERT_CHANNEL
from .review import AUTO, PENDING


def _counts(matches):
    if matches.empty:
        return {AUTO: 0, PENDING: 0}
    vc = matches["status"].value_counts()
    return {AUTO: int(vc.get(AUTO, 0)), PENDING: int(vc.get(PENDING, 0))}


def ingest_purchases(engine, purchases, user_id=None, source=None, send=True, channel=None):
    """Save new purchases, match only those against the recalls, and alert on high-confidence matches."""
    from .db import match_from_db, save_matches, save_purchases
    if purchases.empty:
        return {"purchases": 0, "auto_published": 0, "pending_review": 0, "alerts_queued": 0, "alerts_sent": 0}
    n = save_purchases(engine, purchases, user_id=user_id, source=source)
    matches = match_from_db(engine, purchase_ids=list(purchases["purchase_id"]))
    save_matches(engine, matches)
    queued = alerts.queue_alerts(engine, channel or ALERT_CHANNEL)
    sent = alerts.dispatch(engine, channel)[0] if send else 0
    c = _counts(matches)
    return {"purchases": n, "auto_published": c[AUTO], "pending_review": c[PENDING], "alerts_queued": queued,
            "alerts_sent": sent, "matches": matches}


def refresh(engine, recalls=None, send=True, channel=None):
    """Load fresh recalls, rematch every purchase, queue and send alerts. Logged in the runs table."""
    from .db import Recall, finish_run, match_from_db, new_recall_count, save_matches, start_run, upsert
    run_id = start_run(engine)
    if recalls is not None:
        upsert(engine, Recall, recalls.dropna(subset=["recall_id", "product_name"]), "recall_id")
    new = new_recall_count(engine)
    matches = match_from_db(engine)
    save_matches(engine, matches)
    queued = alerts.queue_alerts(engine, channel or ALERT_CHANNEL)
    sent, failed = alerts.dispatch(engine, channel) if send else (0, 0)
    c = _counts(matches)
    summary = {"run_id": run_id, "new_recalls": new, "auto_published": c[AUTO], "pending_review": c[PENDING],
               "alerts_queued": queued, "alerts_sent": sent, "alerts_failed": failed}
    finish_run(engine, run_id, **{k: v for k, v in summary.items() if k != "run_id"})
    return summary
