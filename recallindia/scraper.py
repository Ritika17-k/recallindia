import json
import re
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from .clean import canonical_header
from .config import DISCOVERY_DIR, FOSCOS_URL, HEADLESS, MAX_PAGES, RAW_DIR, RECALL_URL

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/128.0.0.0 Safari/537.36")
EXPORT_PATTERN = re.compile(r"export\s*(to)?\s*(excel|xls|csv)", re.I)
RECALL_LINK_PATTERNS = [r"food\s*recall", r"recall"]
NEXT_SELECTORS = [
    "a[aria-label='Next']", "button[aria-label='Next']", "li.next:not(.disabled) a",
    "a.paginate_button.next:not(.disabled)", "button:has-text('Next')", "a:has-text('Next')",
    "a:has-text('›')", "a:has-text('»')",
]


def _stamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _open_recall_section(page):
    for pattern in RECALL_LINK_PATTERNS:
        link = page.get_by_text(re.compile(pattern, re.I)).first
        try:
            if link.count() and link.is_visible():
                link.click()
                page.wait_for_load_state("networkidle", timeout=30000)
                return True
        except Exception:
            continue
    return False


class JsonCapture:
    def __init__(self):
        self.payloads = []
        self.requests = []

    def __call__(self, response):
        try:
            req = response.request
            ctype = response.headers.get("content-type") or ""
            if req.resource_type not in ("xhr", "fetch") and "json" not in ctype:
                return
            self.requests.append({"url": response.url, "method": req.method, "status": response.status,
                                  "content_type": ctype, "post_data": (req.post_data or "")[:2000]})
            text = response.text()
            if "json" in ctype or text.lstrip()[:1] in "[{":
                self.payloads.append({"url": response.url, "status": response.status, "body": json.loads(text)})
        except Exception:
            pass


def _record_lists(obj, path=""):
    if isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj):
        yield path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _record_lists(v, f"{path}.{k}" if path else k)


def _looks_like_recalls(records):
    keys = set().union(*(r.keys() for r in records[:5]))
    mapped = {canonical_header(k) for k in keys} - {None}
    return len(mapped) >= 3 and "product_name" in mapped


def records_from_json(payloads):
    rows = []
    for p in payloads:
        for _, records in _record_lists(p["body"]):
            if _looks_like_recalls(records):
                rows.extend(records)
    return rows


def records_from_tables(page):
    rows = []
    for table in page.locator("table").all():
        headers = [h.inner_text().strip() for h in table.locator("thead th").all()]
        if not headers:
            first = table.locator("tr").first
            headers = [c.inner_text().strip() for c in first.locator("th, td").all()]
        if len({canonical_header(h) for h in headers} - {None}) < 3:
            continue
        body_rows = table.locator("tbody tr").all() or table.locator("tr").all()[1:]
        for tr in body_rows:
            cells = [c.inner_text().strip() for c in tr.locator("td").all()]
            if len(cells) == len(headers):
                rows.append(dict(zip(headers, cells)))
    return rows


def _click_next(page):
    for sel in NEXT_SELECTORS:
        btn = page.locator(sel).first
        try:
            if not btn.count() or not btn.is_visible() or not btn.is_enabled():
                continue
            cls = (btn.get_attribute("class") or "") + " " + (btn.get_attribute("aria-disabled") or "")
            if "disabled" in cls or "true" in cls:
                continue
            btn.click()
            page.wait_for_load_state("networkidle", timeout=30000)
            time.sleep(0.5)
            return True
        except Exception:
            continue
    return False


def _wait_for_rows(page, timeout=20000):
    try:
        page.wait_for_selector("table tbody tr td", timeout=timeout)
        time.sleep(1)
        return True
    except Exception:
        return False


def describe_tables(page):
    out = []
    for table in page.locator("table").all():
        headers = [h.inner_text().strip() for h in table.locator("thead th").all()]
        rows = table.locator("tbody tr").all()
        out.append({"headers": headers, "mapped": {h: canonical_header(h) for h in headers}, "rows": len(rows),
                    "first_rows": [[c.inner_text().strip()[:80] for c in r.locator("td").all()] for r in rows[:3]]})
    return out


def describe_controls(page):
    js = """() => ({
        buttons: [...document.querySelectorAll('button, input[type=submit], input[type=button]')]
            .map(e => (e.innerText || e.value || '').trim()).filter(Boolean).slice(0, 40),
        selects: [...document.querySelectorAll('select')].map(s => ({name: s.name || s.id,
            options: [...s.options].map(o => o.text.trim()).slice(0, 15)})),
        inputs: [...document.querySelectorAll('input')].map(i => ({name: i.name || i.id, type: i.type,
            placeholder: i.placeholder})).slice(0, 30)
    })"""
    return page.evaluate(js)


def _launch():
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=HEADLESS, args=["--disable-blink-features=AutomationControlled"])
    context = browser.new_context(viewport={"width": 1400, "height": 900}, user_agent=USER_AGENT,
                                  locale="en-IN", timezone_id="Asia/Kolkata", accept_downloads=True)
    page = context.new_page()
    return pw, browser, page


def _find_header_row(frame):
    for i in range(min(15, len(frame))):
        values = [str(v) for v in frame.iloc[i].tolist()]
        if len({canonical_header(v) for v in values} - {None}) >= 3:
            body = frame.iloc[i + 1:].copy()
            body.columns = values
            return body.dropna(how="all")
    return None


def read_export(path):
    path = Path(path)
    frames = []
    try:
        frames = [pd.read_excel(path, header=None, dtype=str)]
    except Exception:
        try:
            frames = pd.read_html(str(path))
        except Exception:
            try:
                frames = [pd.read_csv(path, header=None, dtype=str)]
            except Exception:
                frames = []
    for frame in frames:
        frame = frame.astype(object).where(frame.notna(), None)
        if all(isinstance(c, int) for c in frame.columns):
            found = _find_header_row(frame)
        else:
            found = frame if len({canonical_header(c) for c in frame.columns} - {None}) >= 3 else None
            if found is None:
                found = _find_header_row(frame)
        if found is not None and len(found):
            return found.reset_index(drop=True)
    return None


def export_all(page, out_dir):
    button = page.get_by_text(EXPORT_PATTERN).first
    try:
        if not button.count():
            return None, None
        with page.expect_download(timeout=60000) as info:
            button.click()
        download = info.value
        target = Path(out_dir) / f"foscos_export_{_stamp()}_{download.suggested_filename or 'recalls.xlsx'}"
        download.save_as(str(target))
        return target, read_export(target)
    except Exception:
        return None, None


def _first_row_text(page):
    try:
        return page.locator("table tbody tr").first.inner_text(timeout=2000)
    except Exception:
        return None


def discover(url=None, out_dir=None):
    name = re.sub(r"\W+", "_", re.sub(r"^https?://", "", url or FOSCOS_URL)).strip("_")[:40]
    out = Path(out_dir or DISCOVERY_DIR) / f"{_stamp()}_{name}"
    out.mkdir(parents=True, exist_ok=True)
    pw, browser, page = _launch()
    capture = JsonCapture()
    page.on("response", capture)
    try:
        page.goto(url or FOSCOS_URL, wait_until="networkidle", timeout=60000)
        page.screenshot(path=str(out / "home.png"), full_page=True)
        (out / "home.html").write_text(page.content(), encoding="utf-8")
        opened = False if "recall" in page.url.lower() else _open_recall_section(page)
        rows_appeared = _wait_for_rows(page)
        page.screenshot(path=str(out / "recall.png"), full_page=True)
        (out / "recall.html").write_text(page.content(), encoding="utf-8")
        links = page.eval_on_selector_all("a", "els => els.map(e => ({text: e.innerText.trim(), href: e.href}))")
        summary = {
            "url": page.url,
            "recall_section_opened": opened,
            "tables": page.locator("table").count(),
            "rows_appeared": rows_appeared,
            "table_details": describe_tables(page),
            "controls": describe_controls(page),
            "table_rows_found": len(records_from_tables(page)),
            "requests": capture.requests,
            "json_rows_found": len(records_from_json(capture.payloads)),
            "recall_links": [l for l in links if re.search("recall", l["text"] + l["href"], re.I)],
        }
        (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        (out / "network.json").write_text(json.dumps(capture.payloads, indent=2, default=str)[:5_000_000],
                                          encoding="utf-8")
    finally:
        browser.close()
        pw.stop()
    return out, summary


def scrape(url=None, max_pages=None, out_dir=None):
    max_pages = max_pages or MAX_PAGES
    out = Path(out_dir or RAW_DIR)
    out.mkdir(parents=True, exist_ok=True)
    pw, browser, page = _launch()
    capture = JsonCapture()
    page.on("response", capture)
    rows, pages, export_file, exported = [], 0, None, None
    try:
        page.goto(url or RECALL_URL, wait_until="networkidle", timeout=60000)
        if "recall" not in page.url.lower():
            _open_recall_section(page)
        _wait_for_rows(page, 30000)
        export_file, exported = export_all(page, out)
        if exported is None or exported.empty:
            last_first = None
            while pages < max_pages:
                first = _first_row_text(page)
                if first is not None and first == last_first:
                    break
                last_first = first
                pages += 1
                rows.extend(records_from_tables(page))
                if not _click_next(page):
                    break
                _wait_for_rows(page, 10000)
        json_rows = records_from_json(capture.payloads)
    finally:
        browser.close()
        pw.stop()
    if exported is not None and not exported.empty:
        df, source = exported, f"excel export ({export_file.name})"
    elif len(json_rows) > len(rows):
        df, source = pd.DataFrame(json_rows), "json"
    else:
        df, source = pd.DataFrame(rows), "table"
    path = out / f"recalls_raw_{_stamp()}.csv"
    df.to_csv(path, index=False, encoding="utf-8")
    return path, {"pages": pages, "rows": len(df), "source": source}


def latest_raw(directory=None):
    files = sorted(Path(directory or RAW_DIR).glob("recalls_raw_*.csv"))
    return files[-1] if files else None