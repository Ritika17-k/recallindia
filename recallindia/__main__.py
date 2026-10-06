import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from .config import CLEAN_DIR, EVAL_DIR, MATCH_THRESHOLD, RAW_DIR, REFERENCE_DIR, REPORT_DIR, SAMPLES_DIR


def _read_csv(path):
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def _clean_path(name):
    return CLEAN_DIR / f"{name}.pkl"


def cmd_discover(args):
    from .config import FOSCOS_URL, RECALL_URL
    from .scraper import discover
    for url in [args.url] if args.url else [RECALL_URL]:
        try:
            out, summary = discover(url)
        except Exception as e:
            print(f"{url}: failed ({e})")
            continue
        print(json.dumps({k: summary[k] for k in ("url", "tables", "rows_appeared", "table_rows_found",
                                                  "json_rows_found")}, indent=2))
        for t in summary["table_details"]:
            print("Table headers:", t["headers"], "| rows:", t["rows"])
        print("Requests captured:", len(summary["requests"]))
        print(f"Saved to {out}")


def cmd_scrape(args):
    from .scraper import scrape
    path, info = scrape(args.url, args.max_pages)
    print(f"Scraped {info['rows']} rows from {info['pages']} pages ({info['source']}) -> {path}")


def cmd_clean(args):
    from .clean import clean_purchases, clean_recalls
    from .scraper import latest_raw
    src = Path(args.recalls) if args.recalls else (SAMPLES_DIR / "sample_recalls.csv" if args.sample else latest_raw())
    if not src or not src.exists():
        sys.exit("No raw file found. Run scrape first or pass --sample.")
    recalls, report = clean_recalls(_read_csv(src))
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    recalls.to_pickle(_clean_path("recalls"))
    recalls.to_csv(CLEAN_DIR / "recalls.csv", index=False)
    (CLEAN_DIR / "clean_report.json").write_text(json.dumps(report, indent=2))
    print(f"Recalls: {report['rows_in']} in, {report['rows_out']} out, {report['duplicates_removed']} duplicates removed")
    purchases_src = Path(args.purchases) if args.purchases else (
        SAMPLES_DIR / "sample_purchases.csv" if args.sample else None)
    if purchases_src and purchases_src.exists():
        purchases = clean_purchases(_read_csv(purchases_src))
        purchases.to_pickle(_clean_path("purchases"))
        print(f"Purchases: {len(purchases)} rows")


def cmd_report(args):
    from .quality import profile, to_markdown, validate
    recalls = pd.read_pickle(_clean_path("recalls"))
    report_file = CLEAN_DIR / "clean_report.json"
    clean_report = json.loads(report_file.read_text()) if report_file.exists() else None
    prof, val = profile(recalls), validate(recalls)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "quality_report.md").write_text(to_markdown(prof, val, clean_report), encoding="utf-8")
    (REPORT_DIR / "validation.json").write_text(json.dumps(val, indent=2))
    print(f"Validation {'PASSED' if val['passed'] else 'FAILED'}: {len(val['errors'])} error rules, "
          f"{len(val['warnings'])} warning rules -> {REPORT_DIR / 'quality_report.md'}")


def cmd_load(args):
    from .db import Purchase, Recall, get_engine, init_db, upsert
    engine = get_engine()
    init_db(engine)
    n = upsert(engine, Recall, pd.read_pickle(_clean_path("recalls")).dropna(subset=["recall_id", "product_name"]),
               "recall_id")
    print(f"Loaded {n} recalls")
    if _clean_path("purchases").exists():
        print(f"Loaded {upsert(engine, Purchase, pd.read_pickle(_clean_path('purchases')), 'purchase_id')} purchases")


def cmd_match(args):
    threshold = args.threshold or MATCH_THRESHOLD
    if args.memory:
        from .matching import match_all
        matches = match_all(pd.read_pickle(_clean_path("recalls")), pd.read_pickle(_clean_path("purchases")), threshold)
    else:
        from .db import get_engine, match_from_db, save_matches
        engine = get_engine()
        matches = match_from_db(engine, threshold)
        save_matches(engine, matches)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    matches.to_csv(REPORT_DIR / "matches.csv", index=False)
    print(f"{len(matches)} matches at threshold {threshold} -> {REPORT_DIR / 'matches.csv'}")


def cmd_evaluate(args):
    from .clean import clean_purchases, clean_recalls
    from .matching import evaluate, evaluate_end_to_end, score_labeled_pairs
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    if args.sample:
        recalls, _ = clean_recalls(_read_csv(SAMPLES_DIR / "sample_recalls.csv"))
        purchases = clean_purchases(_read_csv(SAMPLES_DIR / "sample_purchases.csv"))
        labels = _read_csv(SAMPLES_DIR / "labeled_pairs.csv")
        labels["recall_id"] = labels["recall_id"].str.upper()
        results, best = evaluate(score_labeled_pairs(recalls, purchases, labels), labels)
        title = "Synthetic sample pairs"
    else:
        if not _clean_path("recalls").exists():
            sys.exit("Run scrape and clean first, or use --sample.")
        recalls = pd.read_pickle(_clean_path("recalls")).set_index("recall_id", drop=False)
        purchases = clean_purchases(_read_csv(args.purchases or EVAL_DIR / "real_purchases.csv"))
        gold = _read_csv(args.gold or EVAL_DIR / "real_gold.csv")
        gold = gold[gold["recall_id"].isin(recalls.index)]
        results, best, scored = evaluate_end_to_end(recalls, purchases, gold)
        scored.to_csv(REPORT_DIR / "evaluation_scores.csv", index=False)
        title = (f"{len(purchases)} labelled purchases against {len(recalls)} recalls "
                 f"({len(gold)} true matches; candidate search found {best['candidate_recall']:.0%} of them)")
    results.to_csv(REPORT_DIR / "evaluation.csv", index=False)
    print(title)
    print(results.to_string(index=False))
    print(f"Best threshold {best['threshold']:.0f}: precision {best['precision']}, recall {best['recall']}, "
          f"F1 {best['f1']}")


def _memory_recall_lookup():
    path = _clean_path("recalls")
    if not path.exists():
        return None
    from .matching import candidates_in_memory
    recalls = pd.read_pickle(path).set_index("recall_id", drop=False)
    return lambda product_norm, brand_norm: [recalls.loc[i].to_dict() for i in
                                             candidates_in_memory(recalls, {"product_norm": product_norm})]


def cmd_load_rules(args):
    from .db import get_engine, init_db, load_reference
    engine = get_engine()
    init_db(engine)
    print(load_reference(engine, args.dir or REFERENCE_DIR))


def cmd_check(args):
    from . import openfoodfacts
    from .checker import ProductNotFound, check_product
    from .rulebook import RuleBook
    try:
        result = check_product(RuleBook.from_csv(args.dir), barcode=args.barcode, ingredients_text=args.text,
                               category=args.category, product_name=args.name, brand=args.brand,
                               fetch_product=openfoodfacts.fetch, recall_lookup=_memory_recall_lookup())
    except (ProductNotFound, ValueError, openfoodfacts.ProductLookupError) as e:
        sys.exit(str(e))
    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return
    p = result["product"]
    print(f"{p['product_name'] or 'Product'} ({p['source']})  category: {result['category_name'] or 'not set'}")
    print(f"Verdict: {result['verdict'].upper()}")
    for f in result["findings"]:
        if f["status"] != "ok":
            print(f"  [{f['status']}] {f['substance']}{' (INS ' + f['ins'] + ')' if f['ins'] else ''}: {f['message']}")
    for n in result["label_notes"]:
        print(f"  [note] {n['message']}")
    for r in result["recalls"]:
        print(f"  [recall] {r['recall_id']} {r['product_name']}: {r['reason']}")
    ok = [f["substance"] for f in result["findings"] if f["status"] == "ok"]
    if ok:
        print(f"  No restriction found: {', '.join(ok)}")
    print(result["disclaimer"])


def cmd_serve(args):
    import uvicorn
    uvicorn.run("recallindia.api:app", host=args.host, port=args.port, reload=False)


def main(argv=None):
    p = argparse.ArgumentParser(prog="recallindia")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("discover")
    s.add_argument("--url")
    s.set_defaults(func=cmd_discover)
    s = sub.add_parser("scrape")
    s.add_argument("--url")
    s.add_argument("--max-pages", type=int)
    s.set_defaults(func=cmd_scrape)
    s = sub.add_parser("clean")
    s.add_argument("--recalls")
    s.add_argument("--purchases")
    s.add_argument("--sample", action="store_true")
    s.set_defaults(func=cmd_clean)
    sub.add_parser("report").set_defaults(func=cmd_report)
    sub.add_parser("load").set_defaults(func=cmd_load)
    s = sub.add_parser("match")
    s.add_argument("--threshold", type=float)
    s.add_argument("--memory", action="store_true")
    s.set_defaults(func=cmd_match)
    s = sub.add_parser("evaluate")
    s.add_argument("--sample", action="store_true")
    s.add_argument("--purchases")
    s.add_argument("--gold")
    s.set_defaults(func=cmd_evaluate)
    s = sub.add_parser("load-rules")
    s.add_argument("--dir")
    s.set_defaults(func=cmd_load_rules)
    s = sub.add_parser("check")
    s.add_argument("--barcode")
    s.add_argument("--text")
    s.add_argument("--category")
    s.add_argument("--name")
    s.add_argument("--brand")
    s.add_argument("--dir")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_check)
    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(func=cmd_serve)
    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
