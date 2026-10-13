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
