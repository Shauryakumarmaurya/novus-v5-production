import re
from datetime import datetime

def parse_financial_date(date_str: str) -> int:
    """
    Parses a variety of Indian corporate financial date strings into a sortable YYYYMM integer.
    Examples:
    - "FY24" -> 202403  (Ends March 2024)
    - "Q3 FY25" -> 202412 (Ends Dec 2024)
    - "Dec-2025" -> 202512
    - "April 2024" -> 202404
    - "2024" -> 202412
    """
    date_str = date_str.upper().replace(' ', '')
    
    # 1. FY pattern: e.g., FY24, FY2025
    fy_match = re.search(r'FY(\d{2,4})', date_str)
    if fy_match:
        year = int(fy_match.group(1))
        year = 2000 + year if year < 100 else year
        
        # Check if there is a quarter attached (Q1, Q2, Q3, Q4)
        q_match = re.search(r'Q([1-4])', date_str)
        if q_match:
            q = int(q_match.group(1))
            # Indian FY starts in April. Q1=Jun, Q2=Sep, Q3=Dec, Q4=Mar
            if q == 1:
                return (year - 1) * 100 + 6
            elif q == 2:
                return (year - 1) * 100 + 9
            elif q == 3:
                return (year - 1) * 100 + 12
            elif q == 4:
                return year * 100 + 3
        # If no quarter, assume end of FY (March)
        return year * 100 + 3

    # 2. Month-Year pattern (e.g., DEC2025, APR24, DEC-2025)
    months = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
    for i, m in enumerate(months):
        if m in date_str:
            # Extract the year following the month
            # Allow optional dash or space separator
            yr_match = re.search(f"{m}[^0-9]*(\\d{{2,4}})", date_str)
            if yr_match:
                year = int(yr_match.group(1))
                year = 2000 + year if year < 100 else year
                return year * 100 + (i + 1)
            
    # 3. YYYY pattern (fallback)
    yr_match = re.search(r'(20\d{2})', date_str)
    if yr_match:
        return int(yr_match.group(1)) * 100 + 12  # default to Dec

    # If completely unparseable, return 0 (safe fallback)
    return 0


def verify_chronology(cause_date: str, effect_date: str) -> bool:
    """
    Verifies that the cause precedes or aligns with the effect.
    """
    if not cause_date or not effect_date or cause_date.upper() == 'N/A' or effect_date.upper() == 'N/A':
        return True # Cannot verify cleanly, pass through

    cause_score = parse_financial_date(cause_date)
    effect_score = parse_financial_date(effect_date)

    if cause_score == 0 or effect_score == 0:
        return True # Could not parse

    return cause_score <= effect_score
