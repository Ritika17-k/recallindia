"""Shared helpers for purchase intake (order emails, receipts, manual entry)."""
import hashlib
import re

import pandas as pd

from .clean import clean_purchases

PURCHASE_COLUMNS = ["purchase_id", "user_id", "product_name", "brand", "batch_no", "store", "purchase_date", "source"]

# Lines that are totals, charges or page furniture rather than products. Checked as whole words.
META_WORDS = [
    "total", "subtotal", "sub total", "grand total", "net amount", "net amt", "amount", "amt", "bill", "invoice",
    "delivery", "shipping", "handling", "fee", "fees", "charge", "charges", "discount", "savings", "saved", "you saved",
    "coupon", "promo", "offer", "cashback", "wallet", "refund", "tax", "taxes", "gst", "cgst", "sgst", "igst", "cess",
    "vat", "tip", "packaging", "payment", "paid", "cash", "change", "card", "upi", "balance", "round off", "rounding",
    "order id", "order no", "order number", "order summary", "order total", "items", "item total", "item(s)",
    "qty", "quantity", "mrp", "rate", "price", "hsn", "gstin", "fssai", "phone", "mobile", "tel", "email",
    "address", "deliver to", "delivered to", "shipped to", "ship to", "billing", "thank", "thanks", "visit again",
    "track", "view", "help", "support", "rate us", "return", "cancel", "platform", "convenience", "small cart",
    "seller", "sold by", "invoice no", "bill no", "cashier", "counter", "customer", "member",
    "points", "welcome", "dear", "hello", "unsubscribe", "privacy", "terms", "download", "app",
]
META_RE = re.compile(r"\b(" + "|".join(re.escape(w) for w in sorted(META_WORDS, key=len, reverse=True)) + r")\b",
                     re.I)
LETTERS_RE = re.compile(r"[A-Za-z]")


def is_meta(line):
    return bool(META_RE.search(line))


def looks_like_product(name):
    """Enough letters to be a product name and not just a code or a number."""
    name = name.strip()
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'&.-]*", name) if len(w) >= 2]
    return len(LETTERS_RE.findall(name)) >= 4 and len(words) >= 1 and not is_meta(name)


def stable_id(prefix, *parts):
    digest = hashlib.sha1("\x1f".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:12].upper()
    return f"{prefix}-{digest}"


def to_purchases(items, user_id, source, store=None, purchase_date=None, key=""):
    """Turn parsed line items into cleaned purchase rows ready for db.save_purchases or matching.

    `key` identifies the email or receipt, so importing the same one twice gives the same purchase IDs.
    """
    prefix = {"gmail": "GM", "email": "GM", "receipt": "RC", "manual": "MN"}.get(source, "PU")
    def iso(d):
        return d.isoformat() if hasattr(d, "isoformat") else d

    rows = []
    for i, item in enumerate(items):
        item = item if isinstance(item, dict) else {"product_name": str(item)}
        name = item["product_name"]
        rows.append({
            "purchase_id": stable_id(prefix, user_id, key, i, name),
            "user_id": user_id,
            "product_name": name,
            "brand": item.get("brand"),
            "batch_no": item.get("batch_no"),
            "store": item.get("store") or store,
            "purchase_date": iso(item.get("purchase_date") or purchase_date),
            "source": source,
        })
    if not rows:
        return pd.DataFrame(columns=PURCHASE_COLUMNS + ["product_norm", "brand_norm"])
    return clean_purchases(pd.DataFrame(rows, columns=PURCHASE_COLUMNS))
