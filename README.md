# RecallIndia

RecallIndia checks packaged food against Indian food-safety rules. A user scans a barcode or types the ingredient list from a label. The app reads the INS/E numbers and ingredient names, checks each one against FSSAI rules for that kind of food, and also checks the product against FSSAI recall notices. It returns a verdict (avoid, caution, check, or no issues found) with the rule and source behind every flag.

Cosmetics (CDSCO prohibited substances), watchlist alerts when a rule changes, and suggested alternatives are planned next.

## Ingredient checker

```
barcode ──► Open Food Facts ──┐
                              ├──► ingredient parser ──► rulebook (FSSAI) ──► verdict + findings
label text ───────────────────┘                                │
                                         recall notices ───────┘
```

| Part | Module | What it does |
|---|---|---|
| Parser | `ingredients` | Splits a label at top-level commas, keeps sub-ingredients, and reads INS/E codes in every common style: `INS 500(ii)`, `E330`, `Colours (INS 102, 110)`, `Emulsifier (322)`. It ignores amounts such as `300mg` and `2.5%`, and notes labels that name a class ("permitted synthetic food colour") without the code |
| Rulebook | `rulebook` | Finds each ingredient by INS code, then by exact name or alias, then by close spelling (92+ similarity, names of 6+ letters only), and applies the rules for the product's food category |
| Barcode lookup | `openfoodfacts` | Gets the name, brand, ingredients, additive codes and category from Open Food Facts, maps the category to the FSSAI food category system, and caches the result in the `products` table |
| Checker | `checker` | Combines everything into one result, adding any recall notices that match the product name and brand |

### Rule statuses

| Status | Meaning | Verdict |
|---|---|---|
| `prohibited` | Not allowed in any food | avoid |
| `not_permitted` | Not allowed in this food category | avoid |
| `limit` | Allowed up to a limit the label can't show | caution |
| `restricted` | Allowed only in some categories, within limits | check |
| `ok` | Known additive with no restriction loaded | no issues found |

A recall match raises the verdict to at least caution. Recalls cover specific batches, so the user should compare the batch number on the pack.

### Reference data

The rulebook is three CSV files in `data/reference`, so rules can be added without changing code:

- `food_categories.csv`: the FSSAI food category system (FSSAI Appendix A).
- `substances.csv`: 94 additives and adulterants with INS numbers and alternative names.
- `rules.csv`: one row per rule, giving the substance, the category (`ALL` or a code; `13` also covers `13.1` and `13.2`), the status, any limit, the source and a `verified` flag.

The current rules are a starting set. Only the potassium bromate ban is marked `verified=yes`. The rest are well-known rules that still need checking against the official text before release. The next step is importing the full additive tables from the [FSSAI compendium](https://fssai.gov.in/cms/compendium-fss-fps-fa.php).

## Recall pipeline

```
FoSCoS portal ──► scraper ──► data/raw/*.csv
                                  │
                         clean + validate ──► data/reports/quality_report.md
                                  │
                              PostgreSQL (pg_trgm)
                                  │
            purchases ──► matching engine ──► matches table / CSV
                                  │
                               FastAPI
```

| Stage | Module | What it does |
|---|---|---|
| Discover | `scraper.discover` | Opens the portal, finds the Food Recall section, saves screenshots, HTML, every JSON response, and a summary of what it found |
| Scrape | `scraper.scrape` | Reads recall tables across every page by clicking Next until it is disabled, and also captures the JSON the page loads. Whichever source gives more rows is saved |
| Clean | `clean` | Maps portal headers to standard columns, trims text, turns `N/A`/`-`/`nil` into nulls, parses dates (day-first and ISO), normalizes IDs, batches and licence numbers, and removes duplicate recall IDs (keeping the latest) |
| Validate | `quality` | Profiles every column and checks the rules below, writing a Markdown report |
| Load | `db` | Creates the tables and GIN trigram indexes, then upserts recalls and purchases |
| Match | `matching`, `db` | PostgreSQL trigram search finds candidate recalls, and RapidFuzz weighted scoring decides the match |
| Evaluate | `matching.evaluate_end_to_end` | Runs candidate search and scoring over every recall and measures precision, recall and F1 at each threshold against hand-labelled matches |
| Serve | `api` | REST API over the database |

### Validation rules

| Level | Rule |
|---|---|
| Error | `recall_id`, `product_name` and `recall_date` are present |
| Error | `recall_id` is unique |
| Error | `recall_date` is not in the future |
| Error | `expiry_date` is after `manufacture_date` |
| Warning | `recall_date` is before `manufacture_date` |
| Warning | licence number is not 14 digits |
| Warning | brand, batch, reason or status is missing |

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
| Neither side has a usable brand | 70 |
| Bought before the batch was made | 0 |
| Bought after the batch expired | score halved |

### Measured accuracy

`data/eval` holds 72 receipt lines labelled by hand against the 236 real FoSCoS recalls scraped on 5 Oct 2026. There are 71 true purchase-recall matches. The negatives are deliberately hard: the same brand with a different product (Ramdev Kasuri Methi), the same product from another brand (Catch chilli powder), a different variant (Priyagold Marie vs Butter Delite), and a different batch.

`python -m recallindia evaluate` runs the full pipeline (candidate search, then scoring) over all 236 recalls and counts every predicted pair:

| Threshold | Precision | Recall | F1 |
|---|---|---|---|
| 60 | 0.70 | 1.00 | 0.82 |
| 65–80 | 0.99 | 0.97 | 0.98 |
| 85 | 0.98 | 0.86 | 0.92 |

The candidate search found all 71 true matches. The default threshold is 75, the middle of the stable range.

The errors are:
- **One false positive:** a different Priyagold biscuit, because both are in the "Biscuit" category.
- **Two misses:** "Eastern chilli powder" against a recall whose only description is the category "Chillies and Capsicum", and "Mor Namak low sodium salt" against "Salt Substitutes".

The test lines were written while the matcher was being built, so treat these numbers as optimistic. Adding real receipt lines to `data/eval` gives a fairer measure.

## Setup on Windows

1. **Install Python.** Get Python 3.11 or newer from python.org and tick "Add python.exe to PATH" during install.
2. **Install PostgreSQL.** Get PostgreSQL 16 from the EDB installer at postgresql.org/download/windows. Keep the default components and note the password you set for the `postgres` user. The installer includes `pg_trgm`.
3. **Set up the project.** In PowerShell, from the project folder:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
copy .env.example .env
```

   If PowerShell blocks the activate script, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

4. **Point the project at your database.** Edit `.env` and put your `postgres` password into `DATABASE_URL`.
5. **Create the database.** Open "SQL Shell (psql)" from the Start menu, or use pgAdmin, and run:

```sql
CREATE DATABASE recallindia;
```

`python -m recallindia load` creates the `pg_trgm` extension, the tables and the indexes. `python -m recallindia load-rules` loads the reference CSVs into the database. Run it again whenever you edit them, then call `POST /admin/reload-rules` if the API is running.

## Running it

### Check a product

```powershell
python -m recallindia check --barcode <digits under the barcode>
python -m recallindia check --text "Wheat flour, Sugar, Colours (INS 102, 110), Potassium bromate" --category 7
python -m recallindia check --text "Rice flour, Sucralose" --category 13.2 --json
```

`--category` takes an FSSAI code: 1 dairy, 5 confectionery, 6 cereals, 7 bakery, 12 spices and sauces, 13.1 infant formula, 13.2 baby food, 14 beverages, 15 savoury snacks. With a barcode, the category comes from Open Food Facts unless you set it. If the product isn't on Open Food Facts, type the label with `--text`.

### Recall pipeline on the sample data

```powershell
python -m recallindia clean --sample
python -m recallindia report
python -m recallindia evaluate --sample
python -m recallindia load
python -m recallindia match
python -m recallindia serve
```

Then open http://127.0.0.1:8000/docs.

### Real FSSAI data

```powershell
python -m recallindia discover
python -m recallindia scrape
python -m recallindia clean --purchases my_purchases.csv
python -m recallindia report
python -m recallindia evaluate
python -m recallindia load
python -m recallindia match
```

`discover` opens the FoSCoS Food Recall page (https://foscos.fssai.gov.in/food-recall) and saves a dated folder in `data/discovery` containing `summary.json`, screenshots, the page HTML and the captured network JSON. Check `summary.json` first:

- If `table_rows_found` or `json_rows_found` is above 0, `scrape` can read the section as it is.
- If both are 0, the section needs selector changes. If the portal moves the recall list to its own page, pass its address with `scrape --url`.

Set `SCRAPER_HEADLESS=0` in `.env` to watch the browser while it works.

### Your own purchases

`--purchases` takes a CSV with the columns `purchase_id, product_name, brand, batch_no, store, purchase_date`. Dates are day-first, as in `15/06/2025`.

### Other commands

| Command | Use |
|---|---|
| `match --memory` | Match without the database, from the cleaned files |
| `match --threshold 80` | Override the threshold for one run |
| `evaluate --purchases p.csv --gold g.csv` | Evaluate on your own receipt lines and true matches (`purchase_id, recall_id`) |
| `evaluate --sample` | Evaluate on the synthetic sample pairs |
| `pytest` | Run the tests |

## API

| Method | Path | Returns |
|---|---|---|
| POST | `/check` | Verdict and findings. Body: `{"barcode": "..."}` or `{"ingredients_text": "...", "category": "7", "product_name": "...", "brand": "..."}` |
| GET | `/categories` | FSSAI food categories |
| GET | `/substances?q=102` | Look up an additive by INS number or name, with its rules |
| POST | `/admin/reload-rules` | Reload the rulebook after `load-rules` |
| GET | `/health` | Status and recall count |
| GET | `/recalls?q=chikki&category=dairy&limit=50&offset=0` | Recalls, fuzzy-searched by product or brand when `q` is given, otherwise newest first |
| GET | `/recalls/{recall_id}` | One recall |
| POST | `/match` | Recalls that match one purchase. Body: `{"product_name": "...", "brand": "...", "batch_no": "...", "purchase_date": "2025-06-15"}` |
| GET | `/purchases/{purchase_id}/matches` | Stored matches for a loaded purchase |
| GET | `/stats` | Counts, recalls by category, and recalls by month |

## Project layout

```
recallindia/
  ingredients.py label parsing and INS code extraction
  rulebook.py    substance lookup and category-aware rule checks
  openfoodfacts.py barcode lookup
  checker.py     combines label checks and recall matches
  config.py      settings and portal header aliases
  scraper.py     Playwright discover and scrape
  clean.py       normalization and deduplication
  quality.py     profiling, validation, report
  matching.py    scoring, in-memory candidates, evaluation
  db.py          SQLAlchemy models, pg_trgm indexes, upserts, candidate SQL
  api.py         FastAPI app
  __main__.py    command line
data/reference/  FSSAI categories, substances and rules
data/eval/       hand-labelled receipt lines and true matches against real recalls
data/samples/    synthetic sample data (see its README)
tests/           pytest suite
```

## Status

- **Tested:** label parsing, rule checks, Open Food Facts parsing (on sample responses), cleaning, validation, matching, evaluation, and the trigram candidate SQL on PostgreSQL 16.
- **Scraper:** tested against a mock paginated page. It still needs its first run against the live portal.
- **Next:** import the full FSSAI additive tables; add CDSCO cosmetics rules; a watchlist that alerts users when a rule changes for an ingredient in a saved product; suggested alternatives; label photo OCR; and a React app.
