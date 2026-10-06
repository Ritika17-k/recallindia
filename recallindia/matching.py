import re

import pandas as pd
from rapidfuzz import fuzz, process

WEIGHTS = {"product": 0.40, "brand": 0.40, "batch": 0.20}
CANDIDATE_CUTOFF = 40
CANDIDATE_LIMIT = 25
THRESHOLDS = [50, 55, 60, 65, 70, 75, 80, 85, 90, 95]

SYNONYMS = {
    "hing": ["asafoetida"], "mirchi": ["chilli"], "mirch": ["chilli"], "chillies": ["chilli"], "chilly": ["chilli"],
    "chili": ["chilli"], "haldi": ["turmeric"], "jeera": ["cumin"], "zeera": ["cumin"], "dhania": ["coriander"],
    "aata": ["atta"], "atta": ["wheat", "flour"], "chaki": ["chakki"], "rava": ["semolina"], "rawa": ["semolina"],
    "suji": ["semolina"], "sooji": ["semolina"], "gur": ["jaggery"], "dahi": ["curd", "fermented", "milk"],
    "pwd": ["powder"], "namak": ["salt"], "mix": ["mixture"], "biscuits": ["biscuit"], "namkin": ["namkeen"],
}
CORPORATE = {"pvt", "ltd", "private", "limited", "m", "s", "co", "company", "foods", "food", "products", "product",
             "industries", "industry", "enterprises", "enterprise", "traders", "trading", "store", "stores", "mart",
             "supermarket", "hypermart", "hypermarket", "marketing", "agencies", "and", "the", "brand", "india",
             "unit", "of", "a", "llp", "inc", "prop", "distributors", "retail", "retails"}
GENERIC = {
    "chilli", "powder", "red", "kashmiri", "oil", "refined", "atta", "flour", "wheat", "ghee", "cow", "pure", "desi",
    "water", "drinking", "packaged", "mineral", "masala", "mixed", "garam", "salt", "rice", "dal", "tea", "coffee",
    "biscuit", "cake", "plum", "bread", "rusk", "mixture", "snacks", "namkeen", "cumin", "turmeric", "coriander",
    "paneer", "milk", "curd", "fermented", "honey", "jaggery", "vinegar", "sugar", "chutney", "pickle", "papad",
    "chips", "besan", "semolina", "asafoetida", "kachi", "ghani", "mustard", "soyabean", "sunflower", "olive",
    "fresh", "organic", "premium", "special", "chakki", "multigrain", "apple", "cider", "white", "wine", "raw",
    "unfiltered", "loose", "unbranded", "flavoured", "low", "sodium", "and", "with", "gm", "kg", "pack", "lal",
    "teekhi", "dates", "seeds", "whole", "roasted", "instant", "bakery", "bakes", "sweets", "garlic",
    "bombay", "kerala", "mix",
}
PLACEHOLDER_BATCH = {"", "NIL", "NA", "NO", "NONE", "LOOSE", "OTHER", "OTHERS", "0", "00", "X", "NV",
                     "SOLDASLOOSE", "LOOSESAMPLE", "LOOSECONDITION"}
BATCH_PREFIX_RE = re.compile(r"^\s*(batch|b|lot)\s*(no|number)?\s*[.:\-]*\s*", re.I)


def _present(v):
    return v is not None and not (isinstance(v, float) and pd.isna(v)) and str(v) != ""


def tokens(text):
    out = []
    for t in str(text or "").split():
        out.append(t)
        out.extend(SYNONYMS.get(t, []))
    return out


def key_tokens(text):
    return [t for t in str(text or "").split() if t not in CORPORATE and t not in GENERIC and len(t) > 1
            and not t.isdigit()]


def normalize_batch(value):
    if not _present(value):
        return None
    s = BATCH_PREFIX_RE.sub("", str(value)).upper()
    s = re.sub(r"[^A-Z0-9]", "", s)
    return None if s in PLACEHOLDER_BATCH or len(s) < 2 else s


def _token_found(token, pool):
    if token in pool:
        return True
    if len(token) < 4:
        return False
    return any(len(p) >= 4 and fuzz.ratio(token, p) >= 85 for p in pool)


def _coverage(keys, pool):
    if not keys:
        return None
    return 100.0 * sum(_token_found(k, pool) for k in keys) / len(keys)


def brand_score(recall, purchase):
    purchase_pool = set(tokens(f"{purchase.get('brand_norm') or ''} {purchase.get('product_norm') or ''}"))
    recall_pool = set(tokens(f"{recall.get('brand_norm') or ''} {recall.get('fbo_norm') or ''}"))
    scores = [_coverage(key_tokens(recall.get("brand_norm")), purchase_pool),
              _coverage(key_tokens(recall.get("fbo_norm")), purchase_pool),
              _coverage(key_tokens(purchase.get("brand_norm")), recall_pool)]
    scores = [s for s in scores if s is not None]
    return max(scores) if scores else None


def shared_brand_tokens(recall, purchase):
    purchase_pool = set(tokens(f"{purchase.get('brand_norm') or ''} {purchase.get('product_norm') or ''}"))
    recall_keys = set(key_tokens(recall.get("brand_norm"))) | set(key_tokens(recall.get("fbo_norm")))
    shared = {k for k in recall_keys if _token_found(k, purchase_pool)}
    shared |= {p for p in purchase_pool if any(_token_found(p, {k}) for k in shared)}
    return shared


def product_score(recall, purchase):
    shared = shared_brand_tokens(recall, purchase)
    p = [t for t in tokens(purchase.get("product_norm")) if t not in shared]
    r = [t for t in tokens(f"{recall.get('product_norm') or ''} {recall.get('brand_norm') or ''}")
         if t not in shared]
    if not p or not r:
        return None
    return float(fuzz.token_set_ratio(" ".join(sorted(set(p))), " ".join(sorted(set(r)))))


def batch_score(recall, purchase):
    rb, pb = normalize_batch(recall.get("batch_no")), normalize_batch(purchase.get("batch_no"))
    if not rb or not pb:
        return None
    if rb == pb or (min(len(rb), len(pb)) >= 4 and (rb in pb or pb in rb)):
        return 100.0
    return 0.0


def score_pair(recall, purchase):
    parts = {k: v for k, v in {"product": product_score(recall, purchase), "brand": brand_score(recall, purchase),
                               "batch": batch_score(recall, purchase)}.items() if v is not None}
    weight = sum(WEIGHTS[k] for k in parts)
    score = sum(WEIGHTS[k] * v for k, v in parts.items()) / weight if weight else 0.0

    reasons = []
    if "brand" not in parts:
        score = min(score, 70.0)
        reasons.append("no brand to compare")
    elif parts["brand"] < 50:
        score = min(score, 50.0)
        reasons.append("brand differs")
    if parts.get("product", 100) < 60:
        score = min(score, 60.0)
        reasons.append("different product")
    if parts.get("batch") == 0.0:
        score = min(score, 60.0)
        reasons.append("batch number differs")
    pdate, mfg, exp = purchase.get("purchase_date"), recall.get("manufacture_date"), recall.get("expiry_date")
    if _present(pdate) and _present(mfg) and pdate < mfg:
        score = 0.0
        reasons.append("bought before the recalled batch was made")
    elif _present(pdate) and _present(exp) and pdate > exp:
        score *= 0.5
        reasons.append("bought after the recalled batch expired")
    return round(score, 1), {k: round(v, 1) for k, v in parts.items()}, reasons


def search_text(record):
    return " ".join(str(record.get(k) or "") for k in ("brand_norm", "product_norm", "fbo_norm")).strip()


def candidates_in_memory(recalls, purchase, limit=CANDIDATE_LIMIT):
    if "search_norm" in recalls.columns:
        choices = dict(zip(recalls.index, recalls["search_norm"]))
    else:
        choices = {i: search_text(r) for i, r in recalls.to_dict("index").items()}
    query = f"{purchase.get('brand_norm') or ''} {purchase.get('product_norm') or ''}".strip()
    hits = process.extract(" ".join(tokens(query)), choices, scorer=fuzz.token_set_ratio,
                           score_cutoff=CANDIDATE_CUTOFF, limit=limit)
    return [idx for _, _, idx in hits]


def score_candidates(recalls, purchases):
    rows = []
    rec_records = recalls.to_dict("index")
    for p in purchases.to_dict("records"):
        for idx in candidates_in_memory(recalls, p):
            r = rec_records[idx]
            score, parts, reasons = score_pair(r, p)
            rows.append({"purchase_id": p.get("purchase_id"), "recall_id": r.get("recall_id"), "score": score,
                         "product_score": parts.get("product"), "brand_score": parts.get("brand"),
                         "batch_score": parts.get("batch"), "notes": "; ".join(reasons)})
    cols = ["purchase_id", "recall_id", "score", "product_score", "brand_score", "batch_score", "notes"]
    return pd.DataFrame(rows, columns=cols)


def match_all(recalls, purchases, threshold=75.0):
    scored = score_candidates(recalls, purchases)
    return scored[scored["score"] >= threshold].sort_values(["purchase_id", "score"], ascending=[True, False]) \
        .reset_index(drop=True)


def _metrics(tp, fp, fn):
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(precision, 3), "recall": round(recall, 3),
            "f1": round(f1, 3)}


def _best(out):
    return out.sort_values(["f1", "recall", "threshold"], ascending=[False, False, True]).iloc[0].to_dict()


def evaluate(scored_pairs, labels, thresholds=None):
    lab = labels.copy()
    lab["is_match"] = lab["is_match"].astype(int)
    merged = lab.merge(scored_pairs[["purchase_id", "recall_id", "score"]], on=["purchase_id", "recall_id"], how="left")
    merged["score"] = merged["score"].fillna(0.0)
    results = []
    for t in thresholds or THRESHOLDS:
        pred = merged["score"] >= t
        results.append({"threshold": t, **_metrics(int((pred & (merged["is_match"] == 1)).sum()),
                                                   int((pred & (merged["is_match"] == 0)).sum()),
                                                   int((~pred & (merged["is_match"] == 1)).sum()))})
    out = pd.DataFrame(results)
    return out, _best(out)


def evaluate_end_to_end(recalls, purchases, gold, thresholds=None):
    scored = score_candidates(recalls, purchases)
    gold_pairs = set(zip(gold["purchase_id"].astype(str), gold["recall_id"].astype(str)))
    results = []
    for t in thresholds or THRESHOLDS:
        hit = scored[scored["score"] >= t]
        predicted = set(zip(hit["purchase_id"].astype(str), hit["recall_id"].astype(str)))
        results.append({"threshold": t, **_metrics(len(predicted & gold_pairs), len(predicted - gold_pairs),
                                                   len(gold_pairs - predicted))})
    out = pd.DataFrame(results)
    retrieved = set(zip(scored["purchase_id"].astype(str), scored["recall_id"].astype(str)))
    best = _best(out)
    best["candidate_recall"] = round(len(retrieved & gold_pairs) / len(gold_pairs), 3) if gold_pairs else None
    return out, best, scored


def score_labeled_pairs(recalls, purchases, labels):
    r = recalls.set_index("recall_id").to_dict("index")
    p = purchases.set_index("purchase_id").to_dict("index")
    rows = []
    for _, row in labels.iterrows():
        rec, pur = r.get(row["recall_id"]), p.get(row["purchase_id"])
        score = score_pair(rec, pur)[0] if rec and pur else 0.0
        rows.append({"purchase_id": row["purchase_id"], "recall_id": row["recall_id"], "score": score})
    return pd.DataFrame(rows)
