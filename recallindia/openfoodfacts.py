import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from . import __version__

API = "https://world.openfoodfacts.org/api/v2/product/{barcode}.json"
SEARCH_API = "https://world.openfoodfacts.org/api/v2/search"
FIELDS = "code,product_name,brands,categories_tags,ingredients_text,ingredients_text_en,additives_tags,image_front_url"
SEARCH_FIELDS = FIELDS + ",image_front_small_url,countries_tags"
SEARCH_CACHE_SECONDS = 24 * 3600  # Open Food Facts allows 10 searches a minute per IP, so results are reused
_search_cache = {}
USER_AGENT = f"RecallIndia/{__version__} (github.com/Ritika17-k)"

CATEGORY_MAP = [
    ("13.1", ["infant-formula", "infant-milk"]),
    ("13.2", ["baby-foods", "baby-cereals", "foods-for-babies"]),
    ("3", ["ice-creams", "frozen-desserts", "sorbets"]),
    ("1", ["dairies", "milks", "cheeses", "yogurts", "paneer", "butters", "dairy-desserts"]),
    ("2", ["fats", "vegetable-oils", "oils", "margarines", "ghee"]),
    ("7", ["biscuits", "cookies", "breads", "cakes", "pastries", "rusks"]),
    ("5", ["confectioneries", "chocolates", "candies", "sweets", "chewing-gum", "indian-sweets"]),
    ("15", ["salty-snacks", "chips", "crisps", "namkeen", "savory-snacks", "snacks"]),
    ("6", ["cereals", "breakfast-cereals", "noodles", "pastas", "rices", "flours", "instant-noodles"]),
    ("8", ["meats", "sausages", "hams"]),
    ("9", ["fishes", "seafood"]),
    ("10", ["eggs"]),
    ("11", ["sugars", "honeys", "sweeteners", "jaggery"]),
    ("12", ["spices", "condiments", "sauces", "ketchups", "soups", "salts", "pickles", "masalas"]),
    ("4", ["fruits", "vegetables", "jams", "nuts", "dried-fruits", "seeds"]),
    ("14", ["beverages", "drinks", "waters", "juices", "sodas", "teas", "coffees"]),
]

class ProductLookupError(Exception):
    pass

def guess_category(categories_tags):
    tags = [t.split(":", 1)[-1] for t in categories_tags or []]
    for code, keys in CATEGORY_MAP:
        if any(k == t or t.endswith(k) or k in t for t in tags for k in keys):
            return code
    return None

def additive_codes(additives_tags):
    out = []
    for tag in additives_tags or []:
        m = re.fullmatch(r"(?:\w+:)?e(\d{3,4})([a-h]?)[ivx]*", tag.lower())
        if m:
            out.append(f"{int(m.group(1))}{m.group(2)}")
    return out

def parse_product(payload):
    if not payload or payload.get("status") != 1 or not payload.get("product"):
        return None
    return product_from(payload["product"], payload.get("code"))

def product_from(p, code=None):
    return {
        "barcode": str(p.get("code") or code or ""),
        "product_name": p.get("product_name") or None,
        "brand": (p.get("brands") or "").split(",")[0].strip() or None,
        "ingredients_text": p.get("ingredients_text_en") or p.get("ingredients_text") or None,
        "additive_codes": additive_codes(p.get("additives_tags")),
        "category": guess_category(p.get("categories_tags")),
        "image_url": p.get("image_front_small_url") or p.get("image_front_url"),
        "categories_tags": list(p.get("categories_tags") or []),
        "source": "Open Food Facts",
    }

def fetch(barcode, timeout=15):
    if not re.fullmatch(r"\d{8,14}", str(barcode)):
        raise ValueError("Barcode must be 8 to 14 digits")
    req = urllib.request.Request(API.format(barcode=barcode) + f"?fields={FIELDS}", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return parse_product(json.loads(resp.read().decode("utf-8")))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise ProductLookupError(f"Open Food Facts returned {e.code}") from e
    except urllib.error.URLError as e:
        raise ProductLookupError(f"Could not reach Open Food Facts: {e.reason}") from e


def search_category(tag, country="en:india", page_size=50, timeout=20):
    """Products in one Open Food Facts category sold in `country`, most scanned first. Cached for a day."""
    key = (tag, country)
    hit = _search_cache.get(key)
    if hit and time.time() - hit[0] < SEARCH_CACHE_SECONDS:
        return hit[1]
    params = urllib.parse.urlencode({"categories_tags": tag, "countries_tags": country, "fields": SEARCH_FIELDS,
                                     "page_size": page_size, "sort_by": "unique_scans_n"})
    req = urllib.request.Request(f"{SEARCH_API}?{params}", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise ProductLookupError(f"Open Food Facts search returned {e.code}") from e
    except urllib.error.URLError as e:
        raise ProductLookupError(f"Could not reach Open Food Facts: {e.reason}") from e
    products = [product_from(p) for p in (data.get("products") or []) if isinstance(p, dict)]
    _search_cache[key] = (time.time(), products)
    return products
