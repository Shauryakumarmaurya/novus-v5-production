# agents/extraction.py
"""
Agent 2: Extraction Pipeline (Not in original human framework — mandatory for FinLLM)

Type: Vision / OCR / Parser
This is the hardest engineering problem in Indian FinLLM.

Pipeline:
  Raw PDF → page images → Vision LLM (Gemini Flash) → JSON tables
  → Validation Layer → Footnote Extraction → clean_financials.json

Critical Warning: A ROIC calculation is only as good as the "Invested Capital"
number. If your OCR pipeline missed ₹500 Cr of off-balance-sheet guarantees
on page 288, your ROIC is wrong. Build validation layers obsessively.
"""

import os
import json
import fitz  # PyMuPDF
import re
from typing import Optional
from dataclasses import dataclass, field

# Import the existing Gemini caller from the parent module
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_clients import call_gemini


# ── Data Models ──────────────────────────────────────────────────────────────

@dataclass
class ExtractedTable:
    """A single financial table extracted from a PDF page."""
    page_number: int
    table_type: str  # "profit_and_loss", "balance_sheet", "cash_flow", "other"
    data: dict
    confidence: float = 0.0  # 0-1, how confident are we in extraction quality
    footnotes: list[str] = field(default_factory=list)
    validation_warnings: list[str] = field(default_factory=list)


@dataclass
class ExtractionResult:
    """Output of the full extraction pipeline."""
    ticker: str
    tables: list[ExtractedTable] = field(default_factory=list)
    raw_text: str = ""
    qa_sections: list[str] = field(default_factory=list)  # Q&A transcript sections
    contingent_liabilities: list[str] = field(default_factory=list)
    related_party_disclosures: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


# ── PDF Text Extraction (upgraded from existing logic.py) ────────────────────

def extract_text_from_pdfs(files_data: list[bytes]) -> str:
    """
    Extract and combine text from PDF byte streams.
    Uses PyMuPDF for reliable text extraction.
    """
    combined_text = ""
    for file_data in files_data:
        try:
            pdf_doc = fitz.open(stream=file_data, filetype="pdf")
            for page_num in range(pdf_doc.page_count):
                page = pdf_doc.load_page(page_num)
                combined_text += page.get_text()
        except Exception as e:
            print(f"[Extraction] Error processing PDF: {e}")
            continue
    return combined_text


def extract_qa_sections(full_text: str) -> list[str]:
    """
    Extract Q&A sections from earnings call transcripts.
    
    Design principle: For management assessment (Agent 4), we ONLY
    feed Q&A sections, never the polished opening statement.
    The opening statement is CEO PR; the Q&A is where truth leaks.
    """
    qa_sections = []

    # Common patterns for Q&A section headers in Indian earnings transcripts
    qa_patterns = [
        r"(?i)(?:question\s*(?:and|&)\s*answer|q\s*&\s*a|q&a\s*session)",
        r"(?i)(?:analyst\s*q(?:uestion)?s?\s*(?:and|&)\s*a(?:nswer)?s?)",
        r"(?i)(?:interactive\s*session|question\s*session)",
    ]

    for pattern in qa_patterns:
        matches = list(re.finditer(pattern, full_text))
        if matches:
            # Take everything from the first Q&A header to the end
            start_idx = matches[0].start()
            qa_text = full_text[start_idx:]
            qa_sections.append(qa_text)
            break

    # Fallback: if no explicit Q&A header, look for analyst question patterns
    if not qa_sections:
        lines = full_text.split('\n')
        qa_started = False
        qa_buffer = []
        analyst_pattern = re.compile(
            r"(?i)(?:analyst|participant|questioner|moderator)\s*[:\-–]"
        )
        for line in lines:
            if analyst_pattern.search(line):
                qa_started = True
            if qa_started:
                qa_buffer.append(line)

        if qa_buffer:
            qa_sections.append('\n'.join(qa_buffer))

    return qa_sections


def extract_contingent_liabilities(text: str) -> list[str]:
    """
    Search for contingent liabilities, off-balance-sheet items,
    and guarantees in the extracted text.
    
    These are the items buried on page 288 that naive FinLLMs miss.
    """
    findings = []
    patterns = [
        r"(?i)contingent\s*liabilit(?:y|ies)",
        r"(?i)off[\s-]*balance[\s-]*sheet",
        r"(?i)corporate\s*guarantee",
        r"(?i)pending\s*litigation",
        r"(?i)claims\s*(?:against|not\s*acknowledged)",
        r"(?i)disputed\s*(?:tax|demand)",
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, text):
            # Extract surrounding context (200 chars before and after)
            start = max(0, match.start() - 200)
            end = min(len(text), match.end() + 200)
            context = text[start:end].strip()
            findings.append(context)

    return findings


def extract_related_party_info(text: str) -> list[str]:
    """
    Extract related party transaction disclosures.
    Related party revenue > 20% is a kill signal for Agent 1.
    """
    findings = []
    patterns = [
        r"(?i)related\s*party\s*(?:transaction|disclosure|dealing)",
        r"(?i)transaction(?:s)?\s*with\s*(?:related|associated)\s*(?:party|parties|company|companies)",
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, text):
            start = max(0, match.start() - 300)
            end = min(len(text), match.end() + 500)
            context = text[start:end].strip()
            findings.append(context)

    return findings


def extract_audit_red_flags(text: str) -> list[str]:
    """
    Sweep for auditor-level red flags buried in dense legal text where
    semantic search reliably under-retrieves: emphasis of matter paragraphs,
    qualified/adverse opinions, auditor resignations, going-concern doubts.
    """
    findings = []
    patterns = [
        r"(?i)emphasis\s*of\s*matter",
        r"(?i)qualified\s*opinion",
        r"(?i)adverse\s*opinion",
        r"(?i)disclaimer\s*of\s*opinion",
        r"(?i)material\s*uncertainty.{0,60}going\s*concern",
        r"(?i)going\s*concern\s*(?:assumption|doubt|uncertainty)",
        r"(?i)(?:resignation|removal)\s*of\s*(?:the\s*)?(?:statutory\s*)?auditor",
        r"(?i)auditor[s]?\s*(?:has|have|had)?\s*resigned",
        r"(?i)change\s*(?:of|in)\s*(?:the\s*)?statutory\s*auditor",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            start = max(0, match.start() - 200)
            end = min(len(text), match.end() + 300)
            findings.append(text[start:end].strip())
    return findings


def extract_pledge_mentions(text: str) -> list[str]:
    """Sweep for promoter share pledging / encumbrance language."""
    findings = []
    patterns = [
        r"(?i)promoter[s']*\s*(?:share[s]?\s*)?(?:pledge|pledged|encumber)",
        r"(?i)(?:pledge|encumbrance)\s*(?:of|on)\s*(?:promoter|shares)",
        r"(?i)shares?\s*pledged\s*(?:by|with)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            start = max(0, match.start() - 150)
            end = min(len(text), match.end() + 250)
            findings.append(text[start:end].strip())
    return findings


def build_extraction_signals(text: str) -> dict:
    """
    Run all deterministic risk sweeps over a document corpus and return the
    boolean signal dict consumed by prompt_composer's conditional signal
    modules (signal_high_rpt, signal_auditor_change, etc.).

    This is the heuristic pre-pass that runs BEFORE any LLM sees the text —
    regex doesn't hallucinate and doesn't miss because of embedding distance.
    """
    if not text:
        return {}

    contingent = extract_contingent_liabilities(text)
    rpt = extract_related_party_info(text)
    audit_flags = extract_audit_red_flags(text)
    pledges = extract_pledge_mentions(text)

    auditor_changed = any(
        re.search(r"(?i)resign|removal|change", f) for f in audit_flags
    )

    return {
        "has_contingent_liabilities": len(contingent) > 0,
        "has_rpt_disclosures": len(rpt) > 0,
        "auditor_changed": auditor_changed,
        "has_audit_red_flags": len(audit_flags) > 0,
        "promoter_shares_pledged": len(pledges) > 0,
        # Counts for logging/UI — prompt composer only reads the booleans.
        "_counts": {
            "contingent_liabilities": len(contingent),
            "rpt_disclosures": len(rpt),
            "audit_red_flags": len(audit_flags),
            "pledge_mentions": len(pledges),
        },
    }


# ── Vision-based Table Extraction (Gemini Flash) ─────────────────────────────

EXTRACTION_PROMPT = """You are a financial document parser. Extract ALL financial tables 
from this page of an annual report or earnings call document.

Rules:
1. Preserve ALL row labels, column headers, and footnote markers EXACTLY as written.
2. Return as structured JSON with this format:
   {
     "tables": [
       {
         "type": "profit_and_loss" | "balance_sheet" | "cash_flow" | "other",
         "headers": ["Column1", "Column2", ...],
         "rows": [
           {"label": "Row Label", "values": [val1, val2, ...]}
         ],
         "footnotes": ["1. footnote text", ...]
       }
     ]
   }
3. Do NOT infer or fill in missing values. Use null for unclear cells.
4. If no financial table is found on this page, return: {"tables": []}
"""


def extract_tables_with_vision(pdf_bytes: bytes, page_numbers: Optional[list[int]] = None) -> list[ExtractedTable]:
    """
    Use Gemini Vision to extract financial tables from PDF pages.
    
    This is the recommended approach for messy Indian annual reports
    with scanned PDFs, merged cells, and image-rendered tables.
    """
    tables = []

    try:
        pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        print(f"[Extraction] Failed to open PDF: {e}")
        return tables

    if page_numbers is None:
        page_numbers = list(range(pdf_doc.page_count))

    for page_num in page_numbers:
        if page_num >= pdf_doc.page_count:
            continue

        try:
            page = pdf_doc.load_page(page_num)
            page_text = page.get_text()

            # Skip pages with very little text (likely images/charts)
            if len(page_text.strip()) < 50:
                continue

            # For now, use text-based extraction via Gemini
            # In production, render page to image and use Vision API
            response = call_gemini(
                EXTRACTION_PROMPT,
                page_text,
            )

            if response and not response.startswith("Error"):
                # Parse JSON response
                try:
                    # Strip markdown code fences if present
                    clean = response.strip()
                    if '```json' in clean:
                        clean = clean.split('```json', 1)[1].rsplit('```', 1)[0]
                    elif '```' in clean:
                        clean = clean.split('```', 1)[1].rsplit('```', 1)[0]

                    parsed = json.loads(clean.strip())

                    for tbl in parsed.get("tables", []):
                        extracted = ExtractedTable(
                            page_number=page_num + 1,
                            table_type=tbl.get("type", "other"),
                            data={
                                "headers": tbl.get("headers", []),
                                "rows": tbl.get("rows", []),
                            },
                            confidence=0.85,
                            footnotes=tbl.get("footnotes", []),
                        )
                        tables.append(extracted)

                except (json.JSONDecodeError, KeyError) as e:
                    print(f"[Extraction] Failed to parse Gemini response for page {page_num + 1}: {e}")

        except Exception as e:
            print(f"[Extraction] Error processing page {page_num + 1}: {e}")

    return tables


# ── Main Pipeline Entry Point ────────────────────────────────────────────────

def run_extraction_pipeline(ticker: str, files_data: list[bytes]) -> ExtractionResult:
    """
    Run the full extraction pipeline on uploaded PDFs.
    
    Steps:
      1. Text extraction (PyMuPDF)
      2. Q&A section isolation (for Agent 4)
      3. Contingent liabilities scan
      4. Related party disclosure scan
      5. (Optional) Vision-based table extraction for complex PDFs
    """
    result = ExtractionResult(ticker=ticker)

    # Step 1: Extract raw text
    result.raw_text = extract_text_from_pdfs(files_data)
    if not result.raw_text:
        result.data_gaps.append("No text could be extracted from the provided PDFs.")
        return result

    # Step 2: Isolate Q&A sections for Agent 4 (NLP Analyst)
    result.qa_sections = extract_qa_sections(result.raw_text)
    if not result.qa_sections:
        result.data_gaps.append("No Q&A section found in transcripts. Management evasion scoring will be limited.")

    # Step 3: Scan for contingent liabilities
    result.contingent_liabilities = extract_contingent_liabilities(result.raw_text)

    # Step 4: Scan for related party disclosures
    result.related_party_disclosures = extract_related_party_info(result.raw_text)

    # Step 5: Vision-based table extraction (optional, resource-intensive)
    # Uncomment for production use when processing annual reports:
    # for file_data in files_data:
    #     result.tables.extend(extract_tables_with_vision(file_data))

    return result
