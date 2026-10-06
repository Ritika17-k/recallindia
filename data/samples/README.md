# Sample data (synthetic)

All files in this folder are **made up** for development and testing. The products, brands, batch numbers, licence numbers and recall IDs (`SYN-...`) are invented and do not describe real recalls.

- `sample_recalls.csv`: 33 rows in a portal-style layout, with deliberate problems to clean up: duplicate IDs, mixed date formats, an invalid date, `N/A` and `-` placeholders, a malformed licence number and a missing brand.
- `sample_purchases.csv`: 123 purchase lines that vary the product names (case, word order, units) and include cases that should not match.
- `labeled_pairs.csv`: 135 purchase/recall pairs labeled `is_match` 1 or 0, with a `case` column that says what each pair tests.

Scores measured on this data only show that the code works. To get real precision and recall, label pairs built from scraped FSSAI recalls.
