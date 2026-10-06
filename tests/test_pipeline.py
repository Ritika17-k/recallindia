from datetime import date

import pandas as pd

from recallindia.clean import canonical_header, clean_purchases, clean_recalls, normalize_name, parse_date
from recallindia.config import SAMPLES_DIR
from recallindia.matching import evaluate, match_all, score_labeled_pairs, score_pair
from recallindia.quality import profile, validate
from recallindia.scraper import records_from_json


def _read(name):
    return pd.read_csv(SAMPLES_DIR / name, dtype=str, keep_default_na=False)


def _raw(**overrides):
    row = {"Recall No.": "R-1", "Product Name": "Peanut Chikki 200g", "Brand Name": "Acme", "Batch No.": "b12",
           "Licence No.": "12345678901234", "Reason for Recall": "Allergen", "Date of Recall": "10/02/2025",
           "Mfg. Date": "01/01/2025", "Expiry Date": "01/07/2025", "Status": "ongoing"}
    row.update(overrides)
    return pd.DataFrame([row])


def test_headers_map_to_canonical_columns():
    assert canonical_header("Recall No.") == "recall_id"
    assert canonical_header("Name of FBO") == "fbo_name"
    assert canonical_header("Reason for Recall") == "reason"
    assert canonical_header("Mfg. Date") == "manufacture_date"
    assert canonical_header("Something else") is None


def test_dates_parse_day_first_and_iso():
    assert parse_date("03/04/2025") == date(2025, 4, 3)
    assert parse_date("2025-04-03") == date(2025, 4, 3)
    assert parse_date("03-Apr-2025") == date(2025, 4, 3)
    assert parse_date("31/02/2025") is None
    assert parse_date("N/A") is None


def test_normalize_name_strips_units_and_case():
    assert normalize_name("Roasted Peanut CHIKKI 200g") == "roasted peanut chikki"
    assert normalize_name("Mango Drink 1.2L") == "mango drink"
    assert normalize_name(None) == ""


def test_clean_recalls_standardizes_values():
    df, report = clean_recalls(_raw(**{"Status": "ONGOING", "Batch No.": " b12 ", "Brand Name": "n/a"}))
    row = df.iloc[0]
    assert row["recall_id"] == "R-1"
    assert row["batch_no"] == "B12"
    assert row["status"] == "Ongoing"
    assert row["brand"] is None
    assert row["recall_date"] == date(2025, 2, 10)
    assert report["unmapped_columns"] == []


def test_clean_recalls_removes_duplicate_ids_keeping_latest():
    raw = pd.concat([_raw(**{"Status": "Ongoing"}), _raw(**{"Recall No.": " r-1", "Status": "Completed",
                                                            "Date of Recall": "20/02/2025"})])
    df, report = clean_recalls(raw)
    assert len(df) == 1
    assert report["duplicates_removed"] == 1
    assert df.iloc[0]["status"] == "Completed"


def test_sample_data_cleans_and_reports_issues():
    df, report = clean_recalls(_read("sample_recalls.csv"))
    assert report["duplicates_removed"] == 3
    assert df["recall_id"].is_unique
    assert len(report["unparseable_dates"]) == 1
    val = validate(df, today=date(2026, 10, 5))
    rules = {e["rule"] for e in val["errors"]} | {w["rule"] for w in val["warnings"]}
    assert "recall_date is required" in rules
    assert "license_no is not 14 digits" in rules
    assert profile(df)["rows"] == len(df)


def test_validate_flags_bad_rows():
    df, _ = clean_recalls(_raw(**{"Date of Recall": "01/01/2030", "Expiry Date": "01/12/2024"}))
    val = validate(df, today=date(2026, 1, 1))
    errors = {e["rule"] for e in val["errors"]}
    assert not val["passed"]
    assert "recall_date must not be in the future" in errors
    assert "expiry_date must be after manufacture_date" in errors


def test_score_pair_rules():
    recall = {"product_norm": "roasted peanut chikki", "brand_norm": "acme", "batch_no": "B12",
              "manufacture_date": date(2025, 1, 1), "expiry_date": date(2025, 7, 1)}
    good = {"product_norm": "peanut chikki roasted", "brand_norm": "acme", "batch_no": "B12",
            "purchase_date": date(2025, 3, 1)}
    assert score_pair(recall, good)[0] == 100.0
    assert score_pair(recall, {**good, "batch_no": "B13"})[0] <= 60.0
    assert score_pair(recall, {**good, "purchase_date": date(2024, 12, 1)})[0] == 0.0
    assert score_pair(recall, {**good, "purchase_date": date(2025, 9, 1)})[0] == 50.0
    no_batch = score_pair(recall, {**good, "batch_no": None})
    assert no_batch[0] == 100.0 and "batch" not in no_batch[1]


def test_matching_on_sample_data():
    recalls, _ = clean_recalls(_read("sample_recalls.csv"))
    purchases = clean_purchases(_read("sample_purchases.csv"))
    labels = _read("labeled_pairs.csv")
    matches = match_all(recalls.set_index("recall_id", drop=False), purchases, threshold=85)
    assert not matches.empty
    results, best = evaluate(score_labeled_pairs(recalls, purchases, labels), labels)
    assert len(results) == 10
    assert best["f1"] >= 0.9


def test_evaluate_counts():
    labels = pd.DataFrame({"purchase_id": ["a", "b", "c"], "recall_id": ["x", "x", "x"], "is_match": ["1", "0", "1"]})
    scores = pd.DataFrame({"purchase_id": ["a", "b", "c"], "recall_id": ["x", "x", "x"], "score": [90, 80, 40]})
    results, _ = evaluate(scores, labels, thresholds=[75])
    row = results.iloc[0]
    assert (row["tp"], row["fp"], row["fn"]) == (1, 1, 1)
    assert row["precision"] == 0.5 and row["recall"] == 0.5


def test_records_from_json_finds_nested_recall_lists():
    payloads = [
        {"url": "a", "status": 200, "body": {"menu": [{"title": "Home", "link": "/"}]}},
        {"url": "b", "status": 200, "body": {"data": {"items": [
            {"recallNo": "1", "productName": "Tea", "brandName": "X", "recallDate": "01/01/2025"}]}}},
    ]
    rows = records_from_json(payloads)
    assert len(rows) == 1 and rows[0]["productName"] == "Tea"


def test_foscos_headers_and_reason_split():
    raw = pd.DataFrame([{"Sr.No": "1", "Recall Id": "266", "FBO Name": "Example Foods", "Brand Name": "NIL",
                         "Batch/ Lot No.": "556/26", "Product": "half cooked poori",
                         "Reason For Recall": "Unsafe - sorbate exceeds limit", "Recall Start Date": "02-09-2026",
                         "Recall Status": "Initiated", "Recall Termination Date": "NA",
                         "License No.": "11323007000355", "License Type\n[Central/State/Registration]": "Central License",
                         "Nature of Recall": "Initiated by Authority"}])
    df, report = clean_recalls(raw)
    row = df.iloc[0]
    assert report["unmapped_columns"] == ["Sr.No"]
    assert row["recall_type"] == "Unsafe" and row["reason"] == "sorbate exceeds limit"
    assert row["recall_date"] == date(2026, 9, 2) and row["termination_date"] is None
    assert row["brand"] is None and row["batch_no"] == "556/26"
    assert row["license_type"] == "Central License" and row["recall_class"] == "Initiated by Authority"


def test_reason_tags_type_and_redaction():
    from recallindia.clean import infer_type, is_placeholder_reason, redact, tag_hazards
    assert tag_hazards("Unsafe - PRESENCE OF CHLORPYRIFOS") == "pesticide residue"
    assert tag_hazards("presence of tartrazine") == "synthetic colour"
    assert tag_hazards("sorbate exceeds limit") == "preservative"
    assert tag_hazards("Self Recall") is None
    assert infer_type("The Sample is Declared Unsafe Food") == "Unsafe"
    assert infer_type("other") is None
    assert is_placeholder_reason("other") and is_placeholder_reason("d") and not is_placeholder_reason("Larvae found")
    assert redact("Mob. 9876543210 e-mail- someone@example.com") == "Mob. [phone removed] e-mail- [email removed]"
    assert redact("Licence 12722025000499") == "Licence 12722025000499"


def _recall(brand, product, fbo, batch=None):
    return {"brand_norm": normalize_name(brand), "product_norm": normalize_name(product),
            "fbo_norm": normalize_name(fbo), "batch_no": batch}


def _purchase(product, brand="", batch=None):
    return {"product_norm": normalize_name(product), "brand_norm": normalize_name(brand), "batch_no": batch}


def test_foscos_style_matching():
    eastern = _recall("Eastern - CHILLI POWDER", "Chillies and Capsicum (Lal Mirchi)", "FAMILY SUPERMARKET", "AC25L10A1")
    assert score_pair(eastern, _purchase("EASTERN CHILLI PWD 250G"))[0] >= 75
    assert score_pair(eastern, _purchase("Eastern Chilli Powder", "Eastern", "AC25-L10A1"))[0] == 100.0
    assert score_pair(eastern, _purchase("Eastern Chilli Powder", "Eastern", "AC24X01B2"))[0] <= 60
    assert score_pair(eastern, _purchase("Catch Red Chilli Powder", "Catch"))[0] <= 50
    ramdev = _recall("Ramdev", "Compounded Asafoetida", "RAMDEV FOOD PRODUCTS PVT. LTD.")
    assert score_pair(ramdev, _purchase("Ramdev Hing 50g", "Ramdev"))[0] >= 75
    assert score_pair(ramdev, _purchase("Ramdev Kasuri Methi", "Ramdev"))[0] <= 60
    chutney = _recall("Chilli Garlic Chutney", "Fruits and Vegetable Chutney", "Chokhi Dhani Resort Pvt Ltd")
    assert score_pair(chutney, _purchase("Chokhi Dhani Chilli Garlic Chutney"))[0] >= 75
    mixture = _recall("bombay mixture", "Cereal or pulses flour/ Starch based snacks", "ANNAPOORNA TRADERS")
    assert score_pair(mixture, _purchase("Haldiram's Bombay Mix", "Haldiram's"))[0] <= 50
