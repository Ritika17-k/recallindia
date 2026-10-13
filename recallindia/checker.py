import re
from .clean import normalize_name
from .config import AUTO_PUBLISH_SCORE, MATCH_THRESHOLD
from .matching import score_pair

DISCLAIMER = ("RecallIndia gives regulatory information from FSSAI sources, not medical advice. "
              "Rules marked verified=false are seed entries still being checked against the official text.")

RANK = {"no issues found": 0, "check": 1, "caution": 2, "avoid": 3}

class ProductNotFound(Exception):
    pass


LABEL_START_RE = re.compile(r"\b[il1|]ngred[il1]ents?\b\s*[:;.\-]?", re.I)
LABEL_END_RE = re.compile(r"\b(nutrition(al)?|allergen|allergy|contains\s*:|may\s+contain|best\s+before|use\s+by|"
                          r"storage|store\s+in|net\s+(wt|weight|qty|quantity)|mrp|mfd|mfg|manufactured|packed|"
                          r"fssai|lic\.?\s*no|customer\s+care|batch|energy\s*\(|per\s+100)", re.I)


def label_ingredients(text):
    """The ingredient list from OCR text of a pack label: from "Ingredients:" to the next section."""
    text = re.sub(r"[ \t]+", " ", str(text or ""))
    start = LABEL_START_RE.search(text)
    body = text[start.end():] if start else text
    end = LABEL_END_RE.search(body)
    body = body[:end.start()] if end else body
    return re.sub(r"\s*\n\s*", " ", body).strip(" .:;-")

def find_recalls(recall_lookup, product_name, brand, threshold=MATCH_THRESHOLD):
    if not product_name or recall_lookup is None:
        return []
    purchase = {"product_norm": normalize_name(product_name), "brand_norm": normalize_name(brand)}
    out = []
    for recall in recall_lookup(purchase["product_norm"], purchase["brand_norm"]):
        score, parts, _ = score_pair(recall, purchase)
        if score >= threshold:
            out.append({"recall_id": recall["recall_id"], "product_name": recall["product_name"],
                        "brand": recall.get("brand"), "reason": recall.get("reason"),
                        "recall_date": str(recall["recall_date"]) if recall.get("recall_date") else None,
                        "batch_no": recall.get("batch_no"), "score": score})
    return sorted(out, key=lambda r: -r["score"])

def add_global_status(findings, global_status):
    """Attach each ingredient's status in other jurisdictions. Information only; the verdict uses FSSAI rules."""
    for f in findings:
        rows = global_status.table[global_status.table["substance_id"] == f["substance_id"]]
        f["abroad"] = [{"jurisdiction": r["jurisdiction"], "status": r["status"], "note": r["note"],
                        "source_url": r["source_url"]} for r in rows.to_dict("records")]
    return findings


def check_product(rulebook, barcode=None, ingredients_text=None, category=None, product_name=None, brand=None,
                  fetch_product=None, recall_lookup=None, global_status=None):
    product, codes = None, []
    if barcode:
        product = fetch_product(barcode) if fetch_product else None
        if not product and not ingredients_text:
            raise ProductNotFound(f"Barcode {barcode} was not found. Type the ingredients from the label instead.")
        if product:
            ingredients_text = ingredients_text or product.get("ingredients_text")
            codes = product.get("additive_codes") or []
            category = category or product.get("category")
            product_name = product_name or product.get("product_name")
            brand = brand or product.get("brand")
    if not ingredients_text and not codes:
        raise ProductNotFound("No ingredients to check. Add the ingredient list from the label.")
    result = rulebook.check(ingredients_text, category, codes)
    if global_status is not None:
        add_global_status(result["findings"], global_status)
    result["product"] = {"barcode": barcode, "product_name": product_name, "brand": brand,
                         "source": product.get("source") if product else "label text",
                         "image_url": product.get("image_url") if product else None}
    result["ingredients_text"] = ingredients_text
    result["recalls"] = find_recalls(recall_lookup, product_name, brand, threshold=AUTO_PUBLISH_SCORE)
    if result["recalls"]:
        if RANK[result["verdict"]] < RANK["caution"]:
            result["verdict"] = "caution"
        result["label_notes"].append({"ingredient": None, "message": "This product name matches a recall notice. "
                                      "Recalls cover specific batches, so compare the batch number on your pack."})
    result["disclaimer"] = DISCLAIMER
    return result
