import re
from .clean import clean_text

CODE_RE = re.compile(r"\b(?:ins|e)\s*[-:]?\s*(\d{3,4})\s*([a-z])?(?:\s*\(\s*[ivx]+\s*\))?", re.I)
BARE_CODE_RE = re.compile(r"(?<![\d.%])\b(\d{3,4})\s*([a-z])?\b(?:\s*\(\s*[ivx]+\s*\))?(?!\s*(?:%|mg|g\b|ml|kg|kcal|kj))", re.I)
PREFIX_RE = re.compile(r"^\s*(ingredients?|contains)\s*[:\-]\s*", re.I)
PERCENT_RE = re.compile(r"\(?\s*\d+(\.\d+)?\s*%\s*\)?")
NAME_STRIP_RE = re.compile(r"[^a-z0-9' ]+")
CLASS_WORDS = {"colour", "color", "colours", "colors", "preservative", "preservatives", "emulsifier", "emulsifiers",
               "stabilizer", "stabiliser", "stabilizers", "thickener", "thickening agent", "acidity regulator",
               "acidity regulators", "antioxidant", "antioxidants", "raising agent", "raising agents", "flavour enhancer",
               "flavor enhancer", "sweetener", "sweeteners", "artificial sweetener", "humectant", "anticaking agent",
               "flour treatment agent", "improver", "leavening agent", "permitted synthetic food colour",
               "permitted natural colour", "synthetic food colour", "added colour", "food colour"}
GENERIC_PATTERNS = [
    (re.compile(r"(permitted|added|artificial)\s+(synthetic\s+)?(food\s+)?colou?rs?", re.I),
     "The label names a colour without saying which one."),
    (re.compile(r"artificial\s+sweeteners?", re.I), "The label names an artificial sweetener without saying which one."),
    (re.compile(r"preservatives?\s*$", re.I), "The label names a preservative without saying which one."),
]

def normalize_code(number, letter=None):
    return f"{int(number)}{(letter or '').lower()}"

def split_top_level(text):
    parts, depth, buf = [], 0, []
    for ch in text:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        if ch in ",;" and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return [p.strip(" .\n\t") for p in parts if p.strip(" .\n\t")]

def normalize_ingredient(name):
    s = (clean_text(name) or "").lower().replace("&", " and ")
    s = PERCENT_RE.sub(" ", s)
    s = NAME_STRIP_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()

def extract_codes(text):
    codes = []
    for m in CODE_RE.finditer(text):
        codes.append(normalize_code(m.group(1), m.group(2)))
    for inner in re.findall(r"[(\[]([^()\[\]]*(?:\([^()]*\)[^()\[\]]*)*)[)\]]", text):
        for m in BARE_CODE_RE.finditer(CODE_RE.sub(" ", inner)):
            number = int(m.group(1))
            if 100 <= number <= 1600:
                codes.append(normalize_code(m.group(1), m.group(2)))
    seen = set()
    return [c for c in codes if not (c in seen or seen.add(c))]

def parse(text):
    text = PREFIX_RE.sub("", clean_text(text) or "")
    items = []
    for part in split_top_level(text):
        part = re.sub(r"^contains\s+", "", part, flags=re.I)
        head = re.split(r"[(\[]", part, maxsplit=1)[0]
        inner = part[len(head):].strip()
        inner = inner[1:-1] if inner[:1] in "([" and inner[-1:] in ")]" else inner
        names = [normalize_ingredient(head)]
        for sub in split_top_level(inner):
            n = normalize_ingredient(CODE_RE.sub(" ", sub))
            if n and not re.fullmatch(r"[\divx ]+", n):
                names.append(n)
        names = [n for n in names if n]
        notes = [msg for rx, msg in GENERIC_PATTERNS if rx.search(part) and not extract_codes(part)]
        items.append({"raw": part, "name": names[0] if names else "", "sub_names": names[1:],
                      "codes": extract_codes(part), "is_class": bool(names) and names[0] in CLASS_WORDS,
                      "notes": notes})
    return items
