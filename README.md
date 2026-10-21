# RecallIndia

RecallIndia collects official Indian product recall notices in one place and tells you when something you bought is affected.

Right now these notices are spread across regulator websites. None of them has an API, and no Indian consumer app checks them against what people actually buy. This project is my attempt at that.

## Why I built it

- **The sources are scattered.** FSSAI's FoSCoS Food Recall portal, CDSCO's monthly PDF alert lists and BIS's individual web notices are all published separately. FoSCoS gives you a paged table and an Excel export, not an API.
- **Nothing is personal.** To find out if a recall affects you, you have to check each source yourself and recognise your own products in it.
- **India is missing from the existing tools.** Global aggregators like RecallScope and OECD GlobalRecalls don't cover India. Apps like Whystle and RecallSentry in the US suggest the idea works.

## How it works

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

There is also a **Global Regulatory Status** lookup. It shows how an ingredient is regulated in India compared with the EU and the US. It reports each regulator's published status and makes no claim about any brand or product.

## What's done and what isn't

| Item | Status |
|---|---|
| Phase 1: FSSAI recalls | Done. 236 real FoSCoS recalls scraped and cleaned (5 Oct 2026) |
| Phase 2: CDSCO and BIS | Planned. Every recall has a `source` column, and `GET /sources` lists the planned ones |
| Matching | Rule-based on purpose: PostgreSQL trigram search finds candidates, then a weighted brand/product/batch score with caps. No ML |
| Evaluation | 142 hand-labelled receipt lines (127 true matches) split into a dev set and a separate holdout set. Details in [How accurate is it?](#how-accurate-is-it) |

## What you can do with it

| Feature | Module | Command |
|---|---|---|
| Collect FSSAI notices | `scraper`, `clean`, `quality` | `discover`, `scrape`, `clean`, `report` |
| Match recalls against your purchases | `matching`, `db` | `match`, or `run` for a full refresh |
| Review medium-confidence matches | `review`, `db`, `/admin/review` | `review list / approve / reject` |
| Get alerts | `alerts`, `pipeline` | `alerts`, or automatically in `run`, after approval and on import |
| Import Gmail order history | `email_import` | `import-email takeout.mbox` or `import-email --gmail` |
| Read receipts with OCR | `receipts` | `import-receipt photo.jpg` |
| Look up global ingredient status | `global_status`, `data/reference/global_status.csv` | `global-status 127`, `global-status --divergent` |
| Check a label or barcode | `checker`, `rulebook`, `openfoodfacts` | the app's Scan screen, or `check --barcode …` / `check --text …` |
| Use it as a phone app (PWA) | `static/app/` | `serve`, then open http://127.0.0.1:8000 |
| Suggest alternatives to a recalled product | `alternatives`, `openfoodfacts` | shown on alerts and in My Products. `GET /recalls/{id}/alternatives` |

### The review queue

Each match gets a status based on its score:

| Score | Status | What the user sees |
|---|---|---|
| 85 and above | `auto_published` | The match, and an alert |
| 65 to 85 | `pending_review` | Nothing until a reviewer approves it |
| Below 65 | discarded | Nothing |

A reviewer approves (alert sent) or rejects (never shown) each pending match, either from the command line or at http://127.0.0.1:8000/admin/review. Re-running the matcher refreshes scores but never undoes a decision, and never withdraws a match the user has already been alerted about.

I set both thresholds using the dev set only. 65 is the first score above the 60 caps for "batch differs" and "different product", and 85 is where precision stops rising. You can change them in `.env` (`AUTO_PUBLISH_SCORE`, `REVIEW_MIN_SCORE`).

### Alerts

An alert is created once for each visible match (auto-published or approved), for users who have alerts turned on. It repeats what the notice says: the product, recall ID, date, reason, the batch to compare, and a link to the notice. If FoSCoS gives no usable reason (38 notices just say "3", "Others" and similar), the alert says so instead of printing the placeholder.

Delivery channels:
- `email` sends through SMTP. Set `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD` and `ALERT_FROM` in `.env`. For Gmail, use an app password
- `console` prints alerts

### Getting purchases in

Order emails and receipts follow the same path. Lines are parsed into purchases and saved with stable IDs, so importing the same email twice adds nothing. Only the new lines are matched, and a high-confidence match triggers an alert straight away.

**Order emails.** The importer recognises order mail from Amazon, Flipkart, BigBasket, Blinkit, Zepto, Swiggy Instamart, JioMart, DMart Ready and others by sender, skips promotions, and reads item lines from HTML tables, "2 x Item ₹54" lines and "Item… Quantity: 1" blocks. Delivery fees, taxes and totals are dropped. There are two ways in:

**Receipts.** Tesseract OCR with upscaling and contrast correction, then a parser that finds the shop name, the date and the item lines between the "Item … Qty … Amount" header and the total. It strips HSN codes, quantities and prices.

Retailers change their email layouts, so run with `--dry-run` first. It prints the parsed lines and writes them to `data/intake/` without saving anything.

### Global Regulatory Status

`data/reference/global_status.csv` has one row per ingredient and jurisdiction (EU, US, California), with the status, a one-line note, the source, a link, the date checked and a `verified` flag. India's status always comes from the FSSAI rulebook in `rules.csv`, so Indian rules have a single source of truth.

```
python -m recallindia global-status --divergent
```

This lists ingredients that India allows but another regulator doesn't. Right now that's erythrosine (INS 127), Ponceau 4R (124) and carmoisine (122). An ingredient with no FSSAI rule loaded, or only a partial one (for example "not permitted in infant food"), is never shown as allowed in India.

I kept this lookup deliberately narrow, to stay on the right side of defamation:

- It takes an ingredient (INS/E number or name) and nothing else. There is no brand or product input.
- Each row states the regulator's own status and links to it, and the output always carries a disclaimer.
- Rows I've checked against an official regulator page are marked `verified=yes`: the EU titanium dioxide ban, FDA's Red No. 3 revocation, the BVO revocation, the cyclamate prohibition and FDA's food colour list. The rest are `no` until I've checked them.

## The app

There's a phone-friendly web app (a PWA) served by the same FastAPI server. No separate install and no Node.js needed.

```powershell
python -m recallindia serve
```

Open http://localhost:8000 (it redirects to `/app/`). On first launch you enter your name to create an account.
| Screen | What it does |
|---|---|
| Home | Shows whether anything you bought matches a recall, how many products are watched, and the latest FSSAI recalls |
| Scan | Reads a barcode by live camera, photo or typed digits, or reads an ingredient label by photo (OCR) or typed text |
| Scan result | Gives a verdict, each flagged ingredient with its FSSAI rule and its status abroad, any recall matches, and "Save to My Products". For scanned barcodes it also lists similar products from the same category that don't contain the flagged ingredients (Open Food Facts, India, cached for a day, worded as facts rather than a safety rating) |
| My Products | Everything you bought, grouped by shop and date, with its source. You can import order emails, scan a receipt, add a product, set a batch number (which rechecks it), mark it finished, or delete it |
| Alerts | Each recall alert with what to do, plus "I have this batch" / "Not my batch". With notification keys set, new alerts also arrive as phone notifications |
| Ingredient lookup | Global Regulatory Status for any ingredient or E-number |

Loose goods and fresh produce (onions, loose sugar, eggs) aren't tracked, because they have no brand or batch to match against a recall notice. Imports list them as "not tracked".

**Hosting on https:** `DEPLOY.md` walks through Render's free tier using the included `Dockerfile` and `render.yaml`, plus setting up Google sign-in and notification keys.

**On your phone without hosting:** start the server with `python -m recallindia serve --host 0.0.0.0`, find your laptop's IP with `ipconfig` (the IPv4 address), and open `http://<that IP>:8000` on a phone on the same Wi-Fi. Browsers only allow the live camera over https or on the laptop itself, so on a phone over Wi-Fi use **Take a photo of the barcode**. Chrome's menu then offers **Add to Home screen**, so it opens like a normal app. Once it's hosted on https (Render, Railway), the live camera works on phones too.

## How accurate is it?

`python -m recallindia evaluate` runs the full pipeline (candidate search, then scoring) over all 236 real recalls, against two hand-labelled sets:

- **Dev set** (`data/eval/real_*.csv`): 72 receipt lines, 71 true matches. I wrote these while building the matcher.
- **Holdout set** (`data/eval/holdout_*.csv`): 70 lines, 56 true matches. I wrote and labelled these before running the matcher on them, and never used them for tuning. It includes 17 hard negatives (same brand but a different product, same product from another brand, a different batch, a different variant) and 3 receipt-style abbreviations like "RMDEV MIX MSALA 50G". I completed the labels by keyword search over all recalls, not by using the scorer.

| Threshold | Dev precision | Dev recall | Holdout precision | Holdout recall |
|---|---|---|---|---|
| 60 | 0.68 | 1.00 | 0.82 | 0.98 |
| 65–80 | 0.99 | 0.97 | 0.94 | 0.88 |
| 85 | 0.98 | 0.86 | 0.98 | 0.86 |
| 90 | 1.00 | 0.73 | 1.00 | 0.79 |

Precision at 65–80 drops from 0.99 on dev to 0.94 on holdout. That gap is the reason the review queue exists. With both bands applied, the holdout results are:

| | Holdout |
|---|---|
| Auto-published | 49 matches at **0.98 precision**, 0.86 recall |
| Sent to review | 3 matches (1 true, 2 false) |
| Recall once reviewed | 0.88 |
| Missed | 7 of 56 |

In practice a user sees a wrong match about 1 time in 50, and the review queue stays small.

The errors on the holdout are the most useful part:

- **6 of the 7 misses** have a full brand match, but the product score falls under the 60 cap. In 5 of them, FoSCoS describes the product only by food category ("Non-carbonated Water Based Beverages", "Culinary Pastes", "Pickles"), and 3 of those also have the same batch. In 1, the receipt abbreviates the product ("RMDEV MIX MSALA"). The 7th abbreviates the brand too ("VPR GLD COCNT OIL"). A rule sending "brand and batch agree, product differs" to review would catch 3 of them. But I found that rule by looking at the holdout, so I can't claim it as an improvement until I test it on fresh data.
- **The one auto-published false positive** matched Everest Kashmiri Lal chilli powder to a recall of Everest's Tikhalal chilli powder, which is a different variant from the same brand.

Real receipt lines from Gmail and OCR imports would make the best next test set.

### How a match is scored

FoSCoS puts a food *category* in its Product column (for example "Chillies and Capsicum (Lal Mirchi)"). The actual product name is usually in the Brand column ("Eastern - CHILLI POWDER"), and the FBO is often the shop that sold it. So each receipt line is compared with brand, product and FBO together.

1. **Candidates.** PostgreSQL trigram word similarity (`<%` on a GIN index) finds up to 25 recalls whose brand, product and FBO text looks like the receipt line.
2. **Brand (40%).** The share of distinctive brand words found on the other side, checked both ways. Company suffixes (Pvt, Ltd, Foods, Traders) and product words (chilli, atta, ghee, bombay mixture) don't count as brand words. Spellings within 85% similarity count as the same word, so "Chaki" matches "Chakki".
3. **Product (40%).** Token set similarity of whatever is left after removing the shared brand words. Common Indian food words are mapped to one form first: hing → asafoetida, haldi → turmeric, jeera → cumin, rava/suji → semolina, mirchi → chilli, dahi → curd.
4. **Batch (20%).** Compared after removing spaces, punctuation and "Batch No." prefixes. Placeholders like NIL, LOOSE or 0 are ignored.

If a part is missing, its weight is shared among the others. Then these caps apply:

| Condition | Score capped at |
|---|---|
| Brand words don't match (under 50) | 50 |
| Product doesn't match (under 60) | 60 |
| Batch numbers differ | 60 |
| Neither side has a usable brand | 70 (so it always goes to review) |
| Bought before the batch was made | 0 |
| Bought after the batch expired | score halved |

## The recall data pipeline

| Stage | Module | What it does |
|---|---|---|
| Discover | `scraper.discover` | Opens the portal, finds the Food Recall section, saves screenshots, HTML, every JSON response and a summary |
| Scrape | `scraper.scrape` | Reads recall tables across every page and captures the JSON the page loads, keeping whichever gives more rows |
| Clean | `clean` | Maps portal headers to standard columns, turns `N/A`/`-`/`nil` into nulls, parses dates, normalises IDs, batches and licence numbers, tags hazards, strips phone numbers and emails from reasons, and removes duplicate recall IDs |
| Validate | `quality` | Profiles every column, checks the rules below, and writes `data/reports/quality_report.md` |
| Load | `db` | Creates tables and GIN trigram indexes, upgrades older databases in place, upserts recalls and records when each was first seen |
| Refresh | `pipeline.refresh` | Loads recalls, rematches every purchase, queues and sends alerts, and logs the run |

| Level | Validation rule |
|---|---|
| Error | `recall_id`, `product_name` and `recall_date` are present; `recall_id` is unique; `recall_date` isn't in the future; `expiry_date` is after `manufacture_date` |
| Warning | `recall_date` is before `manufacture_date`; licence number isn't 14 digits; brand, batch, reason or status is missing; reason is a placeholder or names no recognised hazard |

## Setting it up on Windows

1. **Install Python** 3.11 or newer from python.org, and tick "Add python.exe to PATH".
2. **Install PostgreSQL 16** with the EDB installer from postgresql.org/download/windows. Note the password you set for the `postgres` user. `pg_trgm` is included.
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
6. **Create the database.** In SQL Shell (psql) or pgAdmin: `CREATE DATABASE recallindia;`

`load`, `run` and `load-rules` create the extension, tables and indexes, and bring a database from an earlier version up to date.

## Running it

### First run

```powershell
python -m recallindia scrape                 # FSSAI recalls from FoSCoS
python -m recallindia clean
python -m recallindia report                 # data quality report
python -m recallindia load                   # recalls into PostgreSQL
python -m recallindia load-rules             # FSSAI rules and global status
python -m recallindia users add ritu --email you@example.com --name Ritu
python -m recallindia import-email "All mail.mbox" --user ritu
python -m recallindia import-receipt receipt.jpg --user ritu
python -m recallindia review list
python -m recallindia review approve 12 --note "same brand and batch"
python -m recallindia serve                  # API docs at http://127.0.0.1:8000/docs
```

### Keeping it up to date

```powershell
python -m recallindia run --scrape
```

This scrapes FoSCoS, loads the recalls (counting the new ones), rematches every purchase, fills the review queue and sends alerts. To run it every morning, add a Windows Task Scheduler task that runs `.venv\Scripts\python.exe -m recallindia run --scrape` in the project folder.

### All commands

| Command | Use |
|---|---|
| `discover`, `scrape`, `clean`, `report` | Collect, clean and check FSSAI recall data |
| `load` | Load cleaned recalls (and a purchases CSV, owned by `--user`) |
| `match` | Match all purchases and fill the review queue. `--memory` works from the cleaned files |
| `run [--scrape] [--no-send]` | Full refresh |
| `review list`, `review approve ID…`, `review reject ID…` | Work the review queue. Approving sends the alert unless `--no-send` |
| `alerts [--channel outbox\|email\|console]` | Queue and send pending alerts |
| `users add ID --email … --name …`, `users list` | Manage users |
| `import-email PATH`, `import-email --gmail` | Order-email intake. `--dry-run` to preview |
| `import-receipt FILE…` | Receipt OCR intake. `--show-text` prints the OCR text |
| `evaluate`, `evaluate --purchases p.csv --gold g.csv`, `evaluate --sample` | Accuracy reports |
| `global-status QUERY`, `global-status --divergent` | Ingredient status in India, the EU and the US |
| `load-rules`, `check`, `serve` | Reference data, label check, API |

`--purchases` CSVs use the columns `purchase_id, product_name, brand, batch_no, store, purchase_date` (and optionally `user_id`). Dates are day-first, like `15/06/2025`.

## API

| Method | Path | Returns |
|---|---|---|
| POST | `/users` | Create or update a user `{"user_id", "email", "name", "alerts_enabled"}` |
| POST | `/users/{id}/purchases` | Add purchases (JSON list), match them, send alerts |
| POST | `/users/{id}/receipts` | Upload a receipt photo: OCR, match, alert |
| POST | `/users/{id}/order-emails` | Upload an `.eml` or Takeout `.mbox`: import, match, alert |
| GET | `/users/{id}/matches` | Published matches for the user (never pending ones) |
| GET | `/users/{id}/summary` | Home screen: products watched, which are affected, latest recalls |
| GET | `/users/{id}/purchases` | My Products, with the published recalls each one matches |
| PATCH / DELETE | `/users/{id}/purchases/{pid}` | Set a batch number or name (rechecks it), mark finished, or delete |
| GET | `/users/{id}/alerts` | The user's alerts with what-to-do text, unanswered first |
| POST | `/users/{id}/alerts/{aid}/feedback` | `{"answer": "has_batch" \| "not_my_batch"}` |
| POST | `/check/photo` | A photo of an ingredient label: OCR, then the label check |
| GET | `/alternatives?barcode=…` | Similar products without the scanned product's flagged ingredients |
| GET / POST | `/auth/config`, `/auth/google` | Google sign-in: settings, and exchanging Google's token for a session |
| GET / POST | `/push/config`, `/users/{id}/push`, `/users/{id}/push/remove`, `/users/{id}/push/test` | Phone notifications |
| POST | `/match` | High-confidence recalls for one product. `?include_medium=true` adds medium ones, for reviewers |
| GET | `/purchases/{id}/matches` | Published matches for one purchase |
| GET | `/review` | The review queue |
| POST | `/review/{match_id}` | `{"decision": "approve" \| "reject", "note": "..."}` |
| GET | `/admin/review` | Review page |
| POST | `/admin/alerts/send` | Queue and send pending alerts |
| GET | `/recalls?q=…`, `/recalls/{id}` | Recall search and detail |
| GET | `/recalls/{id}/alternatives?q=…` | Similar products from other brands (Open Food Facts, India) with no FSSAI recall on record |
| GET | `/global-status?q=127`, `/global-status/divergent` | Global Regulatory Status |
| GET | `/sources` | Regulators covered and planned |
| GET | `/stats` | Counts, matches and alerts by status, last refresh, recalls by month |
| POST | `/check` | Label or barcode check |
| GET | `/categories`, `/substances?q=…` | FSSAI food categories and additives |
| POST | `/admin/reload-rules` | Reload reference data after `load-rules` |
| GET | `/health` | Status and recall count |

## Label and barcode check

Besides recall alerts, `check` reads a label's ingredient list (typed, or pulled from Open Food Facts by barcode), finds INS/E codes and names, and checks them against FSSAI rules for the product's food category. It returns avoid, caution, check or no issues found, along with the rule and source behind each flag, each ingredient's status abroad, and any high-confidence recall matches.

The rules in `data/reference/rules.csv` are a starting set, and only the potassium bromate ban is marked verified so far. The next step is importing the full additive tables from the [FSSAI compendium](https://fssai.gov.in/cms/compendium-fss-fps-fa.php).

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
  alternatives.py   similar products without flagged ingredients
  auth.py           Google sign-in and signed sessions
  push.py           phone notifications (Web Push)
  intake.py         shared purchase-intake helpers
  email_import.py   order emails from Takeout .mbox, .eml or the Gmail API
  receipts.py       receipt OCR and line parsing
  global_status.py  Global Regulatory Status
  ingredients.py, rulebook.py, checker.py, openfoodfacts.py   label check
  db.py             SQLAlchemy models, migrations, trigram search, review, alert and app SQL
  api.py            FastAPI app;  static/review.html  review page;  static/app/  the phone app (PWA)
  config.py         settings, thresholds, sources
data/reference/     FSSAI categories, substances, rules, global status
data/eval/          dev and holdout labelled receipt lines
data/samples/       synthetic sample data
tests/              pytest suite and fixtures (order emails, receipt)
```

## What I've tested and what I haven't

**Tested:** cleaning, validation, matching, banding, review metrics, alert text and delivery, order-email parsing, receipt parsing and OCR, global status, and the evaluation sets (48 tests). I ran the review, alert and migration SQL against PostgreSQL on my own machine.

**Data limits:** FoSCoS gives no manufacture or expiry dates and often no batch, so many matches rest on brand and product alone. The alert tells the user to check the batch on the pack.

## What's next
CDSCO monthly PDF alerts (Phase 2), BIS notices, a scheduled scrape on the server, a fresh test set from real imports, and the "brand and batch agree" review rule.
