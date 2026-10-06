import pytest

from recallindia.checker import ProductNotFound, check_product
from recallindia.ingredients import extract_codes, parse
from recallindia.openfoodfacts import additive_codes, guess_category, parse_product
from recallindia.rulebook import RuleBook, category_applies

LABEL = ("Ingredients: Wheat Flour (Atta) (62%), Sugar, Raising Agents [INS 500(ii), INS 503(ii)], "
         "Emulsifier (322), Colours (INS 102, 110), Contains Permitted Synthetic Food Colour, Potassium Bromate")


@pytest.fixture(scope="module")
def rb():
    return RuleBook.from_csv()


def test_extract_codes_handles_label_styles():
    assert extract_codes("Raising Agents [INS 500(ii), INS 503(ii)]") == ["500", "503"]
    assert extract_codes("Colours (INS 102, 110)") == ["102", "110"]
    assert extract_codes("Acidity regulator (E330)") == ["330"]
    assert extract_codes("Emulsifiers (322(i), 476)") == ["322", "476"]
    assert extract_codes("Vitamin C (300mg)") == []
    assert extract_codes("Milk solids (2.5%)") == []


def test_parse_splits_top_level_only():
    items = parse(LABEL)
    assert [i["name"] for i in items][:3] == ["wheat flour", "sugar", "raising agents"]
    assert items[0]["sub_names"] == ["atta"]
    assert items[2]["is_class"]
    assert any(i["notes"] for i in items)


def test_category_scope():
    assert category_applies("ALL", None)
    assert category_applies("13", "13.2")
    assert not category_applies("13.1", "13.2")
    assert not category_applies("1", "13.1")


def test_check_flags_prohibited_and_restricted(rb):
    result = rb.check(LABEL, "7")
    status = {f["substance"]: f["status"] for f in result["findings"]}
    assert result["verdict"] == "avoid"
    assert status["Potassium bromate"] == "prohibited"
    assert status["Tartrazine"] == "restricted"
    assert status["Lecithins"] == "ok"
    assert result["findings"][0]["severity"] == 3
    assert "wheat flour" not in status


def test_category_specific_rule_applies_to_infant_food(rb):
    assert rb.check("Rice flour, Sucralose", "13.2")["verdict"] == "avoid"
    assert rb.check("Rice flour, Sucralose", "14")["verdict"] == "no issues found"


def test_misspelled_names_still_match(rb):
    finding = rb.check("Potasium Bromate", None)["findings"][0]
    assert finding["substance"] == "Potassium bromate"
    assert finding["matched_by"].startswith("similar name")


def test_short_words_do_not_fuzzy_match(rb):
    assert rb.find_by_name("salt") == (None, None)


def test_off_payload_parsing():
    payload = {"status": 1, "product": {"code": "8901234567890", "product_name": "Glucose Biscuits",
                                        "brands": "Example, Other", "categories_tags": ["en:snacks", "en:biscuits"],
                                        "ingredients_text": "Wheat flour, sugar, INS 503(ii)",
                                        "additives_tags": ["en:e503ii", "en:e322i", "en:e924a"]}}
    p = parse_product(payload)
    assert p["brand"] == "Example"
    assert p["category"] == "7"
    assert p["additive_codes"] == ["503", "322", "924a"]
    assert parse_product({"status": 0}) is None
    assert guess_category(["en:baby-foods"]) == "13.2"
    assert additive_codes(["en:e150d"]) == ["150d"]


def test_check_product_with_barcode_and_recalls(rb):
    product = {"product_name": "Glucose Biscuits", "brand": "Example", "ingredients_text": "Wheat flour, sugar",
               "additive_codes": ["924a"], "category": "7", "source": "Open Food Facts"}
    recall = {"recall_id": "R1", "product_name": "Glucose Biscuits 100g", "product_norm": "glucose biscuits",
              "brand": "Example", "brand_norm": "example", "reason": "Foreign matter", "recall_date": None}
    result = check_product(rb, barcode="8901234567890", fetch_product=lambda b: product,
                           recall_lookup=lambda p, b: [recall])
    assert result["category"] == "7"
    assert result["findings"][0]["substance"] == "Potassium bromate"
    assert result["recalls"][0]["recall_id"] == "R1"


def test_check_product_errors(rb):
    with pytest.raises(ProductNotFound):
        check_product(rb, barcode="8901234567890", fetch_product=lambda b: None)
    with pytest.raises(ProductNotFound):
        check_product(rb)
