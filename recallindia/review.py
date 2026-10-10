"""Confidence bands and the manual review queue.

High-confidence matches are published to the user straight away. Medium-confidence matches wait in the review
queue and are never shown or alerted until a reviewer approves them.
"""
import pandas as pd

from .config import AUTO_PUBLISH_SCORE, REVIEW_MIN_SCORE

AUTO = "auto_published"
PENDING = "pending_review"
APPROVED = "approved"
REJECTED = "rejected"
STATUSES = (AUTO, PENDING, APPROVED, REJECTED)
VISIBLE = (AUTO, APPROVED)


def band(score, auto=AUTO_PUBLISH_SCORE, floor=REVIEW_MIN_SCORE):
    """Return the status a new match with this score starts in, or None if it is discarded."""
    if score is None:
        return None
    if score >= auto:
        return AUTO
    if score >= floor:
        return PENDING
    return None


def confidence(score, auto=AUTO_PUBLISH_SCORE, floor=REVIEW_MIN_SCORE):
    status = band(score, auto, floor)
    return {AUTO: "high", PENDING: "medium"}.get(status, "low")


def assign_status(matches, auto=AUTO_PUBLISH_SCORE, floor=REVIEW_MIN_SCORE):
    """Add a status column and drop matches below the review floor."""
    out = matches.copy()
    out["status"] = [band(s, auto, floor) for s in out["score"]]
    return out[out["status"].notna()].reset_index(drop=True)


def visible(matches):
    return matches[matches["status"].isin(VISIBLE)]


def review_metrics(scored, gold, auto=AUTO_PUBLISH_SCORE, floor=REVIEW_MIN_SCORE):
    """How the two bands perform against hand-labelled true matches.

    Assumes a reviewer approves true matches and rejects false ones, so `recall_after_review` is the best the
    pipeline can reach and `queue_size` is the work it costs.
    """
    gold_pairs = set(zip(gold["purchase_id"].astype(str), gold["recall_id"].astype(str)))

    def pairs(df):
        return set(zip(df["purchase_id"].astype(str), df["recall_id"].astype(str)))

    auto_pairs = pairs(scored[scored["score"] >= auto])
    queue_pairs = pairs(scored[(scored["score"] >= floor) & (scored["score"] < auto)])
    auto_tp = len(auto_pairs & gold_pairs)
    queue_tp = len(queue_pairs & gold_pairs)
    n = len(gold_pairs) or 1
    return {
        "auto_threshold": auto,
        "review_floor": floor,
        "auto_published": len(auto_pairs),
        "auto_precision": round(auto_tp / len(auto_pairs), 3) if auto_pairs else None,
        "auto_recall": round(auto_tp / n, 3),
        "queue_size": len(queue_pairs),
        "queue_true_matches": queue_tp,
        "recall_after_review": round((auto_tp + queue_tp) / n, 3),
        "missed": len(gold_pairs) - auto_tp - queue_tp,
    }


def queue_frame(matches, recalls, purchases):
    """Join pending matches with the recall and purchase text a reviewer needs to decide."""
    q = matches[matches["status"] == PENDING]
    cols_r = ["recall_id", "brand", "product_name", "batch_no", "fbo_name", "reason", "recall_date"]
    cols_p = ["purchase_id", "product_name", "brand", "batch_no", "store", "purchase_date"]
    out = q.merge(recalls[cols_r].add_prefix("recall_").rename(columns={"recall_recall_id": "recall_id"}),
                  on="recall_id", how="left")
    out = out.merge(purchases[cols_p].add_prefix("purchase_").rename(columns={"purchase_purchase_id": "purchase_id"}),
                    on="purchase_id", how="left")
    return out.sort_values("score", ascending=False).reset_index(drop=True) if len(out) else pd.DataFrame(out)
