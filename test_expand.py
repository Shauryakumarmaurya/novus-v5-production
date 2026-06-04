import requests
import re
import pandas as pd
from io import StringIO
import json

def test_fetch():
    ticker = 'HINDUNILVR'
    url = f"https://www.screener.in/company/{ticker}/consolidated/"
    response = requests.get(url)
    html = response.text
    company_match = re.search(r'data-company-id="(\d+)"', html)
    if not company_match:
        print("No company ID")
        return
    company_id = company_match.group(1)
    
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    section = soup.find("section", id="profit-loss")
    table = section.find("table")
    html_table = str(table)
    
    clean_html = html_table.replace('<button', '<div').replace('</button>', '</div>')
    df = pd.read_html(StringIO(clean_html))[0]
    df.columns = [str(c).replace('\n', ' ').strip() for c in df.columns]
    if df.columns[0].strip() == "" or "Unnamed" in df.columns[0]:
        columns = list(df.columns)
        columns[0] = "Line Item"
        df.columns = columns
    
    records = df.to_dict(orient="records")
    
    # print raw line items
    print("Raw parents:", [r.get("Line Item") for r in records[:5]])
    
    expand_calls = re.findall(r"Company\.showSchedule\('([^']+)',\s*'([^']+)'", html_table)
    print("Expand calls found:", expand_calls)
    
    session = requests.Session()
    new_records = []
    
    for row in records:
        raw_line_item = str(row.get("Line Item", ""))
        # usually ends with + (e.g., 'Sales +')
        line_item = raw_line_item.replace("+", "").strip() 
        line_item = line_item.replace("\xa0", " ").strip()
        
        # Remove the plus from the parent name too just in case
        row["Line Item"] = line_item
        new_records.append(row)
        
        for parent, sec in expand_calls:
            clean_parent = parent.strip()
            if line_item == clean_parent:
                print(f"Fetching sub-schedule for {line_item}...")
                api_url = f"https://www.screener.in/api/company/{company_id}/schedules/?parent={parent.replace(' ', '%20')}&section={sec}&consolidated="
                try:
                    res = session.get(api_url, timeout=10)
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
                    else:
                        print(f"Failed status {res.status_code} for {api_url}")
                except Exception as e:
                    print(f"Error fetching {api_url}: {e}")
                break
                
    print("\nResult Sample:")
    for r in new_records[:10]:
        print(f"{r['Line Item']:<25} | {r.get('Mar 2025', '')}")

if __name__ == '__main__':
    test_fetch()
