from .clean import normalize_name
from .config import MATCH_THRESHOLD
from .matching import score_pair

DISCLAIMER = ("RecallIndia gives regulatory information from FSSAI sources, not medical advice. "
              "Rules marked verified=false are seed entries still being checked against the official text.")

RANK = {"no issues found": 0, "check": 1, "caution": 2, "avoid": 3}

class ProductNotFound(Exception):
    pass

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

def check_product(rulebook, barcode=None, ingredients_text=None, category=None, product_name=None, brand=None,
                  fetch_product=None, recall_lookup=None):
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
    result["product"] = {"barcode": barcode, "product_name": product_name, "brand": brand,
                         "source": product.get("source") if product else "label text",
                         "image_url": product.get("image_url") if product else None}
    result["ingredients_text"] = ingredients_text
    result["recalls"] = find_recalls(recall_lookup, product_name, brand)
    if result["recalls"]:
        if RANK[result["verdict"]] < RANK["caution"]:
            result["verdict"] = "caution"
        result["label_notes"].append({"ingredient": None, "message": "This product name matches a recall notice. "
                                      "Recalls cover specific batches, so compare the batch number on your pack."})
    result["disclaimer"] = DISCLAIMER
    return result
