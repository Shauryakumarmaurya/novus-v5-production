#!/usr/bin/env python3
"""
screener_docs_scraper.py
────────────────────────
Fetches all documents (Annual Reports, Concall Transcripts & PPTs,
Credit Ratings, Exchange Filings / Announcements) for every stock
listed on Screener.in and saves them into:

    <STOCK_DATA_ROOT>/<ticker>/
        annual-reports/
        concalls/
        credit-ratings/
        exchange-filings/
        presentations/

Usage:
    python scrape_screener_docs.py                  # all stocks
    python scrape_screener_docs.py --symbols HUL TCS  # specific stocks
    python scrape_screener_docs.py --resume           # skip already-downloaded files

Requirements (install via pip in venv):
    requests, beautifulsoup4, lxml, tqdm
"""

import argparse
import concurrent.futures
import json
import logging
import os
import random
import re
import socket
import time
import urllib.parse
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

# ─── CONFIG ──────────────────────────────────────────────────────────────────

STOCK_DATA_ROOT = Path(
    "/Users/shauryaiitd/Library/CloudStorage/GoogleDrive-shauryakumarmaurya6000@gmail.com/My Drive/Top 766"
)

SCREENER_BASE = "https://www.screener.in"

# Screener's paginated stock-list endpoint.  Each page returns up to 50 results.
# We iterate until we exhaust all pages.
SCREENER_STOCKS_URL = "https://www.screener.in/api/company/search/"

# How many seconds to wait between page downloads (be polite!)
DOWNLOAD_DELAY = 0.1          # between individual file downloads (almost instant)\

PAGE_DELAY     = 1.5          # between pagination requests to Screener
STOCK_DELAY    = 2.5          # between stocks (company-page fetch)
PARALLEL_DOWNLOADS = 5        # number of concurrent file downloads per stock
CONCURRENT_STOCKS  = 2        # number of stocks to process simultaneously

# HTTP session headers — mimic a real browser to avoid bot blocks
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.screener.in/",
}

# Maximum file size to download (100 MB) — skip anything larger
MAX_FILE_BYTES = 100 * 1024 * 1024

# ─── LOGGING ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ─── NETWORK RESILIENCE ──────────────────────────────────────────────────────

CONNECTIVITY_CHECK_HOST = "www.screener.in"
CONNECTIVITY_CHECK_PORT = 443
CONNECTIVITY_POLL_INTERVAL = 2  # seconds between connectivity checks when offline

def _is_online() -> bool:
    """Quick TCP connect check — returns True if we can reach Screener."""
    try:
        sock = socket.create_connection((CONNECTIVITY_CHECK_HOST, CONNECTIVITY_CHECK_PORT), timeout=5)
        sock.close()
        return True
    except OSError:
        return False


def wait_for_connection() -> None:
    """Block until network connectivity is restored. Polls every 2 s."""
    if _is_online():
        return
    log.warning("⏸  Network is down — pausing until connection is restored …")
    while not _is_online():
        time.sleep(CONNECTIVITY_POLL_INTERVAL)
    log.info("▶  Connection restored — resuming.")


def resilient_request(session: requests.Session, method: str, url: str, **kwargs):
    """
    Wrapper around session.request that:
      1. Waits for connectivity before attempting the request.
      2. On network errors (ConnectionError, Timeout), waits and retries.
      3. On 429 Too Many Requests, automatically sleeps for 60s and retries.
    """
    while True:
        wait_for_connection()
        try:
            resp = session.request(method, url, **kwargs)
            if resp.status_code == 429:
                log.warning("  Rate limit (429) hit for %s. Sleeping for 60 seconds...", url)
                import time
                time.sleep(60)
                continue
            return resp
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                requests.exceptions.ChunkedEncodingError,
                OSError) as exc:
            log.warning("  Network error during request to %s: %s", url, exc)
            wait_for_connection()  # blocks until back online, then retries


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def _load_cookies() -> dict:
    """Screener.in session cookies from env (never hardcode credentials).

    Set SCREENER_SESSIONID and SCREENER_CSRFTOKEN in .env — grab them from
    your browser's cookie store after logging in to screener.in.
    """
    import os
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    sessionid = os.getenv("SCREENER_SESSIONID", "")
    csrftoken = os.getenv("SCREENER_CSRFTOKEN", "")
    if not sessionid:
        log.warning(
            "SCREENER_SESSIONID not set — scraping unauthenticated. "
            "Document lists may be incomplete. Set SCREENER_SESSIONID / "
            "SCREENER_CSRFTOKEN in .env for full access."
        )
        return {}
    return {"sessionid": sessionid, "csrftoken": csrftoken}


COOKIES = _load_cookies()

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    s.cookies.update(COOKIES)
    return s


def safe_filename(url: str, fallback: str = "document") -> str:
    """Derive a safe filename from a URL."""
    parsed = urllib.parse.urlparse(url)
    name = parsed.path.rstrip("/").split("/")[-1]
    # keep only safe chars
    name = re.sub(r'[^\w\-.]', '_', name)
    return name or fallback


def download_file(session: requests.Session, url: str, dest: Path, resume: bool) -> bool:
    """Download *url* to *dest*.  Returns True on success."""
    if resume and dest.exists() and dest.stat().st_size > 0:
        log.debug("  SKIP (exists): %s", dest.name)
        return True

    try:
        resp = resilient_request(session, "GET", url, stream=True, timeout=30, allow_redirects=True)
        resp.raise_for_status()

        content_len = int(resp.headers.get("Content-Length", 0))
        if content_len > MAX_FILE_BYTES:
            log.warning("  SKIP (too large %s MB): %s", content_len // 1024 // 1024, url)
            return False

        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            with open(tmp, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    f.write(chunk)
            tmp.rename(dest)
        except (requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError,
                requests.exceptions.Timeout,
                OSError) as exc:
            log.warning("  ✗ stream interrupted for %s: %s", url, exc)
            tmp.unlink(missing_ok=True)
            # wait for network, then retry the whole download
            wait_for_connection()
            return download_file(session, url, dest, resume)

        log.info("  ✓ %s", dest.name)
        return True

    except requests.exceptions.RequestException as exc:
        log.warning("  ✗ %s  →  %s", url, exc)
        return False


import csv

# ─── STOCK UNIVERSE ───────────────────────────────────────────────────────────

def fetch_all_stock_symbols(session: requests.Session) -> list[dict]:
    """
    Reads from screener_tickers.csv instead of doing exhaustive crawling.
    Returns a list of dicts:  {"name": ..., "symbol": ..., "url": ...}
    """
    csv_file = Path(__file__).parent / "screener_tickers.csv"
    if not csv_file.exists():
        log.error("screener_tickers.csv not found. Please run fetch_screener_tickers.py first.")
        return []

    stocks: list[dict] = []
    with open(csv_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            stocks.append({
                "id": None,
                "name": row["stock_name"],
                "symbol": row["ticker"],
                "url": row["screener_url"],
                "sector": row.get("sector", "Unknown_Sector"),
                "industry": row.get("industry", "Unknown_Industry"),
            })

    log.info("Loaded %d stocks from %s.", len(stocks), csv_file.name)
    return stocks


# ─── DOCUMENT EXTRACTION ─────────────────────────────────────────────────────

def is_from_2020_or_later(label: str) -> bool:
    """
    Tries to find a year in the label. If a year is found and it's < 2020, returns False.
    Otherwise returns True.
    """
    text = label.upper()
    
    # 1. Match 4-digit years (e.g. 2019, 2021)
    four_digit_matches = re.findall(r'\b(19[9][0-9]|20[0-9]{2})\b', text)
    if four_digit_matches:
        years = [int(y) for y in four_digit_matches]
        if max(years) < 2020:
            return False
        else:
            return True

    # 2. Match FY notation like FY19, FY21, FY 20, FY-22
    fy_matches = re.findall(r'FY\s*-?\s*([0-9]{2})\b', text)
    if fy_matches:
        years = [int(y) for y in fy_matches]
        if max(years) < 20: # FY20 is 2020
            return False
        else:
            return True

    # 3. Match 2-digit years in month formats like "Dec 19", "Mar '18", "Sep-19"
    month_regex = r'\b(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[A-Z]*\s*\'?-?\s*([0-9]{2})\b'
    month_matches = re.findall(month_regex, text)
    if month_matches:
        years = [int(y) for _, y in month_matches]
        if max(years) < 20:
            return False
        else:
            return True
            
    # Default: if no date is recognized, safely include it.
    return True


def parse_documents_from_html(session: requests.Session, html: str, symbol: str) -> dict[str, list[dict]]:
    """
    Parse the Screener company page HTML and fetch Important Announcements.
    """
    soup = BeautifulSoup(html, "lxml")
    
    import collections
    docs: dict[str, list[dict]] = collections.defaultdict(list)
    seen_urls = set()

    # ── Find the #documents section ──────────────────────────────────────────
    documents_section = soup.find(id="documents")
    if not documents_section:
        # fall back to searching by heading text
        for heading in soup.find_all(["h2", "h3", "h4", "section"]):
            if "document" in heading.get_text(strip=True).lower():
                documents_section = heading.find_parent(["section", "div"]) or heading
                break

    if not documents_section:
        log.debug("  No #documents section found for %s", symbol)
        return dict(docs)

    # ── Walk sub-headings to categorise links ────────────────────────────────
    current_category: str | None = None

    CATEGORY_MAP = {
        "annual report":   "annual_reports",
        "annual reports":  "annual_reports",
        "credit rating":   "credit_ratings",
        "credit ratings":  "credit_ratings",
        "concall":         "concalls",
        "concalls":        "concalls",
        "conference call": "concalls",
        "announcement":    "announcements",
        "announcements":   "announcements",
        "exchange filing": "announcements",
        "presentation":    "presentations",
        "presentations":   "presentations",
        "important":       "Important"
    }

    for element in documents_section.descendants:
        if not hasattr(element, "name"):
            continue

        # Detect sub-section heading
        if element.name in ("h2", "h3", "h4", "h5"):
            text = element.get_text(strip=True).lower()
            for key, cat in CATEGORY_MAP.items():
                if key in text:
                    current_category = cat
                    break
            else:
                current_category = None
            continue

        # Harvest links
        if element.name == "a" and current_category:
            href = element.get("href", "").strip()
            if not href or href.startswith("#") or "screener.in/login" in href:
                continue

            # Resolve relative URLs
            if href.startswith("/"):
                href = SCREENER_BASE + href

            label = element.get_text(" ", strip=True) or element.get("title", "")
            
            # The date is often in the parent element (e.g. <li> or <div> container)
            # so we check the full parent text for the year.
            parent_text = element.parent.get_text(" ", strip=True) if element.parent else label

            # Skip older documents (pre-2020)
            if not is_from_2020_or_later(parent_text):
                continue

            # Credit-rating pages are HTML (CRISIL/ICRA), not PDFs.
            # We still record them; download logic will save the HTML.
            is_pdf_url = href.lower().endswith(".pdf")
            is_credit  = "crisil.com" in href or "icra.in" in href or "careratings.com" in href

            # Re-categorise by domain if current_category is ambiguous
            if is_credit and current_category not in ("credit_ratings",):
                effective_cat = "credit_ratings"
            elif "presentation" in label.lower() or href.lower().endswith(".pptx"):
                effective_cat = "presentations"
            else:
                effective_cat = current_category

            # Only keep PDF & HTML rating documents (skip YouTube etc.)
            if not (is_pdf_url or is_credit or href.lower().endswith(".zip")):
                continue

            if href not in seen_urls:
                docs[effective_cat].append({"url": href, "label": label})
                seen_urls.add(href)

    # ── Fetch "Important" Announcements via AJAX ─────────────────────────────
    company_id_match = soup.find(attrs={"data-company-id": True})
    if company_id_match:
        comp_id = company_id_match["data-company-id"]
        announcement_url = f"https://www.screener.in/announcements/important/{comp_id}/"
        try:
            resp = resilient_request(session, "GET", announcement_url, timeout=10)
            if resp.status_code == 200:
                ann_soup = BeautifulSoup(resp.text, "lxml")
                for a in ann_soup.find_all("a"):
                    href = a.get("href", "").strip()
                    if not href or href.startswith("#"):
                        continue
                    if href.startswith("/"):
                        href = SCREENER_BASE + href
                    if href.lower().endswith(".pdf") or href.lower().endswith(".zip"):
                        label = a.get_text(" ", strip=True) or a.get("title", "")
                        parent_text = a.parent.get_text(" ", strip=True) if a.parent else label
                        if is_from_2020_or_later(parent_text) and href not in seen_urls:
                            docs["announcements"].append({"url": href, "label": label})
                            seen_urls.add(href)
        except Exception as e:
            log.debug("  Failed to fetch important announcements for %s: %s", symbol, e)

    return dict(docs)


def fetch_company_page(session: requests.Session, url_path: str) -> str | None:
    """Fetch a Screener company page and return raw HTML, or None on error."""
    url = SCREENER_BASE + url_path if url_path.startswith("/") else url_path
    try:
        resp = resilient_request(session, "GET", url, timeout=20)
        resp.raise_for_status()
        return resp.text
    except requests.exceptions.RequestException as exc:
        log.warning("  Failed to fetch %s: %s", url, exc)
        return None


# ─── DOWNLOAD ORCHESTRATOR ────────────────────────────────────────────────────

def _download_one(doc_url: str, dest: Path, resume: bool) -> bool:
    """Thread-safe single-file downloader using its own session."""
    if resume and dest.exists() and dest.stat().st_size > 0:
        log.debug("  SKIP (exists): %s", dest.name)
        return True
    try:
        s = requests.Session()
        s.headers.update(HEADERS)
        resp = s.get(doc_url, stream=True, timeout=60, allow_redirects=True)
        resp.raise_for_status()
        content_len = int(resp.headers.get("Content-Length", 0))
        if content_len > MAX_FILE_BYTES:
            log.warning("  SKIP (too large %s MB): %s", content_len // 1024 // 1024, doc_url)
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)
        log.info("  ✓ %s", dest.name)
        return True
    except Exception as exc:
        log.warning("  ✗ %s  →  %s", doc_url, exc)
        return False


def process_stock(
    session: requests.Session,
    stock: dict,
    resume: bool,
) -> dict:
    """Download all documents for one stock using parallel threads."""
    symbol = stock["symbol"]
    name   = stock["name"]
    url    = stock["url"]

    log.info("── %s (%s)", name, symbol)

    sector_name = safe_filename(stock.get("sector", "Unknown"), fallback="Sector").strip("_")
    industry_name = safe_filename(stock.get("industry", "Unknown"), fallback="Industry").strip("_")
    
    stock_dir = STOCK_DATA_ROOT / sector_name / industry_name / symbol.lower()
    stock_dir.mkdir(parents=True, exist_ok=True)

    html = fetch_company_page(session, url)
    if html is None:
        return {"symbol": symbol, "status": "fetch_failed", "downloaded": 0}

    doc_groups = parse_documents_from_html(session, html, symbol)

    # Collect all download tasks
    tasks: list[tuple[str, Path]] = []
    for category, doc_list in doc_groups.items():
        if not doc_list:
            continue
        cat_dir = stock_dir / category
        cat_dir.mkdir(parents=True, exist_ok=True)
        import hashlib
        for doc in doc_list:
            raw_label = doc["label"].strip()
            if not raw_label:
                raw_label = urllib.parse.urlparse(doc["url"]).path.rstrip("/").split("/")[-1]
            
            # Make the label safe for a file system, replacing invalid chars with _
            safe_label = re.sub(r'[^\w\-. ]', '_', raw_label).strip()

            import os
            url_path = urllib.parse.urlparse(doc["url"]).path
            ext = os.path.splitext(url_path)[1].lower()
            if ext not in [".pdf", ".zip", ".html", ".htm"]:
                if ".pdf" in doc["url"].lower():
                    ext = ".pdf"
                elif ".zip" in doc["url"].lower():
                    ext = ".zip"
                elif category == "credit_ratings":
                    ext = ".html"
                else:
                    ext = ".pdf"
            
            # Clean up double extensions from the label
            if ext and safe_label.lower().endswith(ext):
                safe_label = safe_label[:-len(ext)].strip("_")
                
            # Create a 6-character stable hash from the URL to guarantee uniqueness
            url_hash = hashlib.md5(doc["url"].encode()).hexdigest()[:6]
                
            # 200 character cutoff so the hash and extension still easily fit in 255 OS limit
            filename = f"{safe_label[:200]}_{url_hash}{ext}"
                
            dest = cat_dir / filename
            tasks.append((doc["url"], dest))

    if not tasks:
        return {"symbol": symbol, "status": "ok", "downloaded": 0}

    # Download all files in parallel (10 threads)
    total_downloaded = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=PARALLEL_DOWNLOADS) as pool:
        futures = {
            pool.submit(_download_one, doc_url, dest, resume): (doc_url, dest)
            for doc_url, dest in tasks
        }
        for future in concurrent.futures.as_completed(futures):
            if future.result():
                total_downloaded += 1

    return {"symbol": symbol, "status": "ok", "downloaded": total_downloaded}


# ─── ENTRY POINT ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Download all Screener documents for Indian stocks"
    )
    parser.add_argument(
        "--symbols", nargs="+", metavar="SYMBOL",
        help="Only process these ticker symbols (e.g. HUL TCS INFY)"
    )
    parser.add_argument(
        "--resume", action="store_true", default=True,
        help="Skip files that already exist (default: True)"
    )
    parser.add_argument(
        "--no-resume", dest="resume", action="store_false",
        help="Re-download even if file already exists"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="Override the output root directory"
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Process only the first N stocks (useful for testing)"
    )
    args = parser.parse_args()

    global STOCK_DATA_ROOT
    if args.output_dir is not None:
        STOCK_DATA_ROOT = args.output_dir
    STOCK_DATA_ROOT.mkdir(parents=True, exist_ok=True)

    log.info("Output directory: %s", STOCK_DATA_ROOT)

    session = make_session()

    # ── Get stock list ────────────────────────────────────────────────────────
    all_stocks = fetch_all_stock_symbols(session)
    
    if args.symbols:
        target_syms = {s.upper() for s in args.symbols}
        stocks = [s for s in all_stocks if s["symbol"].upper() in target_syms]
        log.info("Processing %d user-specified stocks.", len(stocks))
    else:
        stocks = all_stocks
        if args.limit:
            stocks = stocks[: args.limit]
            log.info("Limiting to first %d stocks.", args.limit)

    # ── Process each stock (3 concurrent workers) ─────────────────────────────
    import threading
    results = []
    failed  = []
    lock = threading.Lock()
    pbar = tqdm(total=len(stocks), desc="Downloading docs", unit="stock")

    def _process_one(stock):
        time.sleep(STOCK_DELAY + random.uniform(0, 0.5))
        result = process_stock(make_session(), stock, resume=args.resume)
        with lock:
            results.append(result)
            if result["status"] != "ok":
                failed.append(result["symbol"])
            pbar.set_postfix(symbol=stock["symbol"])
            pbar.update(1)
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENT_STOCKS) as pool:
        list(pool.map(_process_one, stocks))

    pbar.close()

    # ── Summary ───────────────────────────────────────────────────────────────
    total_files = sum(r["downloaded"] for r in results)
    log.info("═" * 60)
    log.info("Done.  Stocks processed: %d  |  Files downloaded: %d", len(results), total_files)
    if failed:
        log.warning("Failed stocks (%d): %s", len(failed), ", ".join(failed))

    # Save a run-summary JSON next to the script
    summary_path = Path(__file__).parent / "scrape_summary.json"
    with open(summary_path, "w") as f:
        json.dump({
            "total_stocks": len(results),
            "total_files": total_files,
            "failed": failed,
            "results": results,
        }, f, indent=2)
    log.info("Summary saved to %s", summary_path)


if __name__ == "__main__":
    main()
