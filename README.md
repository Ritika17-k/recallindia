# RecallIndia

RecallIndia is a consumer platform that collects official Indian product recall and ban notices in one place and alerts people when something they have bought is affected.

Today these notices are scattered across regulator websites, none of which offers an API, and no Indian consumer app matches them against what people actually buy.

## Problem

- **Fragmented sources.** FSSAI's FoSCoS Food Recall portal, CDSCO's monthly PDF alert lists and BIS's individual web notices are all published separately. FoSCoS offers a paged table and an Excel export, not an API.
- **No personalisation.** Consumers have to check each source themselves and recognise their own products in it.
- **Market gap.** Global aggregators such as RecallScope and OECD GlobalRecalls exclude India. US apps such as Whystle and RecallSentry show the model works there.

## Solution

```
 FSSAI FoSCoS ──► scraper ──► clean + validate ──┐
 (CDSCO, BIS: Phase 2)                            ▼
                                            PostgreSQL (pg_trgm)
 Gmail order emails ──┐                           │
 receipt photos (OCR) ┼──► purchases ────────────►│
 manual entry ────────┘                           ▼
                                  rule-based matcher (trigram + weighted score)
                                                  │
                         ┌──────── score ≥ 85 ────┴──── 65 – 85 ────────┐
                         ▼                                               ▼
                   auto-published                              manual review queue
                         │                                     (never shown until approved)
                         ▼                                               │ approve
                      alerts ◄───────────────────────────────────────────┘
```

Alongside this, a **Global Regulatory Status** lookup shows how an ingredient is regulated in India compared with the EU and the US, framed as each regulator's published status and never as a claim about a brand or product.

## MVP scope

| Item | Status |
|---|---|
| Phase 1: FSSAI recalls | Done. 236 real FoSCoS recalls scraped and cleaned (5 Oct 2026) |
| Phase 2: CDSCO and BIS | Planned. Each recall carries a `source` column, and `GET /sources` lists the planned sources |
| Matching engine | Rule-based on purpose: PostgreSQL trigram search for candidates, then weighted brand/product/batch scoring with caps. No ML |
| Evaluation | 142 hand-labelled receipt lines (127 true matches) across a dev set and a separate holdout set. See [Measured accuracy](#measured-accuracy) |

## Core features

| Feature | Where | How to use it |
|---|---|---|
| Aggregation of FSSAI notices | `scraper`, `clean`, `quality` | `discover`, `scrape`, `clean`, `report` |
| Fuzzy matching against purchase history | `matching`, `db` | `match`, or `run` for the full refresh |
| Manual review queue for medium-confidence matches | `review`, `db`, `/admin/review` page | `review list / approve / reject` |
| Alerts for matched recalls | `alerts`, `pipeline` | `alerts`, or automatically in `run`, after approval, and on import |
| Gmail order-history import | `email_import` | `import-email takeout.mbox` or `import-email --gmail` |
| Receipt OCR | `receipts` | `import-receipt photo.jpg` |
| Global Regulatory Status | `global_status`, `data/reference/global_status.csv` | `global-status 127`, `global-status --divergent` |
| Label and barcode check (extra) | `checker`, `rulebook`, `openfoodfacts` | `check --barcode …` or `check --text …` |

### Review queue

Every match gets a status from its score:

| Score | Status | What the user sees |
|---|---|---|
| 85 and above | `auto_published` | The match, and an alert |
| 65 to 85 | `pending_review` | Nothing, until a reviewer approves it |
| Below 65 | discarded | Nothing |

A reviewer approves (`approved`, alert sent) or rejects (`rejected`, never shown) each pending match from the command line or at http://127.0.0.1:8000/admin/review. Re-running the matcher refreshes scores but never undoes a decision, and never withdraws a match the user has already been alerted about. Both thresholds were set on the dev set only. 65 is the first score above the 60 caps for "batch differs" and "different product", and 85 is where precision stops rising. They can be changed in `.env` (`AUTO_PUBLISH_SCORE`, `REVIEW_MIN_SCORE`).

### Alerts

An alert is created once per visible match (auto-published or approved) for users who have alerts turned on. It states what the notice says: the product, the recall ID, the date, the reason, the batch to compare, and a link to the notice. When FoSCoS gives no usable reason (38 notices just say "3", "Others" and similar), the alert says so rather than printing the placeholder. Channels:

- `outbox` (default): writes each alert as an `.eml` file in `data/outbox`, which you can open in any mail app
- `email`: sends through SMTP. Set `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD` and `ALERT_FROM` in `.env`; for Gmail, use an app password
- `console`: prints alerts

### Purchase intake

Order emails and receipts go through the same path: the lines are parsed into purchases, saved with stable IDs (importing the same email twice adds nothing), matched only against those new lines, and alerted right away if there's a high-confidence match.

- **Order emails.** Detects order mail from Amazon, Flipkart, BigBasket, Blinkit, Zepto, Swiggy Instamart, JioMart, DMart Ready and others by sender, skips promotions, and reads item lines from HTML tables, "2 x Item ₹54" lines and "Item… Quantity: 1" blocks. Delivery fees, taxes and totals are dropped. Two ways in:
  - A Google Takeout export: at takeout.google.com choose Mail only, then run `import-email path\to\All mail.mbox`. Nothing extra to install.
  - The Gmail API (read-only): `pip install -r requirements-intake.txt`, create an OAuth client of type "Desktop app" in Google Cloud Console with the Gmail API enabled, save it as `credentials.json` in the project folder, then run `import-email --gmail`. A browser window asks for consent the first time.
- **Receipts.** Tesseract OCR with upscaling and contrast correction, then a parser that finds the shop name, the date and the item lines between the "Item … Qty … Amount" header and the total, removing HSN codes, quantities and prices.

Retailers change their email layouts, so run with `--dry-run` first. It prints the lines and writes them to `data/intake/` without saving.

### Global Regulatory Status

`data/reference/global_status.csv` has one row per ingredient and jurisdiction (EU, US, California) with the status, a one-line note, the source, a link, the date checked and a `verified` flag. India's status always comes from the FSSAI rulebook in `rules.csv`, so there is a single source of truth for Indian rules.

```
python -m recallindia global-status --divergent
```

lists the ingredients India allows that another regulator does not. Today that's erythrosine (INS 127), Ponceau 4R (124) and carmoisine (122). An ingredient with no FSSAI rule loaded, or only a partial one (for example "not permitted in infant food"), is never presented as allowed in India.

How this stays legally safe:
- The lookup takes an ingredient (INS/E number or name) and nothing else. There is no brand or product input.
- Each row states the regulator's own status with a link to it, and the output always carries the disclaimer.
- Rows checked against an official regulator page are marked `verified=yes`: the EU titanium dioxide ban, FDA's Red No. 3 revocation, the BVO revocation, the cyclamate prohibition and FDA's food colour list. The others are marked `no` until checked.

## Measured accuracy

`python -m recallindia evaluate` runs the full pipeline (candidate search, then scoring) over all 236 real recalls for two hand-labelled sets:

- **Dev set** (`data/eval/real_*.csv`): 72 receipt lines, 71 true matches. These lines were written while the matcher was being built.
- **Holdout set** (`data/eval/holdout_*.csv`): 70 lines, 56 true matches, written and labelled *before* the matcher was run on them, and not used for any tuning. It includes 17 hard negatives (same brand with a different product, same product from another brand, a different batch, a different variant) and 3 receipt-style abbreviations ("RMDEV MIX MSALA 50G"). Labels were completed by keyword search over all recalls, not by the scorer.

| Threshold | Dev precision | Dev recall | Holdout precision | Holdout recall |
|---|---|---|---|---|
| 60 | 0.68 | 1.00 | 0.82 | 0.98 |
| 65–80 | 0.99 | 0.97 | 0.94 | 0.88 |
| 85 | 0.98 | 0.86 | 0.98 | 0.86 |
| 90 | 1.00 | 0.73 | 1.00 | 0.79 |

The gap between dev and holdout at 65–80 (0.99 to 0.94 precision) is what the review queue is for. With the two bands, on the **holdout**:

| | Holdout |
|---|---|
| Auto-published | 49 matches at **0.98 precision**, 0.86 recall |
| Sent to review | 3 matches (1 true, 2 false) |
| Recall once reviewed | 0.88 |
| Missed | 7 of 56 |

So a user is shown a wrong match about 1 time in 50, and the reviewer's queue is small.

The holdout errors are worth reading:
- **6 of the 7 misses** have a full brand match, but the product score falls under the 60 cap. In 5 of them, FoSCoS describes the product only by its food category ("Non-carbonated Water Based Beverages", "Culinary Pastes", "Pickles"), and 3 of those also have the same batch. In 1, the receipt abbreviates the product ("RMDEV MIX MSALA"). The 7th abbreviates the brand too ("VPR GLD COCNT OIL"). A rule that sends "brand and batch agree, product differs" to review would catch 3 of them. It was found by looking at the holdout, though, so it needs a fresh test set before it can be claimed as an improvement.
- **The 1 auto-published false positive** is Everest Kashmiri Lal chilli powder matched to a recall of Everest's Tikhalal chilli powder, a different variant from the same brand.

Real receipt lines from Gmail and OCR imports are the best next test set.

### Match scoring

FoSCoS stores a food *category* in its Product column (for example "Chillies and Capsicum (Lal Mirchi)"). The actual product name usually sits in the Brand column ("Eastern - CHILLI POWDER"), and the FBO is often the shop that sold it. So a receipt line is compared with brand, product and FBO together.

1. **Candidates:** PostgreSQL trigram word similarity (`<%` on a GIN index) finds up to 25 recalls whose brand, product and FBO text resembles the receipt line.
2. **Brand (40%):** the share of distinctive brand words found on the other side, checked both ways. Company suffixes (Pvt, Ltd, Foods, Traders) and product words (chilli, atta, ghee, bombay mixture) don't count as brand words. Spellings within 85% similarity count as the same word, so "Chaki" matches "Chakki".
3. **Product (40%):** token set similarity of what's left after removing the shared brand words. Common Indian food words are mapped to one form first: hing → asafoetida, haldi → turmeric, jeera → cumin, rava/suji → semolina, mirchi → chilli, dahi → curd.
4. **Batch (20%):** compared after removing spaces, punctuation and "Batch No." prefixes. Placeholders such as NIL, LOOSE or 0 are ignored.

If a part is missing, its weight is shared among the others. Then these caps apply:

| Condition | Score capped at |
|---|---|
| Brand words don't match (under 50) | 50 |
| Product doesn't match (under 60) | 60 |
| Batch numbers differ | 60 |
| Neither side has a usable brand | 70 (so it always goes to review) |
| Bought before the batch was made | 0 |
| Bought after the batch expired | score halved |

## Recall data pipeline

| Stage | Module | What it does |
|---|---|---|
| Discover | `scraper.discover` | Opens the portal, finds the Food Recall section, saves screenshots, HTML, every JSON response and a summary |
| Scrape | `scraper.scrape` | Reads recall tables across every page and captures the JSON the page loads, keeping whichever gives more rows |
| Clean | `clean` | Maps portal headers to standard columns, turns `N/A`/`-`/`nil` into nulls, parses dates, normalises IDs, batches and licence numbers, tags hazards, removes phone numbers and emails from reasons, and removes duplicate recall IDs |
| Validate | `quality` | Profiles every column and checks the rules below, writing `data/reports/quality_report.md` |
| Load | `db` | Creates tables and GIN trigram indexes, upgrades older databases in place, upserts recalls and records when each was first seen |
| Refresh | `pipeline.refresh` | Loads recalls, rematches every purchase, queues and sends alerts, and logs the run (new recalls, auto-published, sent to review, alerts sent) |

| Level | Validation rule |
|---|---|
| Error | `recall_id`, `product_name` and `recall_date` are present; `recall_id` is unique; `recall_date` is not in the future; `expiry_date` is after `manufacture_date` |
| Warning | `recall_date` is before `manufacture_date`; licence number is not 14 digits; brand, batch, reason or status is missing; reason is a placeholder or names no recognised hazard |

## Setup on Windows

1. **Install Python** 3.11 or newer from python.org and tick "Add python.exe to PATH".
2. **Install PostgreSQL 16** from the EDB installer at postgresql.org/download/windows. Note the password for the `postgres` user. `pg_trgm` is included.
3. **Install Tesseract** for receipt OCR, using the UB Mannheim installer from github.com/UB-Mannheim/tesseract/wiki. If it isn't on PATH, set `TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe` in `.env`.
4. **Set up the project.** In PowerShell, from the project folder:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
copy .env.example .env
```

   If PowerShell blocks the activate script, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once. For Gmail API import, also run `pip install -r requirements-intake.txt`.

5. **Point the project at your database.** Put your `postgres` password into `DATABASE_URL` in `.env`.
6. **Create the database.** In "SQL Shell (psql)" or pgAdmin: `CREATE DATABASE recallindia;`

`load`, `run` and `load-rules` create the extension, tables and indexes, and bring a database made by an earlier version up to date.

## Running it

### First run

```powershell
python -m recallindia scrape                 # FSSAI recalls from FoSCoS
python -m recallindia clean
python -m recallindia report                 # data quality report
python -m recallindia load-rules             # FSSAI rules and global status
python -m recallindia users add ritu --email you@example.com --name Ritu
python -m recallindia import-email "All mail.mbox" --user ritu
python -m recallindia import-receipt receipt.jpg --user ritu
python -m recallindia review list
python -m recallindia review approve 12 --note "same brand and batch"
python -m recallindia serve                  # API at http://127.0.0.1:8000/docs
```

### Keeping it current

```powershell
python -m recallindia run --scrape
```

scrapes FoSCoS, loads the recalls (counting the new ones), rematches every purchase, fills the review queue and sends alerts. To run it every morning, add a Windows Task Scheduler task that runs `.venv\Scripts\python.exe -m recallindia run --scrape` in the project folder.

### Without a database

```powershell
python -m recallindia clean --sample
python -m recallindia match --memory         # writes data/reports/matches.csv and review_queue.csv
python -m recallindia evaluate               # dev and holdout sets
python -m recallindia global-status titanium dioxide
python -m recallindia import-email tests\fixtures\orders.mbox --dry-run
python -m recallindia import-receipt tests\fixtures\receipt_kirana.png --dry-run
pytest
```

Set `TEST_DATABASE_URL` to an empty test database to include the PostgreSQL integration test in `pytest`.

### All commands

| Command | Use |
|---|---|
| `discover`, `scrape`, `clean`, `report` | Collect, clean and check FSSAI recall data |
| `load` | Load cleaned recalls (and a purchases CSV, owned by `--user`) |
| `match` | Match all purchases and fill the review queue. `--memory` works from the cleaned files |
| `run [--scrape] [--no-send]` | Full refresh |
| `review list`, `review approve ID…`, `review reject ID…` | Work the review queue. Approving sends the alert unless `--no-send` |
| `alerts [--channel outbox\|email\|console]` | Queue and send any pending alerts |
| `users add ID --email … --name …`, `users list` | Manage users |
| `import-email PATH`, `import-email --gmail` | Order-email intake. `--dry-run` to preview |
| `import-receipt FILE…` | Receipt OCR intake. `--show-text` prints the OCR text |
| `evaluate`, `evaluate --purchases p.csv --gold g.csv`, `evaluate --sample` | Accuracy reports |
| `global-status QUERY`, `global-status --divergent` | Ingredient status in India, the EU and the US |
| `load-rules`, `check`, `serve` | Reference data, label check, API |

`--purchases` CSVs use the columns `purchase_id, product_name, brand, batch_no, store, purchase_date` (and optionally `user_id`). Dates are day-first, as in `15/06/2025`.

## API

| Method | Path | Returns |
|---|---|---|
| POST | `/users` | Create or update a user `{"user_id", "email", "name", "alerts_enabled"}` |
| POST | `/users/{id}/purchases` | Add purchases (JSON list), match them, send alerts |
| POST | `/users/{id}/receipts` | Upload a receipt photo; OCR, match, alert |
| POST | `/users/{id}/order-emails` | Upload an `.eml` or Takeout `.mbox`; import, match, alert |
| GET | `/users/{id}/matches` | Published matches for the user (never pending ones) |
| GET | `/users/{id}/alerts` | The user's alerts and their delivery status |
| POST | `/match` | High-confidence recalls for one product. `?include_medium=true` adds medium ones, for reviewers |
| GET | `/purchases/{id}/matches` | Published matches for one purchase |
| GET | `/review` | The review queue |
| POST | `/review/{match_id}` | `{"decision": "approve" \| "reject", "note": "..."}` |
| GET | `/admin/review` | Review page |
| POST | `/admin/alerts/send` | Queue and send pending alerts |
| GET | `/recalls?q=…`, `/recalls/{id}` | Recall search and detail |
| GET | `/global-status?q=127`, `/global-status/divergent` | Global Regulatory Status |
| GET | `/sources` | Regulators covered and planned |
| GET | `/stats` | Counts, matches and alerts by status, last refresh, recalls by month |
| POST | `/check` | Label or barcode check |
| GET | `/categories`, `/substances?q=…` | FSSAI food categories and additives |
| POST | `/admin/reload-rules` | Reload reference data after `load-rules` |
| GET | `/health` | Status and recall count |

## Label and barcode check

Beyond the recall alerts, `check` reads a label's ingredient list (typed, or from Open Food Facts by barcode), finds INS/E codes and names, and checks them against FSSAI rules for the product's food category. It returns avoid, caution, check or no issues found, with the rule and source behind each flag, each ingredient's status abroad, and any high-confidence recall matches. The rules in `data/reference/rules.csv` are a starting set; only the potassium bromate ban is marked verified. The next step is importing the full additive tables from the [FSSAI compendium](https://fssai.gov.in/cms/compendium-fss-fps-fa.php).

## Project layout

```
recallindia/
  scraper.py        Playwright discover and scrape (FoSCoS)
  clean.py          normalisation, hazard tags, deduplication
  quality.py        profiling, validation, report
  matching.py       scoring, in-memory candidates, evaluation
  review.py         confidence bands, review queue, review metrics
  alerts.py         alert messages and delivery (outbox, SMTP, console)
  pipeline.py       ingest purchases, full refresh
  intake.py         shared purchase-intake helpers
  email_import.py   order emails from Takeout .mbox, .eml or the Gmail API
  receipts.py       receipt OCR and line parsing
  global_status.py  Global Regulatory Status
  ingredients.py, rulebook.py, checker.py, openfoodfacts.py   label check
  db.py             SQLAlchemy models, migrations, trigram search, review and alert SQL
  api.py            FastAPI app;  static/review.html  review page
  config.py         settings, thresholds, sources
data/reference/     FSSAI categories, substances, rules, global status
data/eval/          dev and holdout labelled receipt lines
data/samples/       synthetic sample data
tests/              pytest suite and fixtures (order emails, receipt)
```

## Status and limits

- **Tested here:** cleaning, validation, matching, banding, review metrics, alert text and delivery, order-email parsing, receipt parsing and OCR, global status, and the evaluation sets (48 tests). The review, alert and migration SQL was run against PostgreSQL 16, including upgrading a database made by the previous version.
- **Needs a first real run:** the FastAPI endpoints and the SQLAlchemy calls around that SQL (run `pytest` with `TEST_DATABASE_URL` set); the Gmail API path, which needs your own OAuth client; and the order-email parser on real emails, since it was built on representative test emails.
- **Data:** FoSCoS gives no manufacture or expiry dates and often no batch, so many matches rest on brand and product alone, and the alert tells the user to check the batch on the pack.
- **Next:** CDSCO monthly PDF alerts (Phase 2), BIS notices, a fresh test set from real imports, the "brand and batch agree" review rule, and a React app.
