import json
import re
import urllib.error
import urllib.request
from . import __version__

API = "https://world.openfoodfacts.org/api/v2/product/{barcode}.json"
FIELDS = "code,product_name,brands,categories_tags,ingredients_text,ingredients_text_en,additives_tags,image_front_url"
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
    p = payload["product"]
    return {
        "barcode": str(p.get("code") or payload.get("code") or ""),
        "product_name": p.get("product_name") or None,
        "brand": (p.get("brands") or "").split(",")[0].strip() or None,
        "ingredients_text": p.get("ingredients_text_en") or p.get("ingredients_text") or None,
        "additive_codes": additive_codes(p.get("additives_tags")),
        "category": guess_category(p.get("categories_tags")),
        "image_url": p.get("image_front_url"),
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
