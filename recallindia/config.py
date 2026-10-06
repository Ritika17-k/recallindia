import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW_DIR = DATA / "raw"
CLEAN_DIR = DATA / "clean"
REPORT_DIR = DATA / "reports"
DISCOVERY_DIR = DATA / "discovery"
SAMPLES_DIR = DATA / "samples"
REFERENCE_DIR = DATA / "reference"
EVAL_DIR = DATA / "eval"

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5432/recallindia")
FOSCOS_URL = os.environ.get("FOSCOS_URL", "https://foscos.fssai.gov.in/")
RECALL_URL = os.environ.get("RECALL_URL", "https://foscos.fssai.gov.in/food-recall")
HEADLESS = os.environ.get("SCRAPER_HEADLESS", "1") != "0"
MAX_PAGES = int(os.environ.get("SCRAPER_MAX_PAGES", "100"))
MATCH_THRESHOLD = float(os.environ.get("MATCH_THRESHOLD", "75"))

RECALL_COLUMNS = [
    "recall_id", "product_name", "brand", "category", "batch_no", "fbo_name", "license_no", "license_type",
    "recall_type", "reason", "hazard", "recall_class", "recall_date", "termination_date", "manufacture_date", "expiry_date",
    "status", "state",
]

HEADER_ALIASES = {
    "recall_id": ["recall id", "recall no", "recall number", "reference no", "recallid"],
    "product_name": ["product name", "product", "name of product", "food product"],
    "brand": ["brand", "brand name"],
    "category": ["category", "food category", "product category", "category name"],
    "batch_no": ["batch", "batch no", "batch number", "lot no", "lot", "batch/ lot no", "batch/lot no"],
    "fbo_name": ["fbo name", "fbo", "company", "manufacturer", "name of fbo", "business name"],
    "license_no": ["licence", "license", "registration", "licence no", "license no", "licence/registration",
                   "license/registration number"],
    "license_type": ["license type", "licence type"],
    "recall_type": ["recall type", "type of recall"],
    "reason": ["reason", "reason for recall", "recall reason"],
    "hazard": ["hazard", "hazard type"],
    "recall_class": ["class", "recall class", "nature of recall", "initiated by"],
    "recall_date": ["recall date", "date of recall", "recall start date", "start date", "initiated on", "date"],
    "termination_date": ["recall termination date", "termination date", "end date"],
    "manufacture_date": ["manufacture date", "mfg date", "date of manufacture", "manufactured on"],
    "expiry_date": ["expiry date", "exp date", "best before", "use by", "expiry"],
    "status": ["status", "recall status", "current status"],
    "state": ["state", "location", "region"],
}

NULL_TOKENS = {"", "-", "--", "na", "n/a", "nil", "none", "null", "not available", "not applicable", "nan"}
