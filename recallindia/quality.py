from datetime import date
import pandas as pd
from .clean import DATE_COLUMNS, is_placeholder_reason

REQUIRED = ["recall_id", "product_name", "recall_date"]

def profile(df):
    rows = len(df)
    cols = {}
    for col in df.columns:
        s = df[col]
        non_null = s.notna() & (s.astype(str).str.len() > 0)
        info = {
            "non_null": int(non_null.sum()),
            "missing": int(rows - non_null.sum()),
            "missing_pct": round(100 * (rows - non_null.sum()) / rows, 1) if rows else 0.0,
            "distinct": int(s[non_null].astype(str).nunique()),
        }
        if col in DATE_COLUMNS and non_null.any():
            vals = s[non_null]
            info["min"], info["max"] = str(min(vals)), str(max(vals))
        else:
            info["examples"] = [str(v) for v in s[non_null].astype(str).unique()[:3]]
        cols[col] = info
    return {"rows": rows, "columns": cols}

def validate(df, today=None):
    today = today or date.today()
    errors, warnings = [], []

    def add(kind, rule, rows):
        rows = [int(r) for r in rows]
        if rows:
            (errors if kind == "error" else warnings).append({"rule": rule, "count": len(rows), "rows": rows[:20]})

    for col in REQUIRED:
        add("error", f"{col} is required", df.index[df[col].isna()])
    ids = df["recall_id"].dropna()
    add("error", "recall_id must be unique", ids[ids.duplicated(keep=False)].index)
    add("error", "recall_date must not be in the future",
        df.index[df["recall_date"].map(lambda d: d is not None and pd.notna(d) and d > today)])
    both = df["manufacture_date"].notna() & df["expiry_date"].notna()
    add("error", "expiry_date must be after manufacture_date",
        df.index[both & (df["expiry_date"] < df["manufacture_date"])])
    mfg = df["manufacture_date"].notna() & df["recall_date"].notna()
    add("warning", "recall_date is before manufacture_date",
        df.index[mfg & (df["recall_date"] < df["manufacture_date"])])
    lic = df["license_no"].dropna().astype(str)
    add("warning", "license_no is not 14 digits", lic[~lic.str.fullmatch(r"\d{14}")].index)
    add("warning", "reason is a placeholder or not a recall reason",
        df.index[df["reason"].map(is_placeholder_reason)])
    if "hazard" in df.columns:
        add("warning", "reason does not name a recognised hazard",
            df.index[df["reason"].notna() & df["hazard"].isna()])
    for col in ["brand", "batch_no", "reason", "status"]:
        add("warning", f"{col} is missing", df.index[df[col].isna()])
    return {"rows": int(len(df)), "passed": not errors, "errors": errors, "warnings": warnings}

def to_markdown(prof, val, clean_report=None):
    lines = ["# Data quality report", ""]
    if clean_report:
        lines += [f"- Rows scraped: {clean_report['rows_in']}",
                  f"- Duplicates removed: {clean_report['duplicates_removed']}",
                  f"- Rows after cleaning: {clean_report['rows_out']}",
                  f"- Unparseable dates: {len(clean_report['unparseable_dates'])}",
                  f"- Columns not mapped: {', '.join(clean_report['unmapped_columns']) or 'none'}", ""]
    lines += ["## Validation", "", f"**{'PASSED' if val['passed'] else 'FAILED'}** "
              f"({len(val['errors'])} error rules, {len(val['warnings'])} warning rules)", ""]
    for kind in ("errors", "warnings"):
        for item in val[kind]:
            lines.append(f"- {kind[:-1].upper()}: {item['rule']} ({item['count']} rows)")
    lines += ["", "## Profile", "", "| Column | Filled | Missing % | Distinct | Range / examples |",
              "|---|---|---|---|---|"]
    for col, info in prof["columns"].items():
        extra = f"{info['min']} to {info['max']}" if "min" in info else ", ".join(info.get("examples", []))
        lines.append(f"| {col} | {info['non_null']} | {info['missing_pct']} | {info['distinct']} | {extra[:60]} |")
    return "\n".join(lines) + "\n"