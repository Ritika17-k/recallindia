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
# Confidence bands. A match at or above AUTO_PUBLISH_SCORE is shown to the user and alerted straight away.
# A match from REVIEW_MIN_SCORE up to AUTO_PUBLISH_SCORE goes to the manual review queue and is never shown
# until a reviewer approves it. Anything lower is discarded. Both values were chosen on the dev set
# (data/eval/real_*.csv): 65 is the first score above the 60 caps, 85 is where precision stops rising.
AUTO_PUBLISH_SCORE = float(os.environ.get("AUTO_PUBLISH_SCORE", "85"))
REVIEW_MIN_SCORE = float(os.environ.get("REVIEW_MIN_SCORE", "65"))
MATCH_THRESHOLD = REVIEW_MIN_SCORE

# Alerts
ALERT_CHANNEL = os.environ.get("ALERT_CHANNEL", "outbox")  # outbox | email | console
OUTBOX_DIR = DATA / "outbox"
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
ALERT_FROM = os.environ.get("ALERT_FROM", "alerts@recallindia.local")
DEFAULT_USER_ID = "demo"

# Intake
GMAIL_CREDENTIALS = os.environ.get("GMAIL_CREDENTIALS", str(ROOT / "credentials.json"))
GMAIL_TOKEN = os.environ.get("GMAIL_TOKEN", str(ROOT / "token.json"))
TESSERACT_CMD = os.environ.get("TESSERACT_CMD", "")

# Regulators. Phase 1 ships FSSAI; CDSCO and BIS are Phase 2.
SOURCES = [
    {"code": "FSSAI", "name": "FSSAI FoSCoS Food Recall portal", "url": "https://foscos.fssai.gov.in/food-recall",
     "status": "live", "phase": 1},
    {"code": "CDSCO", "name": "CDSCO monthly drug alert lists (PDF)", "url": "https://cdsco.gov.in/",
     "status": "planned", "phase": 2},
    {"code": "BIS", "name": "BIS product notices", "url": "https://www.bis.gov.in/", "status": "planned", "phase": 2},
]

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
