import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from .config import (AUTO_PUBLISH_SCORE, CLEAN_DIR, DATA, DEFAULT_USER_ID, EVAL_DIR, RAW_DIR, REFERENCE_DIR, REPORT_DIR,
                     REVIEW_MIN_SCORE, SAMPLES_DIR)


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
    from .db import Recall, get_engine, init_db, new_recall_count, save_purchases, upsert
    engine = get_engine()
    init_db(engine)
    n = upsert(engine, Recall, pd.read_pickle(_clean_path("recalls")).dropna(subset=["recall_id", "product_name"]),
               "recall_id")
    print(f"Loaded {n} recalls ({new_recall_count(engine)} first seen since the last run)")
    if _clean_path("purchases").exists():
        purchases = pd.read_pickle(_clean_path("purchases"))
        print(f"Loaded {save_purchases(engine, purchases, user_id=args.user, source='csv')} purchases")


def _print_bands(matches):
    from .review import AUTO, PENDING
    vc = matches["status"].value_counts() if len(matches) else {}
    print(f"{int(vc.get(AUTO, 0))} auto-published (score >= {AUTO_PUBLISH_SCORE:.0f}), "
          f"{int(vc.get(PENDING, 0))} sent to the review queue ({REVIEW_MIN_SCORE:.0f}-{AUTO_PUBLISH_SCORE:.0f})")


def cmd_match(args):
    from .review import assign_status
    floor = args.threshold or REVIEW_MIN_SCORE
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    if args.memory:
        from .matching import match_all
        recalls = pd.read_pickle(_clean_path("recalls"))
        purchases = pd.read_pickle(_clean_path("purchases"))
        matches = assign_status(match_all(recalls, purchases, floor), floor=floor)
        from .review import queue_frame
        queue_frame(matches, recalls, purchases).to_csv(REPORT_DIR / "review_queue.csv", index=False)
    else:
        from .db import get_engine, match_from_db, save_matches
        engine = get_engine()
        matches = match_from_db(engine, floor=floor)
        save_matches(engine, matches)
    matches.to_csv(REPORT_DIR / "matches.csv", index=False)
    _print_bands(matches)
    print(f"-> {REPORT_DIR / 'matches.csv'}" + (f" and {REPORT_DIR / 'review_queue.csv'}" if args.memory else ""))


def cmd_run(args):
    from .clean import clean_recalls
    from .db import get_engine, init_db
    from .pipeline import refresh
    recalls = None
    if args.scrape:
        from .scraper import scrape
        path, info = scrape(None, None)
        print(f"Scraped {info['rows']} rows -> {path}")
    if args.scrape or args.recalls:
        from .scraper import latest_raw
        src = Path(args.recalls) if args.recalls else latest_raw()
        recalls, report = clean_recalls(_read_csv(src))
        recalls.to_pickle(_clean_path("recalls"))
        print(f"Cleaned {report['rows_out']} recalls from {src.name}")
    elif _clean_path("recalls").exists():
        recalls = pd.read_pickle(_clean_path("recalls"))
        print(f"Loading {len(recalls)} cleaned recalls from {_clean_path('recalls').name}")
    engine = get_engine()
    init_db(engine)
    summary = refresh(engine, recalls, send=not args.no_send, channel=args.channel)
    print(json.dumps(summary, indent=2))


def cmd_review(args):
    from .db import decide, get_engine, review_queue
    engine = get_engine()
    if args.action == "list":
        rows = review_queue(engine, limit=args.limit)
        if not rows:
            print("The review queue is empty.")
        for r in rows:
            print(f"[{r['match_id']}] score {r['score']:.0f}  ({r['notes'] or 'no caps applied'})")
            print(f"    bought: {r['purchase_product']} | brand {r['purchase_brand'] or '-'} | "
                  f"batch {r['purchase_batch'] or '-'} | {r['purchase_store'] or ''} {r['purchase_date'] or ''}")
            print(f"    recall {r['recall_id']}: {r['recall_brand'] or '-'} | {r['recall_product']} | "
                  f"batch {r['recall_batch'] or '-'} | {r['recall_fbo'] or ''}")
        return
    if not args.ids:
        sys.exit("Give one or more match IDs from `review list`.")
    for match_id in args.ids:
        ok = decide(engine, match_id, args.action == "approve", reviewer=args.reviewer, note=args.note)
        print(f"{match_id}: {('approved' if args.action == 'approve' else 'rejected') if ok else 'not pending (already decided or unknown ID)'}")
    if args.action == "approve" and not args.no_send:
        from . import alerts
        queued = alerts.queue_alerts(engine)
        sent, failed = alerts.dispatch(engine)
        print(f"Alerts: {queued} queued, {sent} sent, {failed} failed")


def cmd_alerts(args):
    from . import alerts
    from .db import get_engine
    engine = get_engine()
    queued = alerts.queue_alerts(engine, args.channel)
    sent, failed = (0, 0) if args.queue_only else alerts.dispatch(engine, args.channel)
    print(f"{queued} alerts queued, {sent} sent, {failed} failed")


def cmd_users(args):
    from .db import User, get_engine, init_db, read_table, upsert
    engine = get_engine()
    init_db(engine)
    if args.action == "add":
        upsert(engine, User, pd.DataFrame([{"user_id": args.user_id, "email": args.email, "name": args.name}]),
               "user_id")
        print(f"Saved user {args.user_id}")
    else:
        print(read_table(engine, User).to_string(index=False))


def _save_or_preview(purchases, args, label):
    if purchases.empty:
        print(f"No purchase lines found in the {label}.")
        return
    cols = ["purchase_id", "product_name", "store", "purchase_date"]
    print(purchases[cols].to_string(index=False))
    out = DATA / "intake"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{label.replace(' ', '_')}_{args.user}.csv"
    purchases.drop(columns=["product_norm", "brand_norm"], errors="ignore").to_csv(path, index=False)
    print(f"{len(purchases)} purchase lines -> {path}")
    if args.dry_run:
        return
    from .db import get_engine, init_db
    from .pipeline import ingest_purchases
    engine = get_engine()
    init_db(engine)
    summary = ingest_purchases(engine, purchases, user_id=args.user)
    summary.pop("matches", None)
    print(json.dumps(summary, indent=2))


def cmd_import_email(args):
    from .email_import import gmail_api_messages, import_messages, iter_messages
    if args.gmail:
        messages = gmail_api_messages(args.query or None, args.max) if args.query else gmail_api_messages(
            max_results=args.max)
    elif args.path:
        messages = iter_messages(args.path)
    else:
        sys.exit("Pass a Takeout .mbox file, an .eml file or folder, or --gmail.")
    purchases, orders = import_messages(messages, args.user, require_known_retailer=not args.any_sender)
    print(f"{len(orders)} order emails: " + ", ".join(sorted({o['retailer'] for o in orders})))
    _save_or_preview(purchases, args, "order emails")


def cmd_import_receipt(args):
    from .receipts import import_receipt
    frames = []
    for path in args.paths:
        purchases, parsed = import_receipt(path, args.user, store=args.store)
        print(f"{path}: {parsed['store'] or 'unknown shop'}, {parsed['purchase_date'] or 'no date found'}, "
              f"{len(parsed['items'])} lines")
        if args.show_text:
            print(parsed["text"])
        frames.append(purchases)
    _save_or_preview(pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(), args, "receipts")


def cmd_global_status(args):
    from .global_status import GlobalStatus
    from .rulebook import RuleBook
    gs = GlobalStatus(RuleBook.from_csv(args.dir))
    results = gs.divergent() if args.divergent else gs.lookup(args.query or "")
    if args.json:
        print(json.dumps(results, indent=2, default=str))
        return
    if not results:
        print("No matching ingredient." if not args.divergent else "No differences loaded.")
    for r in results:
        print(f"\n{r['name']}{' (INS ' + r['ins'] + ')' if r['ins'] else ''}")
        print(f"  {r['summary']}")
        for j in r["jurisdictions"]:
            flag = "  [unverified]" if j["verified"] is False else ""
            print(f"  {j['jurisdiction_name']:<22} {j['status_label']}{flag}")
            if j["note"]:
                print(f"  {'':<22} {j['note']}")
    if results:
        print("\n" + results[0]["disclaimer"])


def _evaluate_set(recalls, purchases_path, gold_path, name):
    from .clean import clean_purchases
    from .matching import evaluate_end_to_end
    from .review import review_metrics
    purchases = clean_purchases(_read_csv(purchases_path))
    gold = _read_csv(gold_path)
    gold = gold[gold["recall_id"].isin(recalls.index)]
    results, best, scored = evaluate_end_to_end(recalls, purchases, gold)
    scored.to_csv(REPORT_DIR / f"evaluation_scores_{name}.csv", index=False)
    results.to_csv(REPORT_DIR / f"evaluation_{name}.csv", index=False)
    bands = review_metrics(scored, gold)
    print(f"\n{name}: {len(purchases)} receipt lines against {len(recalls)} recalls, {len(gold)} true matches "
          f"(candidate search found {best['candidate_recall']:.0%})")
    print(results.to_string(index=False))
    print(f"Review bands: auto-publish {bands['auto_published']} matches at precision {bands['auto_precision']}, "
          f"recall {bands['auto_recall']}; review queue {bands['queue_size']} "
          f"({bands['queue_true_matches']} true); recall after review {bands['recall_after_review']}")
    return {"set": name, "purchases": len(purchases), "true_matches": len(gold), **best, **bands}


def cmd_evaluate(args):
    from .clean import clean_purchases, clean_recalls
    from .matching import evaluate, score_labeled_pairs
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    if args.sample:
        recalls, _ = clean_recalls(_read_csv(SAMPLES_DIR / "sample_recalls.csv"))
        purchases = clean_purchases(_read_csv(SAMPLES_DIR / "sample_purchases.csv"))
        labels = _read_csv(SAMPLES_DIR / "labeled_pairs.csv")
        labels["recall_id"] = labels["recall_id"].str.upper()
        results, best = evaluate(score_labeled_pairs(recalls, purchases, labels), labels)
        results.to_csv(REPORT_DIR / "evaluation_sample.csv", index=False)
        print("Synthetic sample pairs")
        print(results.to_string(index=False))
        return
    if not _clean_path("recalls").exists():
        sys.exit("Run scrape and clean first, or use --sample.")
    recalls = pd.read_pickle(_clean_path("recalls")).set_index("recall_id", drop=False)
    if args.purchases or args.gold:
        if not (args.purchases and args.gold):
            sys.exit("Pass both --purchases and --gold.")
        summaries = [_evaluate_set(recalls, args.purchases, args.gold, "custom")]
    else:
        summaries = [_evaluate_set(recalls, EVAL_DIR / "real_purchases.csv", EVAL_DIR / "real_gold.csv", "dev"),
                     _evaluate_set(recalls, EVAL_DIR / "holdout_purchases.csv", EVAL_DIR / "holdout_gold.csv",
                                   "holdout")]
    pd.DataFrame(summaries).to_csv(REPORT_DIR / "evaluation_summary.csv", index=False)
    print(f"\nSummary -> {REPORT_DIR / 'evaluation_summary.csv'}")


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
    from .global_status import GlobalStatus, load
    from .rulebook import RuleBook
    rb = RuleBook.from_csv(args.dir)
    try:
        result = check_product(rb, barcode=args.barcode, ingredients_text=args.text,
                               category=args.category, product_name=args.name, brand=args.brand,
                               fetch_product=openfoodfacts.fetch, recall_lookup=_memory_recall_lookup(),
                               global_status=GlobalStatus(rb, load(args.dir)))
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
        for a in f.get("abroad", []):
            if a["status"] != "permitted":
                print(f"      {a['jurisdiction']}: {a['note']}")
    for n in result["label_notes"]:
        print(f"  [note] {n['message']}")
    for r in result["recalls"]:
        print(f"  [recall] {r['recall_id']} {r['product_name']}: {r['reason']}")
    ok = [f["substance"] for f in result["findings"] if f["status"] == "ok"]
    if ok:
        print(f"  No restriction found: {', '.join(ok)}")
    print(result["disclaimer"])


def cmd_vapid_keys(args):
    from .push import generate_vapid_keys
    public, private = generate_vapid_keys()
    print("Add these to .env (keep the private key secret):\n")
    print(f"VAPID_PUBLIC_KEY={public}")
    print(f"VAPID_PRIVATE_KEY={private}")
    print("VAPID_SUBJECT=mailto:you@example.com")


def setup_database():
    """First start on a new server: tables, rules, and the bundled FSSAI recall snapshot if there are no recalls yet."""
    from sqlalchemy import func, select
    from .clean import clean_recalls
    from .config import SEED_DIR
    from .db import Recall, get_engine, init_db, load_reference, upsert
    engine = get_engine()
    init_db(engine)
    print("Reference data:", load_reference(engine, REFERENCE_DIR))
    with engine.connect() as conn:
        count = conn.scalar(select(func.count()).select_from(Recall.__table__))
    if count == 0:
        seeds = sorted(SEED_DIR.glob("*.csv"))
        if seeds:
            recalls, report = clean_recalls(_read_csv(seeds[-1]))
            n = upsert(engine, Recall, recalls.dropna(subset=["recall_id", "product_name"]), "recall_id")
            print(f"Loaded {n} recalls from {seeds[-1].name}")
    else:
        print(f"{count} recalls already loaded")


def cmd_serve(args):
    import os
    import uvicorn
    if args.init:
        setup_database()
    port = args.port or int(os.environ.get("PORT", "8000"))
    uvicorn.run("recallindia.api:app", host=args.host, port=port, reload=False, proxy_headers=True,
                forwarded_allow_ips="*")


def main(argv=None):
    p = argparse.ArgumentParser(prog="recallindia", description="Indian product recall alerts")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("discover", help="inspect the FoSCoS recall page")
    s.add_argument("--url")
    s.set_defaults(func=cmd_discover)
    s = sub.add_parser("scrape", help="download FSSAI recall notices")
    s.add_argument("--url")
    s.add_argument("--max-pages", type=int)
    s.set_defaults(func=cmd_scrape)
    s = sub.add_parser("clean", help="normalise scraped recalls and purchase CSVs")
    s.add_argument("--recalls")
    s.add_argument("--purchases")
    s.add_argument("--sample", action="store_true")
    s.set_defaults(func=cmd_clean)
    sub.add_parser("report", help="data quality report").set_defaults(func=cmd_report)
    s = sub.add_parser("load", help="load cleaned recalls and purchases into PostgreSQL")
    s.add_argument("--user", default=DEFAULT_USER_ID, help="owner of purchases that have no user_id column")
    s.set_defaults(func=cmd_load)
    s = sub.add_parser("match", help="match purchases to recalls and fill the review queue")
    s.add_argument("--threshold", type=float, help=f"review floor (default {REVIEW_MIN_SCORE:.0f})")
    s.add_argument("--memory", action="store_true", help="match from the cleaned files, without the database")
    s.set_defaults(func=cmd_match)
    s = sub.add_parser("run", help="refresh: (scrape,) load recalls, rematch everything, send alerts")
    s.add_argument("--scrape", action="store_true", help="scrape FoSCoS first")
    s.add_argument("--recalls", help="raw recall CSV to load instead of scraping")
    s.add_argument("--no-send", action="store_true", help="queue alerts but do not send them")
    s.add_argument("--channel", choices=["outbox", "email", "console"])
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("review", help="manual review queue for medium-confidence matches")
    s.add_argument("action", choices=["list", "approve", "reject"])
    s.add_argument("ids", nargs="*", type=int)
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--reviewer")
    s.add_argument("--note")
    s.add_argument("--no-send", action="store_true", help="do not send alerts for approved matches yet")
    s.set_defaults(func=cmd_review)
    s = sub.add_parser("alerts", help="queue and send alerts for published matches")
    s.add_argument("--channel", choices=["outbox", "email", "console"])
    s.add_argument("--queue-only", action="store_true")
    s.set_defaults(func=cmd_alerts)
    s = sub.add_parser("users", help="add or list users")
    s.add_argument("action", choices=["add", "list"])
    s.add_argument("user_id", nargs="?")
    s.add_argument("--email")
    s.add_argument("--name")
    s.set_defaults(func=cmd_users)

    s = sub.add_parser("import-email", help="read purchases from order-confirmation emails")
    s.add_argument("path", nargs="?", help="Google Takeout .mbox, an .eml file, or a folder of .eml files")
    s.add_argument("--gmail", action="store_true", help="read from Gmail through the API instead")
    s.add_argument("--query", help="Gmail search query (default: known shops, order subjects, last 2 years)")
    s.add_argument("--max", type=int, default=200)
    s.add_argument("--user", default=DEFAULT_USER_ID)
    s.add_argument("--any-sender", action="store_true", help="also read emails from shops not on the list")
    s.add_argument("--dry-run", action="store_true", help="show and save to CSV, but not to the database")
    s.set_defaults(func=cmd_import_email)
    s = sub.add_parser("import-receipt", help="read purchases from receipt photos (OCR) or .txt receipts")
    s.add_argument("paths", nargs="+")
    s.add_argument("--user", default=DEFAULT_USER_ID)
    s.add_argument("--store", help="shop name, if the receipt header is unreadable")
    s.add_argument("--show-text", action="store_true", help="print the OCR text")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_import_receipt)

    s = sub.add_parser("evaluate", help="precision and recall on the labelled dev and holdout sets")
    s.add_argument("--sample", action="store_true")
    s.add_argument("--purchases")
    s.add_argument("--gold")
    s.set_defaults(func=cmd_evaluate)

    s = sub.add_parser("global-status", help="an ingredient's status in India, the EU and the US")
    s.add_argument("query", nargs="?", help="INS/E number or name, e.g. 127 or titanium dioxide")
    s.add_argument("--divergent", action="store_true", help="list ingredients India allows and others do not")
    s.add_argument("--dir")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_global_status)
    s = sub.add_parser("load-rules", help="load reference CSVs (rules, substances, global status)")
    s.add_argument("--dir")
    s.set_defaults(func=cmd_load_rules)
    s = sub.add_parser("check", help="check a label or barcode against FSSAI rules")
    s.add_argument("--barcode")
    s.add_argument("--text")
    s.add_argument("--category")
    s.add_argument("--name")
    s.add_argument("--brand")
    s.add_argument("--dir")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_check)
    sub.add_parser("vapid-keys", help="generate keys for phone notifications").set_defaults(func=cmd_vapid_keys)
    s = sub.add_parser("serve", help="start the API and the app")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, help="default: $PORT or 8000")
    s.add_argument("--init", action="store_true", help="set up tables, rules and seed recalls first (for hosting)")
    s.set_defaults(func=cmd_serve)
    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
