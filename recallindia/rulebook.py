from pathlib import Path
import pandas as pd
from rapidfuzz import fuzz, process
from .config import REFERENCE_DIR
from .ingredients import normalize_ingredient, parse

SEVERITY = {"prohibited": 3, "not_permitted": 3, "limit": 2, "restricted": 1, "info": 1, "ok": 0}
VERDICT = {3: "avoid", 2: "caution", 1: "check", 0: "no issues found"}
FUZZY_CUTOFF = 92
MIN_FUZZY_LEN = 6

def _blank(v):
    return v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == ""

def category_applies(rule_category, product_category):
    if rule_category == "ALL":
        return True
    if not product_category:
        return False
    return product_category == rule_category or product_category.startswith(rule_category + ".")

class RuleBook:
    def __init__(self, substances, rules, categories):
        self.substances = substances.fillna("").set_index("substance_id", drop=False)
        self.rules = rules.fillna("")
        self.categories = dict(zip(categories["code"].astype(str), categories["name"]))
        self.by_code, self.by_alias = {}, {}
        for s in self.substances.to_dict("records"):
            if s["ins"]:
                self.by_code[str(s["ins"]).lower()] = s["substance_id"]
            for alias in [s["name"], *str(s["aliases"]).split("|")]:
                n = normalize_ingredient(alias)
                if n:
                    self.by_alias[n] = s["substance_id"]
        self._alias_keys = list(self.by_alias)

    @classmethod
    def from_csv(cls, directory=None):
        d = Path(directory or REFERENCE_DIR)
        read = lambda name: pd.read_csv(d / name, dtype=str, keep_default_na=False)
        return cls(read("substances.csv"), read("rules.csv"), read("food_categories.csv"))

    def find_by_code(self, code):
        code = code.lower()
        return self.by_code.get(code) or self.by_code.get(code.rstrip("abcdefghijklmnopqrstuvwxyz"))

    def find_by_name(self, name):
        if not name:
            return None, None
        if name in self.by_alias:
            return self.by_alias[name], 100.0
        if len(name) < MIN_FUZZY_LEN:
            return None, None
        hit = process.extractOne(name, self._alias_keys, scorer=fuzz.ratio, score_cutoff=FUZZY_CUTOFF)
        return (self.by_alias[hit[0]], round(hit[1], 1)) if hit else (None, None)

    def rules_for(self, substance_id, category):
        rows = self.rules[self.rules["substance_id"] == substance_id]
        return [r for r in rows.to_dict("records") if category_applies(r["category"], category)]

    def _finding(self, substance_id, item, matched_by, category):
        s = self.substances.loc[substance_id]
        rules = self.rules_for(substance_id, category)
        chosen = sorted(rules, key=lambda r: (-SEVERITY.get(r["status"], 0), r["category"] == "ALL"))
        rule = chosen[0] if chosen else None
        status = rule["status"] if rule else "ok"
        message = rule["note"] if rule else "No FSSAI restriction loaded for this ingredient."
        if status == "restricted" and category:
            message += f" Limits for {self.categories.get(category, category)} are not loaded yet."
        elif status == "restricted":
            message += " Set the product's food category to check it."
        return {
            "ingredient": item["raw"], "substance_id": substance_id, "substance": s["name"],
            "ins": s["ins"] or None, "kind": s["kind"], "matched_by": matched_by, "status": status,
            "severity": SEVERITY.get(status, 0), "message": message,
            "max_level": (rule["max_level"] or None) if rule else None, "unit": (rule["unit"] or None) if rule else None,
            "source": rule["source"] if rule else None, "source_url": rule["source_url"] if rule else None,
            "verified": (rule["verified"] == "yes") if rule else None,
        }

    def check(self, ingredients_text=None, category=None, additive_codes=()):
        items = parse(ingredients_text or "")
        findings, unrecognized, notes, seen = [], [], [], set()

        def add(sid, item, how):
            if sid and sid not in seen:
                seen.add(sid)
                findings.append(self._finding(sid, item, how, category))
                return True
            return bool(sid)

        for item in items:
            notes += [{"ingredient": item["raw"], "message": m} for m in item["notes"]]
            hit = False
            for code in item["codes"]:
                hit |= add(self.find_by_code(code), item, f"INS {code}")
            for name in ([] if item["is_class"] else [item["name"]]) + item["sub_names"]:
                sid, score = self.find_by_name(name)
                hit |= add(sid, item, "name" if score == 100.0 else f"similar name ({score})")
            if not hit and not item["is_class"] and not item["notes"]:
                unrecognized.append(item["raw"])
        for code in additive_codes:
            add(self.find_by_code(code), {"raw": f"INS {code}"}, f"INS {code} (product database)")
        worst = max([f["severity"] for f in findings] + [1 if notes else 0])
        findings.sort(key=lambda f: -f["severity"])
        return {
            "verdict": VERDICT[worst], "category": category, "category_name": self.categories.get(category),
            "findings": findings, "label_notes": notes, "other_ingredients": unrecognized,
            "ingredients_parsed": len(items),
        }
