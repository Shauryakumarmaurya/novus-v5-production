import re

def _clean_human_key(key: str) -> str:
    """Format and fix casing/typos in keys."""
    # Specific typo fixes based on user request
    typo_fixes = {
        "pm synthesis": "PM Synthesis",
        "bul case": "Bull Case",
        "bull case": "Bull Case",
        "reverse dcf": "Reverse DCF",
        "moat durability": "Moat Durability"
    }
    
    key_lower = key.lower().replace("_", " ")
    if key_lower in typo_fixes:
        return typo_fixes[key_lower]
        
    human_key = key.replace("_", " ").title()
    
    # Uppercase specific acronyms
    acronyms = ["PM", "DCF", "ROIC", "OCF", "EBITDA", "USFDA", "ANDA", "CAGR", "KPI", "WACC", "FCF", "PAT"]
    for acr in acronyms:
        # Match word boundaries to avoid replacing parts of words (though title() might have messed up boundaries)
        # Using a simpler replace since title() makes it e.g. "Roic" or "Dcf"
        human_key = re.sub(rf'\b{acr.title()}\b', acr, human_key)
        human_key = re.sub(rf'\b{acr.lower()}\b', acr, human_key)
        
    return human_key

def _is_empty_or_none(val) -> bool:
    """Check if value should be suppressed in output."""
    if val is None:
        return True
    if isinstance(val, str):
        if val.strip() == "" or val.strip().lower() == "none":
            return True
    if isinstance(val, (list, dict)):
        if len(val) == 0:
            return True
    return False

def format_dict_as_markdown(d, indent=0):
    lines = []
    spacer = "  " * indent
    if isinstance(d, dict):
        for k, v in d.items():
            if _is_empty_or_none(v):
                continue
                
            human_key = _clean_human_key(str(k))
            
            if indent == 0:
                if isinstance(v, (dict, list)):
                    lines.append(f"#### {human_key}")
                    lines.extend(format_dict_as_markdown(v, indent + 1))
                    lines.append("")
                else:
                    lines.append(f"#### {human_key}")
                    lines.append(str(v))
                    lines.append("")
            else:
                if isinstance(v, dict):
                    lines.append(f"{spacer}- **{human_key}**:")
                    lines.extend(format_dict_as_markdown(v, indent + 1))
                elif isinstance(v, list):
                    lines.append(f"{spacer}- **{human_key}**:")
                    for item in v:
                        if _is_empty_or_none(item):
                            continue
                        if isinstance(item, dict):
                            # Give a little spacing for list of dicts
                            sub_lines = format_dict_as_markdown(item, indent + 1)
                            if sub_lines:
                                sub_lines[0] = sub_lines[0].replace(f"{spacer}  -", f"{spacer}  *", 1) # minor style tweak to indicate item root
                            lines.extend(sub_lines)
                        else:
                            lines.append(f"{spacer}  - {item}")
                else:
                    lines.append(f"{spacer}- **{human_key}**: {v}")
    elif isinstance(d, list):
        # Check if this is a homogeneous list of dicts that can be formatted as a table
        valid_items = [item for item in d if not _is_empty_or_none(item)]
        if valid_items and all(isinstance(item, dict) for item in valid_items):
            keys = []
            for item in valid_items:
                for k in item.keys():
                    if k not in keys:
                        keys.append(k)
            
            # If we have consistent keys, render as a markdown table
            if keys:
                lines.append("")
                headers = [_clean_human_key(str(k)) for k in keys]
                lines.append(f"{spacer}| " + " | ".join(headers) + " |")
                lines.append(f"{spacer}|" + "|".join(["---"] * len(keys)) + "|")
                for item in valid_items:
                    row = []
                    for k in keys:
                        val = item.get(k, "")
                        # Convert dicts/lists to string representation for table cells
                        if isinstance(val, (dict, list)):
                            val = str(val)
                        row.append(str(val).replace("|", "\\|").replace("\n", " "))
                    lines.append(f"{spacer}| " + " | ".join(row) + " |")
                return lines
                
        # Default list rendering
        for item in d:
            if _is_empty_or_none(item):
                continue
            if isinstance(item, dict):
                lines.extend(format_dict_as_markdown(item, indent))
            else:
                lines.append(f"{spacer}- {item}")
    else:
        if not _is_empty_or_none(d):
            lines.append(f"{spacer}- {d}")
    return lines
