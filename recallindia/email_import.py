"""Import purchases from order-confirmation emails.

Two ways in:
- A Google Takeout export of Gmail (an .mbox file), a folder of .eml files, or a single .eml. Needs nothing extra.
- The Gmail API (read-only), when google-api-python-client and google-auth-oauthlib are installed and a
  credentials.json from Google Cloud is present. See README.

Order emails differ by retailer and change often, so the item finder uses general patterns rather than one
template per shop: "2 x Item ₹54", table rows with a quantity or price cell, and an item line followed by a
"Qty: 1" line. Check imported lines with `import-email --dry-run` before saving them.
"""
import mailbox
import re
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

from .intake import is_meta, looks_like_product, split_trackable, to_purchases

RETAILERS = {
    "amazon.in": "Amazon", "amazon.com": "Amazon", "flipkart.com": "Flipkart", "bigbasket.com": "BigBasket",
    "bbdaily.com": "BigBasket", "blinkit.com": "Blinkit", "grofers.com": "Blinkit", "zeptonow.com": "Zepto",
    "zepto.co": "Zepto", "swiggy.in": "Swiggy Instamart", "swiggy.com": "Swiggy Instamart",
    "jiomart.com": "JioMart", "dmart.in": "DMart Ready", "dmartready.com": "DMart Ready",
    "naturesbasket.co.in": "Nature's Basket", "spencers.in": "Spencer's", "starquik.com": "StarQuik",
    "licious.in": "Licious", "milkbasket.com": "Milkbasket", "country-delight.in": "Country Delight",
    "countrydelight.in": "Country Delight", "tatacliq.com": "Tata CLiQ", "1mg.com": "Tata 1mg",
}
ORDER_SUBJECT_RE = re.compile(r"\b(order|ordered|delivered|delivery|invoice|receipt|purchase|shipped|bill)\b", re.I)
PROMO_SUBJECT_RE = re.compile(r"(\d+\s?%|\boff\b|\bsale\b|\bdeal|\bcashback\b|\bcoupon\b|\bwin\b|\bfree\b)", re.I)
STRONG_ORDER_RE = re.compile(r"\b(delivered|invoice|receipt|shipped|dispatched)\b|"
                             r"\border\s*(#|no\b|id\b|number|summary|confirm|placed|is\b|has\b|was\b)", re.I)
GMAIL_QUERY = ("from:(" + " OR ".join(sorted(RETAILERS)) + ") "
               "subject:(order OR delivered OR invoice OR receipt) newer_than:2y")

PRICE = r"(?:₹|rs\.?|inr)\s*[\d,]+(?:\.\d{1,2})?"
PRICE_RE = re.compile(PRICE, re.I)
QTY_LINE_RE = re.compile(r"^\s*(?:qty|quantity)\s*[:\-]?\s*(\d+)\b", re.I)
QTY_CELL_RE = re.compile(r"^\s*(?:(?:qty|quantity)\s*[:\-]?\s*)?(\d{1,3})\s*(?:nos?|pcs?|units?)?\s*$", re.I)
TIMES_PREFIX_RE = re.compile(r"^\s*(\d{1,3})\s*[x×]\s+(.+)$", re.I)
TIMES_SUFFIX_RE = re.compile(r"^(.+?)\s+[x×]\s*(\d{1,3})\s*$", re.I)
TRAILING_PRICE_RE = re.compile(r"\s*(?:" + PRICE + r"|[\d,]+\.\d{2})\s*$", re.I)
NOISE_RE = re.compile(r"\s*\|\s*$")


class _TextExtractor(HTMLParser):
    """HTML to lines: table cells separated by tabs, block elements end a line."""
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section", "article"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script", "head"):
            self.skip += 1
        elif tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head"):
            self.skip = max(0, self.skip - 1)
        elif tag in ("td", "th"):
            self.parts.append("\t")
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(re.sub(r"\s+", " ", data))


def html_to_lines(html):
    p = _TextExtractor()
    p.feed(html)
    lines = []
    for raw in "".join(p.parts).split("\n"):
        cells = [c.strip() for c in raw.split("\t")]
        cells = [c for c in cells if c]
        if cells:
            lines.append("\t".join(cells))
    return lines


def text_to_lines(text):
    return [re.sub(r"[  ]{2,}", "\t", l).strip() for l in text.splitlines() if l.strip()]


def _clean_name(name):
    name = TRAILING_PRICE_RE.sub("", name).strip(" -:|•*\t")
    return re.sub(r"\s{2,}", " ", name)


def find_items(lines):
    """Product lines in an order email, as dicts with product_name and quantity."""
    items, seen = [], set()

    def add(name, qty=1):
        name = _clean_name(name)
        key = name.lower()
        if looks_like_product(name) and key not in seen:
            seen.add(key)
            items.append({"product_name": name, "quantity": int(qty)})

    for i, line in enumerate(lines):
        cells = line.split("\t")
        flat = " ".join(cells)
        # "2 x Amul Taaza Toned Milk 500 ml  ₹54" or "Amul Taaza Toned Milk x 2"
        m = TIMES_PREFIX_RE.match(flat)
        if m and not is_meta(m.group(2)):
            add(m.group(2), m.group(1))
            continue
        m = TIMES_SUFFIX_RE.match(TRAILING_PRICE_RE.sub("", flat))
        if m and not is_meta(m.group(1)):
            add(m.group(1), m.group(2))
            continue
        # Table row: a name cell plus a quantity or price cell
        if len(cells) >= 2:
            name_cells = [c for c in cells if looks_like_product(c) and not PRICE_RE.fullmatch(c.strip())]
            qty_cells = [c for c in cells if QTY_CELL_RE.match(c)]
            price_cells = [c for c in cells if PRICE_RE.search(c) or re.fullmatch(r"[\d,]+\.\d{2}", c.strip())]
            if name_cells and (qty_cells or price_cells):
                qty = QTY_CELL_RE.match(qty_cells[0]).group(1) if qty_cells else 1
                add(name_cells[0], qty)
                continue
        # An item line followed within two lines by "Qty: 1"
        for j in (i + 1, i + 2):
            if j < len(lines) and QTY_LINE_RE.match(lines[j]):
                if looks_like_product(_clean_name(flat)) and not QTY_LINE_RE.match(flat):
                    add(flat, QTY_LINE_RE.match(lines[j]).group(1))
                break
    return items


def _body_lines(msg):
    html = plain = None
    for part in msg.walk() if msg.is_multipart() else [msg]:
        ctype = part.get_content_type()
        if part.get_content_disposition() == "attachment":
            continue
        try:
            content = part.get_content()
        except (LookupError, KeyError, AttributeError):
            payload = part.get_payload(decode=True) or b""
            content = payload.decode("utf-8", "replace")
        if ctype == "text/html" and html is None:
            html = content
        elif ctype == "text/plain" and plain is None:
            plain = content
    # HTML keeps table structure, which carries most of the item information
    return html_to_lines(html) if html else text_to_lines(plain or "")


def retailer_for(sender):
    domain = parseaddr(sender or "")[1].lower().rsplit("@", 1)[-1]
    for known, name in RETAILERS.items():
        if domain == known or domain.endswith("." + known):
            return name
    return None


def is_order_email(msg, retailer):
    subject = str(msg.get("Subject", ""))
    if not ORDER_SUBJECT_RE.search(subject):
        return False
    if PROMO_SUBJECT_RE.search(subject) and not STRONG_ORDER_RE.search(subject):
        return False
    return retailer is not None


def parse_order_email(msg, require_known_retailer=True):
    """Return {retailer, order_date, message_id, subject, items} or None if this isn't an order email."""
    retailer = retailer_for(msg.get("From"))
    if require_known_retailer and not is_order_email(msg, retailer):
        return None
    try:
        sent = parsedate_to_datetime(msg.get("Date")) if msg.get("Date") else None
    except (TypeError, ValueError):
        sent = None
    items, not_tracked = split_trackable(find_items(_body_lines(msg)))
    if not items and not not_tracked:
        return None
    return {"retailer": retailer or parseaddr(msg.get("From", ""))[1], "order_date": sent.date() if sent else None,
            "message_id": str(msg.get("Message-ID", "")) or str(msg.get("Subject", "")), "items": items,
            "not_tracked": not_tracked,
            "subject": str(msg.get("Subject", ""))}


def read_message(data):
    return BytesParser(policy=policy.default).parsebytes(data)


def iter_messages(path):
    """Messages from an .mbox file, an .eml file, or a folder of .eml files."""
    path = Path(path)
    if path.is_dir():
        for f in sorted(path.glob("*.eml")):
            yield read_message(f.read_bytes())
    elif path.suffix.lower() == ".eml":
        yield read_message(path.read_bytes())
    else:
        for m in mailbox.mbox(str(path), factory=None, create=False):
            yield read_message(m.as_bytes())


def gmail_api_messages(query=GMAIL_QUERY, max_results=200, credentials=None, token=None):
    """Raw messages from Gmail through the API, read-only. Opens a browser window the first time."""
    import base64

    from .config import GMAIL_CREDENTIALS, GMAIL_TOKEN
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as e:
        raise RuntimeError("Gmail API import needs: pip install -r requirements-intake.txt") from e
    scopes = ["https://www.googleapis.com/auth/gmail.readonly"]
    token, credentials = Path(token or GMAIL_TOKEN), Path(credentials or GMAIL_CREDENTIALS)
    creds = Credentials.from_authorized_user_file(str(token), scopes) if token.exists() else None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not credentials.exists():
                raise RuntimeError(f"Put your OAuth client file at {credentials} (see README)")
            creds = InstalledAppFlow.from_client_secrets_file(str(credentials), scopes).run_local_server(port=0)
        token.write_text(creds.to_json())
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    fetched, page = 0, None
    while fetched < max_results:
        resp = service.users().messages().list(userId="me", q=query, pageToken=page,
                                               maxResults=min(100, max_results - fetched)).execute()
        for ref in resp.get("messages", []):
            raw = service.users().messages().get(userId="me", id=ref["id"], format="raw").execute()["raw"]
            yield read_message(base64.urlsafe_b64decode(raw))
            fetched += 1
        page = resp.get("nextPageToken")
        if not page:
            break


def import_messages(messages, user_id, require_known_retailer=True):
    """Parse messages into (purchases DataFrame, list of parsed orders)."""
    import pandas as pd
    frames, orders = [], []
    for msg in messages:
        order = parse_order_email(msg, require_known_retailer)
        if not order:
            continue
        orders.append(order)
        frames.append(to_purchases(order["items"], user_id, "gmail", store=order["retailer"],
                                   purchase_date=order["order_date"], key=order["message_id"]))
    if not frames:
        return to_purchases([], user_id, "gmail"), orders
    return pd.concat(frames, ignore_index=True).drop_duplicates("purchase_id"), orders
