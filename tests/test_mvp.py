"""Tests for the MVP features: review queue, alerts, intake, global status and the evaluation sets."""
import shutil
import time
from datetime import date
from pathlib import Path
import pandas as pd
import pytest
from recallindia import config
from recallindia.alerts import batch_advice, build_message, reason_text, send
from recallindia.email_import import find_items, html_to_lines, import_messages, iter_messages
from recallindia.global_status import GlobalStatus
from recallindia.intake import to_purchases
from recallindia.receipts import parse_item_line, parse_receipt_text
from recallindia.review import AUTO, PENDING, assign_status, band, confidence, review_metrics
from recallindia.rulebook import RuleBook

FIXTURES = Path(__file__).parent / "fixtures"

def test_bands_split_at_auto_and_floor():
    assert band(100) == AUTO and band(85) == AUTO
    assert band(84.9) == PENDING and band(65) == PENDING
    assert band(64.9) is None and band(None) is None
    assert confidence(90) == "high" and confidence(70) == "medium" and confidence(10) == "low"

def test_assign_status_drops_low_scores():
    m = pd.DataFrame({"purchase_id": ["a", "b", "c"], "recall_id": ["1", "2", "3"], "score": [92.0, 70.0, 40.0]})
    out = assign_status(m)
    assert list(out["status"]) == [AUTO, PENDING]

def test_review_metrics_counts_queue_work():
    scored = pd.DataFrame({"purchase_id": ["a", "b", "c", "d"], "recall_id": ["1", "2", "3", "4"],
                           "score": [95.0, 70.0, 70.0, 50.0]})
    gold = pd.DataFrame({"purchase_id": ["a", "b", "d"], "recall_id": ["1", "2", "4"]})
    m = review_metrics(scored, gold)
    assert m["auto_published"] == 1 and m["auto_precision"] == 1.0
    assert m["queue_size"] == 2 and m["queue_true_matches"] == 1
    assert m["recall_after_review"] == round(2 / 3, 3) and m["missed"] == 1

def test_reason_text_hides_placeholders():
    assert "does not give a usable reason" in reason_text("3")
    assert "does not give a usable reason" in reason_text(None)
    assert reason_text("Sorbate exceeds limit") == "Sorbate exceeds limit"

def test_batch_advice():
    assert "matches the recalled batch" in batch_advice("batch no. ac25l10a1", "AC25L10A1")
    assert "covers batch 07/G" in batch_advice(None, "07/G")
    assert "does not list a batch" in batch_advice("X1", None)

def _alert_row(**kw):
    row = {"match_id": 1, "score": 91.0, "status": AUTO, "purchase_product": "Ramdev Hing 50 g",
           "purchase_brand": None, "purchase_batch": None, "purchase_store": "Swiggy Instamart",
           "purchase_date": date(2026, 10, 6), "recall_id": "255", "recall_product": "Compounded Asafoetida",
           "recall_brand": "Ramdev", "recall_batch": "07/G", "recall_fbo": "RAMDEV FOOD PRODUCTS PVT. LTD.",
           "recall_reason": "3", "recall_type": "Sub-standard", "recall_date": date(2026, 9, 29),
           "recall_status": "Initiated", "recall_source": "FSSAI", "user_name": "Ritu"}
    row.update(kw)
    return row

def test_build_message_states_notice_facts_only():
    subject, body = build_message(_alert_row())
    assert subject == "Recall alert: Ramdev - Compounded Asafoetida (FSSAI notice 255)"
    assert "Hi Ritu" in body and "06 Oct 2026" in body and "batch 07/G" in body
    assert "does not give a usable reason" in body
    assert "an FSSAI recall notice" in body
    assert "a reviewer confirmed" in build_message(_alert_row(status="approved"))[1]

def test_outbox_channel_writes_eml(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTBOX_DIR", tmp_path)
    subject, body = build_message(_alert_row())
    send({"id": 7, "user_id": "ritu", "email": "ritu@example.com", "subject": subject, "body": body}, "outbox")
    eml = (tmp_path / "alert_7.eml").read_text()
    assert "To: ritu@example.com" in eml and "Compounded Asafoetida" in eml

def test_email_channel_needs_an_address():
    with pytest.raises(ValueError):
        send({"id": 1, "user_id": "u", "email": None, "subject": "s", "body": "b"}, "email")

def test_order_emails_from_mbox():
    purchases, orders = import_messages(iter_messages(FIXTURES / "orders.mbox"), "ritu")
    assert sorted(o["retailer"] for o in orders) == ["Amazon", "BigBasket", "Blinkit", "Swiggy Instamart"]
    names = set(purchases["product_name"])
    assert "Ramdev Hing 50 g" in names and "VPR Gold Coconut Oil 1 L" in names
    assert not any("total" in n.lower() or "charge" in n.lower() for n in names)
    assert "Fresh Mangoes" not in names  # promotional email
    assert "Pizza" not in " ".join(names)  # not a shop
    assert set(purchases["source"]) == {"gmail"} and set(purchases["user_id"]) == {"ritu"}
    amazon = purchases[purchases["store"] == "Amazon"]
    assert set(amazon["purchase_date"]) == {date(2026, 9, 28)}

def test_reimporting_gives_the_same_ids():
    a, _ = import_messages(iter_messages(FIXTURES / "orders.mbox"), "ritu")
    b, _ = import_messages(iter_messages(FIXTURES / "orders.mbox"), "ritu")
    assert list(a["purchase_id"]) == list(b["purchase_id"])
    c, _ = import_messages(iter_messages(FIXTURES / "orders.mbox"), "someone-else")
    assert not set(a["purchase_id"]) & set(c["purchase_id"])

def test_html_table_rows_become_items():
    html = ("<table><tr><th>Item</th><th>Qty</th><th>Price</th></tr>"
            "<tr><td>Tata Salt 1 kg</td><td>2</td><td>&#8377;56</td></tr>"
            "<tr><td>Delivery charge</td><td></td><td>&#8377;25</td></tr></table>")
    assert find_items(html_to_lines(html)) == [{"product_name": "Tata Salt 1 kg", "quantity": 2}]

def test_qty_line_pattern():
    lines = ["Eastern Chilli Powder, 100g", "Sold by: Cloudtail", "Quantity: 3", "Rs. 135.00"]
    assert find_items(lines) == [{"product_name": "Eastern Chilli Powder, 100g", "quantity": 3}]

# ---------- receipts ----------

def test_receipt_text_with_header():
    r = parse_receipt_text((FIXTURES / "receipt_kirana.txt").read_text())
    assert r["store"] == "SHREE GANESH KIRANA STORES"
    assert r["purchase_date"] == date(2026, 10, 5)
    assert [i["product_name"] for i in r["items"]] == [
        "EASTERN CHILLI PWD 100G", "RAMDEV HING 50G", "VPR GOLD COCONUT OIL 1L", "TATA SALT 1KG",
        "AASHIRVAAD ATTA 5KG"]
    assert r["items"][1]["quantity"] == 2 and r["items"][1]["amount"] == 120.0

def test_receipt_without_header_needs_prices():
    text = "FRESH MART\n12-10-2026\nAmul Butter 100g 58.00\nThank you\nParle G 1 10.00\nTotal 68.00"
    r = parse_receipt_text(text)
    assert [i["product_name"] for i in r["items"]] == ["Amul Butter 100g", "Parle G"]
    assert r["purchase_date"] == date(2026, 10, 12)

def test_item_line_strips_codes_and_prices():
    assert parse_item_line("09041100 RAMDEV HING 50G 2 60.00 120.00")["product_name"] == "RAMDEV HING 50G"
    assert parse_item_line("CGST 2.5% 8.35") is None
    assert parse_item_line("123456 78.00") is None

@pytest.mark.skipif(not (config.TESSERACT_CMD or shutil.which("tesseract")), reason="Tesseract not installed")
def test_receipt_photo_ocr():
    pytest.importorskip("pytesseract")
    from recallindia.receipts import import_receipt
    purchases, parsed = import_receipt(FIXTURES / "receipt_kirana.png", "ritu")
    assert "RAMDEV HING 50G" in list(purchases["product_name"])
    assert parsed["purchase_date"] == date(2026, 10, 5)

def test_to_purchases_keeps_per_item_store_and_date():
    p = to_purchases([{"product_name": "Ramdev Hing", "store": "DMart", "purchase_date": date(2026, 1, 2)}],
                     "ritu", "manual")
    assert p.iloc[0]["store"] == "DMart" and p.iloc[0]["purchase_date"] == date(2026, 1, 2)
    assert p.iloc[0]["purchase_id"].startswith("MN-")

# ---------- global regulatory status ----------

@pytest.fixture(scope="module")
def gs():
    return GlobalStatus(RuleBook.from_csv())


def test_divergent_lists_only_ingredients_india_allows(gs):
    names = {d["name"] for d in gs.divergent()}
    assert names == {"Erythrosine", "Ponceau 4R", "Carmoisine"}

def test_partial_india_rule_is_not_read_as_allowed(gs):
    cyclamate = gs.lookup("952")[0]
    assert cyclamate["jurisdictions"][0]["status"] == "partial_rule_loaded"
    assert cyclamate["stricter_abroad"] == []

def test_lookup_by_e_number_and_name(gs):
    assert gs.lookup("E171")[0]["name"] == "Titanium dioxide"
    assert gs.lookup("titanium dioxide")[0]["substance_id"] == "ins-171"
    bromate = {j["jurisdiction"]: j["status"] for j in gs.lookup("potassium bromate")[0]["jurisdictions"]}
    assert bromate == {"IN": "prohibited", "EU": "not_authorised", "US": "permitted", "US-CA": "not_authorised"}

def test_every_row_has_a_source_and_date():
    g = pd.read_csv(config.REFERENCE_DIR / "global_status.csv", dtype=str, keep_default_na=False)
    s = pd.read_csv(config.REFERENCE_DIR / "substances.csv", dtype=str, keep_default_na=False)
    assert set(g["substance_id"]) <= set(s["substance_id"])
    assert (g["source"] != "").all() and (g["source_url"].str.startswith("https://")).all()
    assert (g["as_of"] != "").all() and set(g["verified"]) <= {"yes", "no"}
    assert not g.duplicated(["substance_id", "jurisdiction"]).any()

def test_checker_shows_status_abroad(gs):
    from recallindia.checker import check_product
    result = check_product(gs.rulebook, ingredients_text="Sugar, Colour (INS 124)", category="5", global_status=gs)
    ponceau = next(f for f in result["findings"] if f["substance_id"] == "ins-124")
    assert {a["jurisdiction"] for a in ponceau["abroad"]} == {"US", "EU"}

# ---------- evaluation sets ----------

def test_evaluation_sets_are_large_and_separate():
    dev_p = pd.read_csv(config.EVAL_DIR / "real_purchases.csv", dtype=str)
    hold_p = pd.read_csv(config.EVAL_DIR / "holdout_purchases.csv", dtype=str)
    hold_g = pd.read_csv(config.EVAL_DIR / "holdout_gold.csv", dtype=str)
    assert len(dev_p) + len(hold_p) >= 100
    assert not set(dev_p["purchase_id"]) & set(hold_p["purchase_id"])
    assert set(hold_g["purchase_id"]) <= set(hold_p["purchase_id"])
    negatives = set(hold_p["purchase_id"]) - set(hold_g["purchase_id"])
    assert len(negatives) >= 15


def test_pipeline_against_postgres():
    import os
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("set TEST_DATABASE_URL to a throwaway PostgreSQL database to run this")
    pytest.importorskip("sqlalchemy")
    from recallindia.db import decide, get_engine, init_db, review_queue
    from recallindia.pipeline import ingest_purchases, refresh
    from recallindia.clean import clean_recalls
    engine = get_engine(url)
    init_db(engine)
    raw = pd.read_csv(config.SAMPLES_DIR / "sample_recalls.csv", dtype=str, keep_default_na=False)
    recalls, _ = clean_recalls(raw)
    refresh(engine, recalls, send=False)
    purchases = to_purchases([{"product_name": "Roasted Peanut Chikki 200g", "brand": "Acme"}], "ritu", "manual")
    summary = ingest_purchases(engine, purchases, user_id="ritu", send=False)
    assert summary["purchases"] == 1
    for row in review_queue(engine):
        assert decide(engine, row["match_id"], approve=False)

# ---------- app support ----------

def test_loose_items_are_not_tracked():
    from recallindia.intake import is_trackable, split_trackable
    assert not is_trackable("ONION 1KG") and not is_trackable("Loose Sugar") and not is_trackable("Green Chilli 100g")
    assert is_trackable("Tomato Ketchup 500g") and is_trackable("Ginger Garlic Paste 200g")
    assert is_trackable("Amul Taaza Toned Fresh Milk (500 ml)") and is_trackable("VPR Gold Coconut Oil 1 L")
    kept, skipped = split_trackable([{"product_name": "Eggs 12 pcs"}, {"product_name": "Ramdev Hing 50 g"}])
    assert [k["product_name"] for k in kept] == ["Ramdev Hing 50 g"] and len(skipped) == 1

def test_receipt_reports_untracked_lines():
    from recallindia.receipts import import_receipt
    text = ("FRESH MART\n12-10-2026\nItem Qty Amount\nONION 1KG 1 40.00\nRAMDEV HING 50G 1 60.00\nTotal 100.00")
    purchases, parsed = import_receipt(text, "ritu", is_text=True)
    assert list(purchases["product_name"]) == ["RAMDEV HING 50G"]
    assert [i["product_name"] for i in parsed["not_tracked"]] == ["ONION 1KG"]

def test_label_ingredients_from_ocr_text():
    from recallindia.checker import label_ingredients
    text = ("NUTRITION INFO per 100 g\nIngredients: Wheat flour (Atta), Sugar, Palm oil,\nColours (INS 124, 127), Salt.\n"
            "Allergen: Contains wheat. Best before 6 months")
    assert label_ingredients(text) == "Wheat flour (Atta), Sugar, Palm oil, Colours (INS 124, 127), Salt"
    assert label_ingredients("lngredients - Sugar, Cocoa") == "Sugar, Cocoa"

def test_app_files_exist():
    app = Path(__file__).parent.parent / "recallindia" / "static" / "app"
    for name in ("index.html", "app.js", "app.css", "manifest.webmanifest", "sw.js", "icon-192.png", "icon-512.png"):
        assert (app / name).is_file(), name

def test_alternatives_skip_flagged_unknown_and_recalled(gs):
    from recallindia.alternatives import find_alternatives
    from recallindia.checker import check_product
    from recallindia.openfoodfacts import product_from
    rb = gs.rulebook
    scanned = {"barcode": "1", "product_name": "Cream biscuit", "brand": "A", "ingredients_text": "Wheat flour, Sugar, Colour (INS 124)",
               "additive_codes": ["124"], "category": "7", "categories_tags": ["en:snacks", "en:biscuits", "en:cream-biscuits"]}
    result = check_product(rb, barcode="1", fetch_product=lambda _: scanned)
    pool = [
        {"code": "2", "product_name": "Plain biscuit", "brands": "B", "ingredients_text": "Wheat flour, Sugar, Palm oil, Salt"},
        {"code": "3", "product_name": "Red biscuit", "brands": "C", "ingredients_text": "Wheat flour, Ponceau 4R", "additives_tags": ["en:e124"]},
        {"code": "4", "product_name": "Mystery biscuit", "brands": "D"},
        {"code": "5", "product_name": "Bromate bread", "brands": "E", "ingredients_text": "Wheat flour, Potassium bromate"},
        {"code": "6", "product_name": "Recalled biscuit", "brands": "Acme", "ingredients_text": "Wheat flour, Sugar"},
        {"code": "1", "product_name": "Cream biscuit", "brands": "A", "ingredients_text": "Wheat flour"},
    ]
    searched = []
    def search(tag):
        searched.append(tag)
        return [product_from(p) for p in pool]
    def recalls(product_norm, brand_norm):
        if "acme" in brand_norm or "recalled" in product_norm:
            return [{"recall_id": "R1", "product_name": "Recalled biscuit", "brand": "Acme", "brand_norm": "acme",
                     "product_norm": "recalled biscuit", "fbo_norm": "", "batch_no": None, "reason": "x"}]
        return []
    out = find_alternatives(rb, scanned, result, search, recall_lookup=recalls)
    assert [a["barcode"] for a in out["items"]] == ["2"]
    assert out["items"][0]["notes"] == ["Doesn't list Ponceau 4R (INS 124)", "No open FSSAI recall"]
    assert searched[0] == "en:cream-biscuits"  # most specific category first

def test_recall_alternatives_other_brands_without_recalls():
    from recallindia.alternatives import find_recall_alternatives, recall_query
    from recallindia.openfoodfacts import category_tags_for, product_from
    assert recall_query({"brand": "Eastern - CHILLI POWDER", "product_name": "Chillies and Capsicum"}) == "chilli powder"
    assert recall_query({"brand": "Ramdev", "product_name": "x"}, "RAMDEV HING 50G") == "hing"
    assert category_tags_for("garam masala") == ["en:masalas"] and category_tags_for("hing") == ["en:spices"]
    pool = [product_from(p) for p in [
        {"code": "1", "product_name": "Mixed Masala", "brands": "Ramdev Foods"},                      # recalled brand
        {"code": "2", "product_name": "Garam Masala", "brands": "MDH", "categories_tags": ["en:masalas"]},
        {"code": "3", "product_name": "Kitchen Masala", "brands": "Badbrand"},                         # has a recall
        {"code": "4", "product_name": "Milk Chocolate", "brands": "Cadbury"},                          # not similar
        {"code": "5", "product_name": "Mixed Masala"},                                                 # no brand
        {"code": "6", "product_name": "Sabzi Masala", "brands": "Everest"},
    ]]
    def recalls(product_norm, brand_norm):
        if "badbrand" in brand_norm:
            return [{"recall_id": "R9", "product_name": "Kitchen Masala", "brand": "Badbrand", "brand_norm": "badbrand",
                     "product_norm": "kitchen masala", "fbo_norm": "", "batch_no": None, "reason": "x"}]
        return []
    searched = []
    out = find_recall_alternatives({"recall_id": "256", "brand": "Ramdev", "product_name": "Mixed Masala"},
                                   lambda q: searched.append(q) or pool, recall_lookup=recalls, query="Mixed Masala")
    assert searched == ["mixed masala"]
    assert [a["barcode"] for a in out["items"]] == ["2", "6"]
    assert out["items"][0]["notes"] == ["No FSSAI recall on record", "Different brand"]

def test_alternatives_only_when_something_is_flagged(gs):
    from recallindia.alternatives import find_alternatives
    from recallindia.checker import check_product
    ok = {"barcode": "9", "product_name": "Atta", "ingredients_text": "Whole wheat flour", "categories_tags": ["en:flours"]}
    res = check_product(gs.rulebook, barcode="9", fetch_product=lambda _: ok)
    assert find_alternatives(gs.rulebook, ok, res, lambda t: [])["reason"] == "no_flags"

# ---------- sign-in ----------

def test_session_tokens_round_trip_and_reject_tampering():
    from recallindia import auth
    tok = auth.make_session("g123")
    assert auth.read_session(tok) == "g123"
    body, sig = tok.split(".")
    forged = auth._b64(b'{"u":"g999","exp":9999999999}') + "." + sig
    assert auth.read_session(forged) is None
    assert auth.read_session(tok, now=time.time() + 86400 * 400) is None  # expired
    assert auth.read_session("garbage") is None and auth.read_session(None) is None
    assert auth.bearer("Bearer abc") == "abc" and auth.bearer("Basic abc") is None

def test_google_claims_are_checked():
    from recallindia import auth
    good = {"aud": "cid", "iss": "https://accounts.google.com", "exp": str(int(time.time()) + 300),
            "email_verified": "true", "sub": "42", "email": "a@b.com"}
    assert auth.check_google_claims(good, client_id="cid")["sub"] == "42"
    for bad in ({"aud": "other"}, {"iss": "evil.com"}, {"exp": "1"}, {"email_verified": "false"}, {"sub": ""}):
        with pytest.raises(auth.AuthError):
            auth.check_google_claims({**good, **bad}, client_id="cid")
    assert auth.google_user_id(good) == "g42"

# ---------- phone notifications ----------

def test_vapid_keys_have_browser_format():
    import base64
    from cryptography.hazmat.primitives.asymmetric import ec
    from recallindia.push import generate_vapid_keys
    public, private = generate_vapid_keys()
    pub = base64.urlsafe_b64decode(public + "=" * (-len(public) % 4))
    priv = base64.urlsafe_b64decode(private + "=" * (-len(private) % 4))
    assert len(pub) == 65 and pub[0] == 4 and len(priv) == 32
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), pub)  # a real point on P-256

def test_notification_text_from_alert():
    from recallindia.alerts import build_message
    from recallindia.push import notification_for
    subject, body = build_message(_alert_row())
    n = notification_for({"id": 9, "subject": subject, "body": body})
    assert n["title"] == "Recall: Ramdev - Compounded Asafoetida"
    assert n["body"].startswith("Check the batch or lot number on your pack")
    assert n["url"] == "/app/#/alerts" and n["tag"] == "alert-9"

def test_push_is_a_no_op_without_keys():
    from recallindia import push
    assert push.send_to_user(None, "ritu", {"title": "x"}) == 0 or push.PUSH_ENABLED
