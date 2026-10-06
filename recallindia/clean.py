import re
from datetime import date, datetime
import pandas as pd
from dateutil import parser as dateparser
from .config import HEADER_ALIASES, NULL_TOKENS, RECALL_COLUMNS

DATE_COLUMNS = ["recall_date", "termination_date", "manufacture_date", "expiry_date"]
UNIT_RE = re.compile(r"\b\d+(\.\d+)?\s?(g|gm|gms|kg|ml|l|ltr|litre|liter|pcs|pc|pack|pkt)\b")
NON_ALNUM_RE = re.compile(r"[^a-z0-9 ]+")
SPACE_RE = re.compile(r"\s+")
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")

def _missing(v):
    try:
        return v is None or bool(pd.isna(v))
    except (TypeError, ValueError):
        return False

def _none_for_missing(df):
    df = df.astype(object)
    for col in df.columns:
        df[col] = pd.Series([None if _missing(v) else v for v in df[col]], index=df.index, dtype=object)
    return df

_ALIASES_BY_LENGTH = sorted(((a, col) for col, aliases in HEADER_ALIASES.items() for a in aliases + [col.replace("_", " ")]),
                            key=lambda x: -len(x[0]))
REASON_TYPE_RE = re.compile(r"^\s*(unsafe|misbranded|sub[\s-]?standard)\s*[-:]\s*(.*)$", re.I | re.S)

def canonical_header(header):
    h = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", str(header or "").strip())
    h = SPACE_RE.sub(" ", h.lower().replace("_", " "))
    h = re.sub(r"\[.*?\]", "", h)
    h = re.sub(r"[.:*#]", "", h).strip()
    for alias, col in _ALIASES_BY_LENGTH:
        if h == alias:
            return col
    for alias, col in _ALIASES_BY_LENGTH:
        if len(alias) > 3 and alias in h:
            return col
    return None

TYPE_WORDS = [("Misbranded", re.compile(r"misbrand", re.I)),
              ("Sub-standard", re.compile(r"sub[\s-]?standard", re.I)),
              ("Unsafe", re.compile(r"\bunsafe\b", re.I))]
HAZARDS = [
    ("pesticide residue", r"pesticide|insecticide|residue|\bmrl\b|chlorpyrifos|ethion|carbendazim|tebuconazole|"
                          r"thiamethoxam|fipronil|ferbam|azoxystrobin"),
    ("synthetic colour", r"tartrazine|synthetic (food )?colou?r|colouring matter|sunset yellow|brilliant blue|"
                         r"unsafe colou?r|rhodamine|metanil|permit+ed colou?r|added colou?ring|colou?ring"),
    ("chemical contaminant", r"metal|\blead\b|arsenic|mercury|cadmium|contaminant"),
    ("preservative", r"sorbate|benzoate|preservative|sulphite|sulfite"),
    ("mycotoxin", r"aflatoxin|mycotoxin"),
    ("insects or foreign matter", r"insect|larva|worm|foreign (matter|body)|fungus|fungal|mould|mold"),
    ("microbial", r"salmonella|e\.? ?coli|listeria|microb|bacteria|coliform|yeast"),
    ("labelling", r"misbrand|label|sugar[- ]free|claim|fssai number|printed"),
    ("quality standard", r"standard|moisture|refractometer|iodine value|fatty acid|protein|dissolved solids|"
                         r"sugar content|total sugars|rancid|b\.?r\.? reading|colou?r change"),
]
HAZARD_RES = [(name, re.compile(rx, re.I)) for name, rx in HAZARDS]
PLACEHOLDER_REASONS = {"ok", "other", "others", "recall", "unsafe", "substandard", "sub-standard", "misbranded",
                       "d", "test", "new plant", "nil"}

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)")

def redact(text):
    if _missing(text):
        return None
    return PHONE_RE.sub("[phone removed]", EMAIL_RE.sub("[email removed]", str(text)))

def infer_type(reason):
    if _missing(reason):
        return None
    for kind, rx in TYPE_WORDS:
        if rx.search(str(reason)):
            return kind
    return None

def tag_hazards(reason):
    if _missing(reason):
        return None
    tags = [name for name, rx in HAZARD_RES if rx.search(str(reason))]
    return "; ".join(tags) or None

def is_placeholder_reason(reason):
    if _missing(reason):
        return False
    text = re.sub(r"[^a-z\- ]", "", str(reason).lower()).strip()
    return len(text) <= 2 or text in PLACEHOLDER_REASONS

def split_reason(reason):
    if _missing(reason):
        return None, None
    reason = str(reason)
    m = REASON_TYPE_RE.match(reason)
    if not m:
        return None, reason
    kind = m.group(1).lower().replace(" ", "-")
    kind = "Sub-standard" if kind.startswith("sub") else kind.title()
    return kind, m.group(2).strip() or None

def map_columns(df):
    mapping, used = {}, set()
    for c in df.columns:
        target = canonical_header(c)
        if target and target not in used:
            mapping[c] = target
            used.add(target)
    out = df.rename(columns=mapping)[list(mapping.values())].copy()
    for col in RECALL_COLUMNS:
        if col not in out.columns:
            out[col] = None
    unmapped = [c for c in df.columns if c not in mapping]
    return out[RECALL_COLUMNS], unmapped

def clean_text(value):
    if _missing(value):
        return None
    s = SPACE_RE.sub(" ", str(value).replace(" ", " ")).strip()
    if s.lower() in NULL_TOKENS:
        return None
    return s

def parse_date(value):
    s = clean_text(value)
    if s is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.date() if isinstance(value, datetime) else value
    if ISO_RE.match(s):
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            return None
    try:
        return dateparser.parse(s, dayfirst=True, fuzzy=False).date()
    except (ValueError, OverflowError):
        return None

def normalize_name(value):
    s = clean_text(value)
    if s is None:
        return ""
    s = s.lower().replace("&", " and ")
    s = UNIT_RE.sub(" ", s)
    s = NON_ALNUM_RE.sub(" ", s)
    return SPACE_RE.sub(" ", s).strip()

def normalize_id(value):
    s = clean_text(value)
    return None if s is None else s.upper().replace(" ", "")

def _upper(v):
    return None if _missing(v) else str(v).upper()

def _title(v):
    return None if _missing(v) else str(v).title()

def _digits(v):
    return None if _missing(v) else (re.sub(r"\D", "", str(v)) or str(v))

def clean_recalls(raw):
    df, unmapped = map_columns(raw)
    raw_dates = {c: df[c].copy() for c in DATE_COLUMNS}
    for col in RECALL_COLUMNS:
        if col in DATE_COLUMNS:
            continue
        df[col] = df[col].map(clean_text)
    df["recall_id"] = df["recall_id"].map(normalize_id)
    df["batch_no"] = df["batch_no"].map(_upper)
    df["license_no"] = df["license_no"].map(_digits)
    split = df["reason"].map(split_reason)
    df["recall_type"] = [t if t else (old if not _missing(old) else None) for (t, _), old in zip(split, df["recall_type"])]
    df["reason"] = [redact(r) for _, r in split]
    df["recall_type"] = [t if not _missing(t) else infer_type(r) for t, r in zip(df["recall_type"], df["reason"])]
    df["hazard"] = df["reason"].map(tag_hazards)
    for col in ["status", "category", "license_type"]:
        df[col] = df[col].map(_title)
    bad_dates = []
    for col in DATE_COLUMNS:
        parsed = raw_dates[col].map(parse_date)
        for i, (orig, val) in enumerate(zip(raw_dates[col], parsed)):
            if val is None and clean_text(orig) is not None:
                bad_dates.append({"row": int(i), "column": col, "value": str(orig)})
        df[col] = parsed
    df["product_norm"] = df["product_name"].map(normalize_name)
    df["brand_norm"] = df["brand"].map(normalize_name)
    df["fbo_norm"] = df["fbo_name"].map(normalize_name)
    df["search_norm"] = (df["brand_norm"] + " " + df["product_norm"] + " " + df["fbo_norm"]).str.strip()
    before = len(df)
    df = df.sort_values("recall_date", na_position="first", kind="stable")
    with_id = df[df["recall_id"].notna()].drop_duplicates("recall_id", keep="last")
    without_id = df[df["recall_id"].isna()]
    df = pd.concat([with_id, without_id]).sort_index().reset_index(drop=True)
    df = _none_for_missing(df)
    report = {
        "rows_in": int(before),
        "rows_out": int(len(df)),
        "duplicates_removed": int(before - len(df)),
        "unmapped_columns": unmapped,
        "unparseable_dates": bad_dates,
    }
    return df, report

def clean_purchases(raw):
    df = raw.copy()
    df.columns = [SPACE_RE.sub("_", str(c).strip().lower()) for c in df.columns]
    for col in ["purchase_id", "product_name", "brand", "batch_no", "store", "purchase_date"]:
        if col not in df.columns:
            df[col] = None
    for col in ["purchase_id", "product_name", "brand", "batch_no", "store"]:
        df[col] = df[col].map(clean_text)
    df["batch_no"] = df["batch_no"].map(_upper)
    df["purchase_date"] = df["purchase_date"].map(parse_date)
    df["product_norm"] = df["product_name"].map(normalize_name)
    df["brand_norm"] = df["brand"].map(normalize_name)
    return _none_for_missing(df)
