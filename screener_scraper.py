import requests
from bs4 import BeautifulSoup
import pandas as pd
import logging
from typing import Dict, Any
from io import StringIO

logger = logging.getLogger(__name__)

def clean_dataframe(df: pd.DataFrame) -> list:
    """Cleans a DataFrame and converts it into a list of dicts for JSON serialization."""
    # Handle NaN values
    df = df.fillna("")
    
    # Standardize column names (cast to string just in case)
    df.columns = [str(c).replace('\n', ' ').strip() for c in df.columns]
    
    # Rename the first nameless column usually containing line items to 'Line Item'
    if df.columns[0].strip() == "" or "Unnamed" in df.columns[0]:
        columns = list(df.columns)
        columns[0] = "Line Item"
        df.columns = columns
        
    records = df.to_dict(orient="records")
    return records


def _extract_sector(soup: BeautifulSoup) -> str:
    """Extract the raw sector string from Screener's peer comparison breadcrumbs.
    
    The peers section contains links like:
        /market/IN04/             → "Fast Moving Consumer Goods"
        /market/IN04/IN0401/...   → "Diversified FMCG"
    
    We return the first (broadest) classification as a raw string.
    No dictionary mapping — let the LLM interpret it.
    """
    try:
        peers = soup.find("section", id="peers")
        if peers:
            links = peers.find_all("a", href=True)
            for a in links:
                href = a.get("href", "")
                # Peer section links to /market/INxx/ for sector classification
                if href.startswith("/market/IN"):
                    text = a.get_text(strip=True)
                    if text:
                        return text
    except Exception as e:
        logger.warning(f"Could not extract sector: {e}")
    return "General"


def fetch_screener_tables(ticker: str) -> Dict[str, Any]:
    """
    Fetches the main financial tables from screener.in for a given ticker.
    Returns a dictionary of cleaned tables (Profit/Loss, Balance Sheet, Cash Flow, Ratios).
    """
    ticker = ticker.upper().strip()
    url = f"https://www.screener.in/company/{ticker}/consolidated/"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        
        # Fallback to standalone if consolidated doesn't exist (returns 404 sometimes)
        if response.status_code == 404:
            url = f"https://www.screener.in/company/{ticker}/"
            response = requests.get(url, headers=headers, timeout=15)
            
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Failed to fetch screener data for {ticker}: {e}")
        return {"error": str(e), "tables": {}}

    soup = BeautifulSoup(response.content, "html.parser")
    
    # Extract sector classification from the peer comparison breadcrumbs
    sector = _extract_sector(soup)
    
    targets = {
        "Quarterly Results": "quarters",
        "Profit & Loss": "profit-loss",
        "Balance Sheet": "balance-sheet",
        "Cash Flows": "cash-flow",
        "Ratios": "ratios",
        # Promoter/FII/DII/public holdings per quarter — governance agents use
        # this for promoter holding trend analysis.
        "Shareholding Pattern": "shareholding",
    }
    
    results = {}
    
    for title, section_id in targets.items():
        section = soup.find("section", id=section_id)
        if not section:
            continue
            
        table = section.find("table")
        if not table:
            continue
            
        try:
            # Wrap the string representation of the table in StringIO for modern pandas
            html_table = str(table)
            # Remove button/span tags that clutter the line item names
            clean_html = html_table.replace('<button', '<div').replace('</button>', '</div>')
            df_list = pd.read_html(StringIO(clean_html))
            
            if df_list:
                df = df_list[0]
                records = clean_dataframe(df)

                company_match = __import__('re').search(r'data-company-id="(\d+)"', response.text)
                if company_match:
                    company_id = company_match.group(1)
                    expand_calls = __import__('re').findall(r"Company\.showSchedule\('([^']+)',\s*'([^']+)'", html_table)
                    
                    if expand_calls:
                        session = requests.Session()
                        new_records = []
                        for row in records:
                            raw_line_item = str(row.get("Line Item", ""))
                            line_item = raw_line_item.replace("+", "").replace("\xa0", " ").strip()
                            row["Line Item"] = line_item
                            new_records.append(row)

                            for parent, sec in expand_calls:
                                if line_item == parent.strip():
                                    api_url = f"https://www.screener.in/api/company/{company_id}/schedules/?parent={parent.replace(' ', '%20')}&section={sec}&consolidated="
                                    try:
                                        res = session.get(api_url, timeout=5)
                                        if res.status_code == 200:
                                            sub_dict = res.json()
                                            for sub_key, sub_vals in sub_dict.items():
                                                sub_row = {"Line Item": f"  {sub_key}"}
                                                for k, v in sub_vals.items():
                                                    if k != "isExpandable":
                                                        sub_row[k] = v
                                                for col in df.columns:
                                                    if col not in sub_row:
                                                        sub_row[col] = ""
                                                new_records.append(sub_row)
                                    except Exception as ajax_e:
                                        logger.warning(f"Failed to fetch schedule for {line_item}: {ajax_e}")
                                    break
                        results[title] = new_records
                    else:
                        results[title] = records
                else:
                    results[title] = records
        except Exception as e:
            logger.warning(f"Could not parse table {title} for {ticker}: {e}")
            
    return {
        "ticker": ticker,
        "source": url,
        "sector": sector,
        "tables": results
    }

if __name__ == "__main__":
    # Test execution
    data = fetch_screener_tables("HINDUNILVR")
    print(f"Sector: {data.get('sector')}")
    if data.get("tables"):
        for t_name, t_data in data["tables"].items():
            print(f"--- {t_name} ---")
            print(f"Rows: {len(t_data)}, Columns: {len(t_data[0]) if t_data else 0}")
    else:
        print("No tables found or error occurred.")
