#!/usr/bin/env python3
"""
fetch_screener_tickers.py
─────────────────────────
Queries Screener.in for companies with Market Cap > 5000 Cr using a user
session cookie, and parses the resulting companies for Sector/Industry.

Saves the results in two formats:
    1. A hierarchical JSON file (grouped by Sector -> Industry)
    2. A flat CSV file (for compatibility with the scraper pipeline)

Usage:
    python fetch_screener_tickers.py

Requirements:
    requests, beautifulsoup4, tqdm
"""

import argparse
import csv
import json
import logging
import random
import socket
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

# ─── CONFIG ──────────────────────────────────────────────────────────────────

SCREENER_BASE = "https://www.screener.in"

# User-provided cookies for authenticated queries
COOKIES = {
    "sessionid": "0uxi341l67sng74iio3ur0b0mpi15nja",
    "csrftoken": "QNnMwmLuD5dPlPbhHN6F25ZVRR08lrev"
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.screener.in/",
}

# ─── LOGGING ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── NETWORK RESILIENCE ─────────────────────────────────────────────────────

def resilient_get(session: requests.Session, url: str, **kwargs) -> requests.Response:
    """GET with basic retry on network errors."""
    for _ in range(5):
        try:
            return session.get(url, **kwargs)
        except (requests.exceptions.RequestException, OSError) as exc:
            log.warning("  Network error: %s — retrying…", exc)
            time.sleep(2)
    raise Exception(f"Failed to fetch {url} after retries.")

# ─── EXTRACTION ─────────────────────────────────────────────────────────────

def discover_screened_tickers(session: requests.Session) -> list[dict]:
    """
    Paginate through the raw screen for Market Capitalization > 5000
    and extract Name, Ticker, URL, and Market Cap.
    """
    stocks = []
    seen_tickers = set()
    page = 1
    
    log.info("Querying Screener for Market Cap > 5000Cr...")

    while True:
        url = f"https://www.screener.in/screen/raw/?sort=Market+Capitalization&order=desc&query=Market+Capitalization+%3E+5000&page={page}"
        resp = resilient_get(session, url, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")
        
        table = soup.find("table")
        if not table:
            break
            
        rows = table.find_all("tr")
        if len(rows) <= 1:
            break
            
        # Find Market Cap column index
        headers_text = [h.get_text(strip=True) for h in rows[0].find_all(["th", "td"])]
        mcap_idx = -1
        for i, h in enumerate(headers_text):
            if "Mar Cap" in h:
                mcap_idx = i
                break
                
        if mcap_idx == -1:
            log.warning("Could not find Market Cap column on page %d", page)
            break
            
        added_on_page = 0
        for row in rows[1:]:
            tds = row.find_all("td")
            if len(tds) <= mcap_idx:
                continue
                
            name_a = tds[1].find("a")
            if not name_a:
                continue
                
            name = name_a.get_text(strip=True)
            href = name_a["href"]
            screener_url = SCREENER_BASE + href
            
            # Extract ticker from e.g. "/company/RELIANCE/consolidated/"
            parts = [p for p in href.strip("/").split("/") if p]
            ticker = parts[1] if len(parts) >= 2 else None
            if not ticker or ticker in seen_tickers:
                continue
                
            mcap_str = tds[mcap_idx].get_text(strip=True).replace(",", "")
            try:
                mcap = float(mcap_str)
            except ValueError:
                mcap = 0.0
                
            stocks.append({
                "stock_name": name,
                "ticker": ticker,
                "screener_url": screener_url,
                "market_cap": mcap
            })
            seen_tickers.add(ticker)
            added_on_page += 1
            
        log.info("Page %d: Found %d companies", page, added_on_page)
        if added_on_page == 0:
            break
        page += 1
        time.sleep(0.5 + random.random()*0.5)

    log.info("Discovered %d unique stocks matching criteria.", len(stocks))
    return stocks

def fetch_sector_industry(session: requests.Session, stocks: list[dict]) -> None:
    """
    Updates each stock dictionary in-place with its Sector and Industry
    by hitting their individual company pages.
    """
    log.info("Fetching Sector and Industry for %d stocks (this will take a few minutes)...", len(stocks))
    
    for stock in tqdm(stocks, desc="Fetching sectors"):
        try:
            resp = resilient_get(session, stock["screener_url"], timeout=10)
            soup = BeautifulSoup(resp.text, "html.parser")
            
            sector_tag = soup.find("a", title="Sector")
            industry_tag = soup.find("a", title="Industry")
            
            stock["sector"] = sector_tag.get_text(strip=True) if sector_tag else "Other / Unknown"
            stock["industry"] = industry_tag.get_text(strip=True) if industry_tag else "Other / Unknown"
            
        except Exception as e:
            log.warning("Failed to fetch info for %s: %s", stock["ticker"], e)
            stock["sector"] = "Other / Unknown"
            stock["industry"] = "Other / Unknown"
            
        # Be very polite to Screener to avoid blocking
        time.sleep(0.5 + random.random() * 0.5)

# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Fetch large cap tickers hierarchically.")
    parser.add_argument("--csv", default="screener_tickers.csv", help="Output CSV path")
    parser.add_argument("--json", default="screener_tickers_hierarchical.json", help="Output JSON path")
    parser.add_argument("--limit", type=int, default=None, help="Process only first N stocks for testing")
    args = parser.parse_args()

    session = requests.Session()
    session.headers.update(HEADERS)
    session.cookies.update(COOKIES)

    # 1. Fetch screen list (> 5000Cr)
    stocks = discover_screened_tickers(session)
    
    if args.limit:
        log.info("Limiting to first %d stocks for testing.", args.limit)
        stocks = stocks[:args.limit]

    if not stocks:
        log.error("No stocks discovered. Cookies might be invalid or expired.")
        return

    # 2. Scrape sector & industry from each company
    fetch_sector_industry(session, stocks)

    # 3. Save flat CSV (backward compatibility)
    with open(args.csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["stock_name", "ticker", "screener_url", "market_cap", "sector", "industry"]
        )
        writer.writeheader()
        writer.writerows(stocks)
    log.info("✅ Saved %d tickers (flat format) to %s", len(stocks), args.csv)

    # 4. Build and save hierarchical JSON
    hierarchy = {}
    for st in stocks:
        sec = st["sector"]
        ind = st["industry"]
        
        # We drop sector and industry from the leaf node to avoid redundancy,
        # but keep other relevant info
        leaf = {
            "stock_name": st["stock_name"],
            "ticker": st["ticker"],
            "screener_url": st["screener_url"],
            "market_cap": st["market_cap"],
        }
        
        if sec not in hierarchy:
            hierarchy[sec] = {}
        if ind not in hierarchy[sec]:
            hierarchy[sec][ind] = []
            
        hierarchy[sec][ind].append(leaf)

    with open(args.json, "w", encoding="utf-8") as f:
        json.dump(hierarchy, f, indent=2, ensure_ascii=False)
    
    log.info("✅ Saved hierarchical structure to %s", args.json)


if __name__ == "__main__":
    main()
