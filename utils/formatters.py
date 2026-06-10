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


def _format_cell_value(val) -> str:
    """Render a table-cell value as human-readable text.

    Nested dicts/lists (e.g. the critic's structured `citations` list) must
    NOT be emitted via str()/repr — that leaks Python syntax (single quotes,
    `None`) straight into the UI. Citation-like dicts are summarised as
    «"snippet" — doc_id, p.N»; generic dicts as «key: value» pairs.
    """
    if _is_empty_or_none(val):
        return ""

    if isinstance(val, dict):
        # Citation-shaped dict → readable provenance string.
        if any(key in val for key in ("snippet", "doc_id", "chunk_id", "page")):
            snippet = str(val.get("snippet") or "").strip()
            doc_id = str(val.get("doc_id") or "").strip()
            page = val.get("page")
            parts = []
            if snippet:
                parts.append(f'"{snippet}"')
            locus = []
            if doc_id:
                locus.append(doc_id)
            if page not in (None, "", "None"):
                locus.append(f"p.{page}")
            if locus:
                parts.append("— " + ", ".join(locus))
            return " ".join(parts) if parts else ""
        # Generic dict → "key: value" pairs, suppressing empties.
        pairs = [
            f"{_clean_human_key(str(k))}: {_format_cell_value(v)}"
            for k, v in val.items()
            if not _is_empty_or_none(v)
        ]
        return "; ".join(pairs)

    if isinstance(val, list):
        rendered = [_format_cell_value(item) for item in val if not _is_empty_or_none(item)]
        rendered = [r for r in rendered if r]
        return " • ".join(rendered)

    return str(val)

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
                        # Render nested dicts/lists as readable text, never repr().
                        cell = _format_cell_value(val) if isinstance(val, (dict, list)) else str(val)
                        row.append(cell.replace("|", "\\|").replace("\n", " "))
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
