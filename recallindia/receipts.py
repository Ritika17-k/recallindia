"""Read purchases from a photo or scan of a shop receipt.

OCR uses Tesseract through pytesseract. Install the Tesseract program itself (on Windows, the UB Mannheim
installer) and set TESSERACT_CMD in .env if it is not on PATH. The parser works on plain text too, so receipts that
are already text (for example a PDF invoice copied out) can skip OCR.
"""
import re
from pathlib import Path

from dateutil import parser as dateparser

from .intake import is_meta, looks_like_product, to_purchases

DATE_RES = [
    re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})\b"),
    re.compile(r"\b(\d{1,2})[\s\-]([A-Za-z]{3,9})[\s\-,]+(\d{2,4})\b"),
    re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),
]
HEADER_RE = re.compile(r"\b(item|items|particulars|description|product)\b.*\b(qty|quantity|rate|amt|amount|price)\b",
                       re.I)
END_RE = re.compile(r"\b(sub\s?total|total|net\s?(amt|amount|payable)|grand\s?total|bill\s?amount|"
                    r"no\.?\s*of\s*items|total\s*items)\b", re.I)
NUMBER_TOKEN_RE = re.compile(r"^[\d,]+(?:[.:]\d{1,3})?$")
LEADING_CODE_RE = re.compile(r"^(?:\d{1,3}[.)]?|\d{4,14})$")


def ocr_image(path, tesseract_cmd=None):
    """Text from a receipt image. Upscales small photos and boosts contrast before OCR."""
    try:
        import pytesseract
        from PIL import Image, ImageOps
    except ImportError as e:
        raise RuntimeError("Receipt OCR needs: pip install pytesseract pillow, plus the Tesseract program") from e
    from .config import TESSERACT_CMD
    if tesseract_cmd or TESSERACT_CMD:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd or TESSERACT_CMD
    img = Image.open(path)
    img = ImageOps.exif_transpose(img).convert("L")
    if img.width < 1200:
        scale = 1200 / img.width
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    img = ImageOps.autocontrast(img)
    return pytesseract.image_to_string(img, config="--psm 6")


def find_date(text):
    for rx in DATE_RES:
        for m in rx.finditer(text):
            try:
                d = dateparser.parse(m.group(0), dayfirst=not m.group(0)[:4].isdigit(), fuzzy=False).date()
            except (ValueError, OverflowError):
                continue
            if 2000 <= d.year <= 2100:
                return d
    return None


def parse_item_line(line):
    """Split 'HSN NAME PACK QTY RATE AMOUNT' into name, quantity and amount. Returns None for non-item lines."""
    tokens = line.replace("\t", " ").split()
    trailing = []
    while tokens and NUMBER_TOKEN_RE.match(tokens[-1]):
        trailing.insert(0, tokens.pop())
    while tokens and LEADING_CODE_RE.match(tokens[0]):
        tokens.pop(0)
    name = " ".join(tokens).strip(" -:*|.")
    if not looks_like_product(name) or is_meta(name):
        return None
    qty = 1
    if len(trailing) >= 2:
        try:
            q = float(trailing[0].replace(",", ""))
            qty = int(q) if q.is_integer() and 0 < q < 100 else 1
        except ValueError:
            pass
    amount = None
    if trailing:
        try:
            amount = float(trailing[-1].replace(",", "").replace(":", "."))
        except ValueError:
            pass
    return {"product_name": name, "quantity": qty, "amount": amount, "has_price": bool(trailing)}


def parse_receipt_text(text):
    """Store name, date and product lines from receipt text."""
    lines = [re.sub(r"\s+", " ", l).strip() for l in text.splitlines()]
    lines = [l for l in lines if l and sum(ch.isalnum() for ch in l) >= 2]
    store = next((l for l in lines[:5] if looks_like_product(l) and not any(rx.search(l) for rx in DATE_RES)), None)
    start = next((i + 1 for i, l in enumerate(lines) if HEADER_RE.search(l)), None)
    end = next((i for i, l in enumerate(lines) if i >= (start or 0) and END_RE.search(l)), len(lines))
    body = lines[start:end] if start is not None else lines[:end]
    items = []
    for l in body:
        if l == store or any(rx.search(l) for rx in DATE_RES):
            continue
        item = parse_item_line(l)
        # Without a header row, only trust lines that end in a price
        if item and (start is not None or item["has_price"]):
            item.pop("has_price")
            items.append(item)
    return {"store": store, "purchase_date": find_date(text), "items": items}


def import_receipt(path_or_text, user_id, is_text=False, store=None):
    """Parse one receipt into (purchases DataFrame, parsed receipt)."""
    import hashlib
    if is_text:
        text, key = path_or_text, hashlib.sha1(path_or_text.encode()).hexdigest()
    else:
        p = Path(path_or_text)
        key = hashlib.sha1(p.read_bytes()).hexdigest()
        text = p.read_text(encoding="utf-8", errors="replace") if p.suffix.lower() == ".txt" else ocr_image(p)
    parsed = parse_receipt_text(text)
    parsed["text"] = text
    purchases = to_purchases(parsed["items"], user_id, "receipt", store=store or parsed["store"],
                             purchase_date=parsed["purchase_date"], key=key)
    return purchases, parsed
