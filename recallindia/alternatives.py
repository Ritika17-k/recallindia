"""Suggested alternatives: similar products in the same category that don't list the flagged ingredients.

Worded as facts ("doesn't list INS 124", "no open FSSAI recall"), never as "safer" or "healthier". A product with
no ingredient information is never suggested, because nothing can be said about what it contains.
"""
from .checker import find_recalls
from .config import AUTO_PUBLISH_SCORE


def flagged_substances(check_result):
    return {f["substance_id"]: (f["substance"], f.get("ins")) for f in check_result["findings"] if f["severity"] > 0}


def _label(name, ins):
    return f"{name} (INS {ins})" if ins else name


def find_alternatives(rulebook, product, check_result, search, recall_lookup=None, limit=4, category_tags=None):
    """Up to `limit` products from the same Open Food Facts category without the original's flagged ingredients.

    `search(tag)` returns products shaped like openfoodfacts.product_from(); `recall_lookup` is the same callable
    the checker uses, so a suggestion never has a high-confidence recall match.
    """
    flagged = flagged_substances(check_result)
    if not flagged:
        return {"reason": "no_flags", "items": []}
    tags = list(category_tags if category_tags is not None else product.get("categories_tags") or [])
    if not tags:
        return {"reason": "no_category", "items": []}
    category = check_result.get("category")
    out, seen = [], {str(product.get("barcode") or "")}
    for tag in reversed(tags[-3:]):  # most specific category first, then broader ones
        for cand in search(tag):
            code = str(cand.get("barcode") or "")
            if code in seen or not cand.get("product_name"):
                continue
            seen.add(code)
            if not cand.get("ingredients_text") and not cand.get("additive_codes"):
                continue
            check = rulebook.check(cand.get("ingredients_text"), category or cand.get("category"), cand.get("additive_codes") or [])
            present = {f["substance_id"] for f in check["findings"]}
            if present & set(flagged) or any(f["severity"] >= 3 for f in check["findings"]):
                continue
            if find_recalls(recall_lookup, cand["product_name"], cand.get("brand"), threshold=AUTO_PUBLISH_SCORE):
                continue
            notes = [f"Doesn't list {_label(*flagged[s])}" for s in flagged] + ["No open FSSAI recall"]
            out.append({"barcode": code, "product_name": cand["product_name"], "brand": cand.get("brand"),
                        "image_url": cand.get("image_url"), "category_tag": tag, "notes": notes})
            if len(out) >= limit:
                return {"reason": None, "items": out}
    return {"reason": None if out else "none_found", "items": out}


# ---------- alternatives to a recalled product ----------

# Words that say nothing about which product it is, so they're left out of the search and brand comparison.
_GENERIC = {"and", "the", "with", "for", "new", "pack", "packet", "pouch", "box", "jar", "bottle", "pvt", "ltd",
            "limited", "private", "company", "foods", "food", "products", "product", "india", "indian", "industries",
            "enterprises", "traders", "brand", "premium", "pure", "special", "fresh", "quality", "best"}


def _words(text):
    from .clean import normalize_name
    return [w for w in normalize_name(text).split() if len(w) > 2 and not w.isdigit() and w not in _GENERIC]


def recall_query(recall, query=None):
    """Search words for products like the recalled one.

    `query` is the user's own product name (from My Products or an alert) when there is one. Otherwise the recall
    itself: FoSCoS often puts the real product in the brand column ("Eastern - CHILLI POWDER") and a food category
    in the product column, so the part after " - " is preferred. The recalled brand's own words are dropped, so
    "RAMDEV HING 50G" searches for "hing" rather than for Ramdev.
    """
    text = (query or "").strip()
    if not text:
        brand = recall.get("brand") or ""
        text = brand.split(" - ", 1)[1] if " - " in brand else (recall.get("product_name") or "")
    words = _words(text)
    brand_words = set(_words((recall.get("brand") or "").split(" - ", 1)[0]))
    kept = [w for w in words if w not in brand_words]
    return " ".join((kept or words)[:4])


def _same_brand(a, b):
    """Two brand names refer to the same brand (shared distinctive word, or one contains the other)."""
    wa, wb = set(_words(a)), set(_words(b))
    return bool(wa and wb and (wa & wb))


def _relevant(cand, query_words):
    """The candidate's name or Open Food Facts categories share a word (or its plural) with the search."""
    tags = " ".join(t.split(":", 1)[-1].replace("-", " ") for t in cand.get("categories_tags") or [])
    hay = set(_words(f"{cand.get('product_name') or ''} {tags}"))
    return any(h == q or (len(q) > 3 and (h.startswith(q) or q.startswith(h)) and len(h) > 3)
               for q in query_words for h in hay)


def find_recall_alternatives(recall, search, recall_lookup=None, query=None, limit=4, min_score=None):
    """Up to `limit` similar products, from other brands, that match no FSSAI recall.

    `search(terms)` returns products shaped like openfoodfacts.product_from(). A candidate is dropped if it has no
    brand (nothing to check a recall against), shares a brand with the recalled product, or matches any recall at
    review confidence or above, which is stricter than the alert threshold on purpose. Facts only: "no FSSAI recall
    on record" is not a claim that a product is safe.
    """
    from .config import REVIEW_MIN_SCORE
    floor = REVIEW_MIN_SCORE if min_score is None else min_score
    q = recall_query(recall, query)
    if not q:
        return {"query": q, "reason": "no_query", "items": []}
    query_words = q.split()
    recalled_brand = (recall.get("brand") or "").split(" - ", 1)[0]
    out, seen = [], set()
    for cand in search(q):
        code = str(cand.get("barcode") or "")
        name = cand.get("product_name")
        if not code or not name or code in seen:
            continue
        seen.add(code)
        if not cand.get("brand") or _same_brand(cand["brand"], recalled_brand) or not _relevant(cand, query_words):
            continue
        if find_recalls(recall_lookup, name, cand["brand"], threshold=floor):
            continue
        out.append({"barcode": code, "product_name": name, "brand": cand["brand"], "image_url": cand.get("image_url"),
                    "notes": ["No FSSAI recall on record", "Different brand"]})
        if len(out) >= limit:
            break
    return {"query": q, "reason": None if out else "none_found", "items": out}
