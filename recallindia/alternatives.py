from .checker import find_recalls
from .config import AUTO_PUBLISH_SCORE
 
def flagged_substances(check_result):
    return {f["substance_id"]: (f["substance"], f.get("ins")) for f in check_result["findings"] if f["severity"] > 0}
 
def _label(name, ins):
    return f"{name} (INS {ins})" if ins else name
 
def find_alternatives(rulebook, product, check_result, search, recall_lookup=None, limit=4, category_tags=None):
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
 
_TYPE_WORDS = {
    "spice": "masala spice seasoning chilli chili mirch turmeric haldi jeera cumin coriander dhania hing asafoetida "
             "pepper garam chaat kasuri methi",
    "beverage": "juice drink beverage nectar squash sharbat sherbet soda cola lemonade smoothie lassi chaas "
                "buttermilk shake milkshake",
    "tea": "tea chai",
    "coffee": "coffee",
    "snack": "chips crisps namkeen bhujia sev mixture wafers puffs nachos popcorn peanuts makhana",
    "noodles": "noodles pasta macaroni vermicelli spaghetti",
    "biscuit": "biscuit cookie rusk cake crackers",
    "sauce": "sauce ketchup chutney dip mayonnaise",
    "pickle": "pickle achar",
    "oil": "oil",
    "dairy": "milk curd dahi paneer cheese butter yogurt yoghurt ghee cream",
    "flour": "atta flour maida besan suji sooji rava",
    "grain": "rice dal poha oats",
    "sweet": "sweet mithai chocolate candy toffee halwa ladoo",
    "sugar": "sugar jaggery gur",
    "salt": "salt",
    "honey": "honey",
    "jam": "jam spread",
}
_TYPE_OF = {w: t for t, words in _TYPE_WORDS.items() for w in words.split()}
_TAG_TYPES = {
    "beverage": ("beverages", "juices", "drinks", "nectars", "sodas"),
    "tea": ("teas",),
    "coffee": ("coffees",),
    "spice": ("spices", "spice", "seasonings", "masala", "masalas"),
    "snack": ("snacks", "chips", "crisps"),
    "noodles": ("noodles", "pastas"),
    "biscuit": ("biscuits", "cookies", "cakes"),
    "sauce": ("sauces", "ketchup", "chutneys"),
    "pickle": ("pickles",),
    "oil": ("oils",),
    "dairy": ("dairies", "milks", "cheeses"),
    "flour": ("flours",),
    "sweet": ("confectioneries", "chocolates", "sweets"),
}
_TAG_SKIP = ("foods-and-beverages", "plant-based-foods")
 
def _word_type(w):
    if w in _TYPE_OF:
        return _TYPE_OF[w]
    if w.endswith("es") and w[:-2] in _TYPE_OF:
        return _TYPE_OF[w[:-2]]
    if w.endswith("s") and w[:-1] in _TYPE_OF:
        return _TYPE_OF[w[:-1]]
    return None
 
def product_type(words):
    """What the product is, from its name: the last word that names a kind of product.
 
    Indian pack names end with the product ("Masala Mixed Fruit Juice" is a juice, "Mixed Masala Powder" a spice
    mix), so the words are read from the end. "Powder" and "mix" are skipped as they don't say what the product is.
    """
    for w in reversed(words):
        t = _word_type(w)
        if t:
            return t
    return None
 
def tag_types(tags):
    """Kinds of product named by Open Food Facts category tags, ignoring the top-level catch-all tags."""
    out = set()
    for tag in tags or []:
        tag = str(tag).lower()
        if any(skip in tag for skip in _TAG_SKIP):
            continue
        parts = tag.split(":", 1)[-1].split("-")
        for kind, keys in _TAG_TYPES.items():
            if any(k in parts for k in keys):
                out.add(kind)
    return out
 
def same_kind(query_type, cand):
    """The candidate is the same kind of product as the search (a spice mix for a spice mix, not a juice).
 
    The candidate's own name decides when it names a kind; otherwise its categories. When the search's kind is
    unknown, any candidate passes. When the candidate's kind is unknown, it is left out, so a vague product is
    never offered in place of a known one.
    """
    if not query_type:
        return True
    name_type = product_type(_words(cand.get("product_name")))
    if name_type:
        return name_type == query_type
    return query_type in tag_types(cand.get("categories_tags"))
 
def _rank(cand, query_words, q):
    """Higher for candidates that share more of the search words, and most for the exact phrase in order."""
    name_words = _words(cand.get("product_name"))
    shared = len(set(query_words) & set(name_words)) / max(len(query_words), 1)
    phrase = 1.0 if q and q in " ".join(name_words) else 0.0
    return phrase + shared
 
def _dedupe_key(cand):
    brand_words = set(_words(cand.get("brand")))
    return (frozenset(brand_words), frozenset(w for w in _words(cand.get("product_name")) if w not in brand_words))
 
def find_recall_alternatives(recall, search, recall_lookup=None, query=None, limit=4, min_score=None):
    from .config import REVIEW_MIN_SCORE
    floor = REVIEW_MIN_SCORE if min_score is None else min_score
    q = recall_query(recall, query)
    if not q:
        return {"query": q, "reason": "no_query", "items": []}
    query_words = q.split()
    query_type = product_type(query_words)
    recalled_brand = (recall.get("brand") or "").split(" - ", 1)[0]
    passed, seen, seen_products = [], set(), set()
    for i, cand in enumerate(search(q)):
        code = str(cand.get("barcode") or "")
        name = cand.get("product_name")
        if not code or not name or code in seen:
            continue
        seen.add(code)
        if not cand.get("brand") or _same_brand(cand["brand"], recalled_brand) or not _relevant(cand, query_words):
            continue
        if not same_kind(query_type, cand):
            continue
        key = _dedupe_key(cand)
        if key in seen_products:
            continue
        seen_products.add(key)
        passed.append((-_rank(cand, query_words, q), i, cand))
    out = []
    for _, _, cand in sorted(passed, key=lambda x: (x[0], x[1])):
        if find_recalls(recall_lookup, cand["product_name"], cand["brand"], threshold=floor):
            continue
        out.append({"barcode": str(cand["barcode"]), "product_name": cand["product_name"], "brand": cand["brand"],
                    "image_url": cand.get("image_url"), "notes": ["No FSSAI recall on record", "Different brand"]})
        if len(out) >= limit:
            break
    return {"query": q, "reason": None if out else "none_found", "items": out}
 
