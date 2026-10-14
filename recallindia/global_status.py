"""Global Regulatory Status: how an ingredient is regulated in India compared with other jurisdictions.

This describes ingredients only. It takes no brand or product as input and makes no statement about any product;
it reports what each regulator's rule says, with its source and date. India's status comes from the FSSAI
rulebook (data/reference/rules.csv), so there is one source of truth for Indian rules.
"""
import re
from pathlib import Path
import pandas as pd
from .clean import normalize_name
from .config import REFERENCE_DIR

JURISDICTIONS = {"IN": "India (FSSAI)", "EU": "European Union", "US": "United States (FDA)",
                 "US-CA": "California, US"}
STATUS_LABELS = {
    "not_authorised": "Not authorised in food",
    "being_withdrawn": "Authorisation revoked, being phased out",
    "restricted": "Allowed only in some foods",
    "warning_label": "Allowed with a mandatory warning label",
    "permitted": "Permitted",
    "prohibited": "Prohibited in all food",
    "permitted_some_categories": "Permitted in some food categories, within limits",
    "limit": "Permitted up to a limit",
    "no_rule_loaded": "No FSSAI rule loaded in RecallIndia yet",
    "partial_rule_loaded": "Not permitted in some food categories; general status not loaded yet",
}
NOT_ALLOWED = {"not_authorised", "being_withdrawn"}
INDIA_ALLOWED = {"permitted_some_categories", "limit", "permitted"}
DISCLAIMER = ("Regulatory status of an ingredient, taken from each regulator's published rules. It is not a statement "
              "about any product or brand, and rules marked unverified are still being checked against the official "
              "text.")

def load(directory=None):
    path = Path(directory or REFERENCE_DIR) / "global_status.csv"
    if not path.exists():
        return pd.DataFrame(columns=["substance_id", "jurisdiction", "status", "note", "source", "source_url",
                                     "as_of", "verified"])
    return pd.read_csv(path, dtype=str, keep_default_na=False)

def india_status(rulebook, substance_id):
    """Summarise the FSSAI rules that apply across all foods."""
    rows = rulebook.rules[(rulebook.rules["substance_id"] == substance_id) & (rulebook.rules["category"] == "ALL")]
    if rows.empty:
        restricted = rulebook.rules[rulebook.rules["substance_id"] == substance_id]
        if restricted.empty:
            return {"jurisdiction": "IN", "status": "no_rule_loaded", "note": "", "source": "", "source_url": "",
                    "verified": None}
        rows = restricted
    order = {"prohibited": 0, "limit": 1, "restricted": 2, "not_permitted": 3}
    rule = sorted(rows.to_dict("records"), key=lambda r: order.get(r["status"], 9))[0]
    # A "not permitted in infant food" rule says nothing about other foods, so it is never read as "allowed".
    status = {"restricted": "permitted_some_categories", "not_permitted": "partial_rule_loaded"}.get(
        rule["status"], rule["status"])
    note = rule["note"]
    if rule["status"] == "not_permitted":
        cats = sorted(set(rows[rows["status"] == "not_permitted"]["category"]))
        note = f"Not permitted in categories {', '.join(cats)}; other categories not loaded yet."
    return {"jurisdiction": "IN", "status": status, "note": note, "source": rule["source"],
            "source_url": rule["source_url"], "verified": rule["verified"] == "yes"}

class GlobalStatus:
    def __init__(self, rulebook, table=None):
        self.rulebook = rulebook
        self.table = (load() if table is None else table).fillna("")

    def substances_with_data(self):
        return list(dict.fromkeys(self.table["substance_id"]))

    def find(self, query):
        """Substance IDs for an INS/E number or a name."""
        q = str(query or "").strip()
        if not q:
            return []
        code = re.search(r"(?:ins|e)?\s*-?\s*(\d{3,4}[a-z]?)\b", q.lower())
        ids = [self.rulebook.find_by_code(code.group(1)) if code else None,
               self.rulebook.find_by_name(normalize_name(q))[0]]
        return list(dict.fromkeys(i for i in ids if i))

    def status(self, substance_id):
        s = self.rulebook.substances.loc[substance_id]
        foreign = self.table[self.table["substance_id"] == substance_id]
        rows = [india_status(self.rulebook, substance_id)]
        for r in foreign.to_dict("records"):
            rows.append({"jurisdiction": r["jurisdiction"], "status": r["status"], "note": r["note"],
                         "source": r["source"], "source_url": r["source_url"], "as_of": r["as_of"],
                         "verified": r["verified"] == "yes"})
        for r in rows:
            r["jurisdiction_name"] = JURISDICTIONS.get(r["jurisdiction"], r["jurisdiction"])
            r["status_label"] = STATUS_LABELS.get(r["status"], r["status"])
        india = rows[0]["status"]
        stricter = [r["jurisdiction"] for r in rows[1:] if r["status"] in NOT_ALLOWED]
        return {
            "substance_id": substance_id, "name": s["name"], "ins": s["ins"] or None, "kind": s["kind"],
            "jurisdictions": rows,
            "stricter_abroad": stricter if india in INDIA_ALLOWED else [],
            "summary": self._summary(s["name"], india, rows, stricter),
            "disclaimer": DISCLAIMER,
        }

    @staticmethod
    def _summary(name, india, rows, stricter):
        if india in ("no_rule_loaded", "partial_rule_loaded"):
            return f"{name}: FSSAI status not loaded yet; see other jurisdictions below."
        if india in INDIA_ALLOWED and stricter:
            by_j = {r["jurisdiction"]: r["status"] for r in rows}
            names = "; ".join(f"{JURISDICTIONS.get(j, j)}: {STATUS_LABELS[by_j[j]].lower()}" for j in stricter)
            return f"{name} is allowed in India within FSSAI rules. Stricter elsewhere - {names}."
        return f"{name}: {STATUS_LABELS.get(india, india)} in India."

    def lookup(self, query):
        return [self.status(i) for i in self.find(query)]

    def divergent(self):
        """Ingredients India allows that at least one other jurisdiction does not."""
        out = [self.status(i) for i in self.substances_with_data() if i in self.rulebook.substances.index]
        return [o for o in out if o["stricter_abroad"]]
