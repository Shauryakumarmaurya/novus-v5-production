"""
agents/transcript_nlp.py — Earnings Call Transcript NLP Parser

Two-layer extraction:
  Layer 1: Fast regex pre-filter (zero API cost) — detects hedging phrases,
           guidance language, and analyst question patterns.
  Layer 2: DeepSeek structured extraction (1 API call per transcript) — extracts
           tone shifts, analyst dodges, key phrases, and confidence score.

Designed for scale: results cached per (ticker, transcript_source) to prevent
redundant API calls across multiple agents referencing the same transcript.
"""

import re
import json
import logging
from typing import Optional, Literal
from dataclasses import dataclass, field, asdict

from rag_engine import query as rag_query

logger = logging.getLogger(__name__)


# ── Data Contracts ───────────────────────────────────────────────────────────

@dataclass
class ToneShift:
    topic: str                         # "Rural demand"
    prior_tone: str                    # Direct quote from earlier call
    current_tone: str                  # Direct quote from current call
    shift_direction: str               # "Optimistic → Cautious"
    significance: str = "MEDIUM"       # HIGH/MEDIUM/LOW

    def to_dict(self):
        return asdict(self)


@dataclass
class AnalystDodge:
    analyst_question: str              # The actual question asked
    management_response: str           # The actual response given
    evasion_type: str                  # "Deflection", "Non-quantitative", "Topic pivot"
    missing_data_point: str            # What they SHOULD have answered
    significance: str = "MEDIUM"       # HIGH/MEDIUM/LOW

    def to_dict(self):
        return asdict(self)


@dataclass
class KeyPhrase:
    phrase: str
    context: str
    category: str                      # "margin_defense", "competitive_intensity", etc.
    source_citation: str = ""          # "[Q3 FY25 Call]"

    def to_dict(self):
        return asdict(self)


@dataclass
class TranscriptAnalysis:
    tone_shifts: list = field(default_factory=list)
    analyst_dodges: list = field(default_factory=list)
    key_phrases: list = field(default_factory=list)
    management_confidence_score: int = 50  # 0-100
    summary: str = ""

    def to_dict(self):
        return {
            "tone_shifts": [t.to_dict() if hasattr(t, 'to_dict') else t for t in self.tone_shifts],
            "analyst_dodges": [d.to_dict() if hasattr(d, 'to_dict') else d for d in self.analyst_dodges],
            "key_phrases": [k.to_dict() if hasattr(k, 'to_dict') else k for k in self.key_phrases],
            "management_confidence_score": self.management_confidence_score,
            "summary": self.summary,
        }


# ── Sector-Aware Evasion Phrase Sets ─────────────────────────────────────────

UNIVERSAL_EVASION = [
    "challenging environment", "one-time", "one time", "strategic investment",
    "going forward", "as I said", "let me clarify", "I think we need to",
    "it's too early to", "we'll have to wait", "difficult to predict",
    "cautiously optimistic", "calibrated approach", "headwinds",
    "sustainable profitable growth", "long-term value creation",
    "we remain confident", "broadly in line",
]

SECTOR_EVASION = {
    "fmcg": [
        "controlling controllables",
        "premiumization journey",          # often masks volume decline
        "right-sizing portfolio",          # code for SKU culling
        "calibrated pricing",             # avoids saying "price hike"
        "investing behind brands",        # margin deflection language
        "grammage rationalization",       # shrinkflation
        "building distribution muscle",   # deflection from volume miss
    ],
    "banking": [
        "granular monitoring",
        "proactive provisioning",
        "dispensation period",
        "restructured book",
        "elevated slippages",
        "one-off recovery",
    ],
    "it_services": [
        "deal pipeline robust",
        "seasonal furloughs",
        "ramp-up delays",
        "vendor consolidation opportunity",
        "discretionary spending pause",
    ],
    "pharma": [
        "price erosion environment",
        "regulatory pathway clarity",
        "complex generics portfolio",
    ],
}

# Guidance / forward-looking language patterns
GUIDANCE_PATTERNS = [
    re.compile(r"(?i)(?:we expect|guidance|outlook|target|aim to|going forward).*?(?:growth|margin|revenue|volume|capex)"),
    re.compile(r"(?i)(?:growth|margin|revenue|volume).*?(?:we expect|guidance|outlook|target)"),
    re.compile(r"(?i)(?:next quarter|coming quarter|H[12]|FY\d{2,4}).*?(?:we expect|guidance|target|guide)"),
]

# Analyst question patterns (to detect Q&A structure)
ANALYST_Q_PATTERN = re.compile(
    r"(?i)(?:question|could you|would you|can you|what is|what are|how do you|"
    r"why did|please share|any guidance|any color|any update|elaborate on).*?\?"
)


# ── Layer 1: Regex Pre-filter ────────────────────────────────────────────────

def _detect_sector(sector_str: str) -> str:
    """Map raw sector string to our evasion phrase set key."""
    s = sector_str.lower()
    if any(k in s for k in ["fmcg", "consumer", "food", "personal care", "household"]):
        return "fmcg"
    if any(k in s for k in ["bank", "financial", "nbfc", "insurance"]):
        return "banking"
    if any(k in s for k in ["software", "it ", "technology", "consulting"]):
        return "it_services"
    if any(k in s for k in ["pharma", "drug", "healthcare"]):
        return "pharma"
    return "general"


def regex_prefilter(
    text: str,
    sector: str = "General",
    qa_only: bool = True,
) -> dict:
    """
    Fast regex pass to identify candidate passages.
    Returns structured findings without any API calls.
    """
    # Isolate Q&A section if requested
    analysis_text = text
    if qa_only:
        for pattern in [r"(?i)question\s*(?:and|&)\s*answer", r"(?i)q\s*&\s*a", r"(?i)q&a\s*session"]:
            match = re.search(pattern, text)
            if match:
                analysis_text = text[match.start():]
                break

    sector_key = _detect_sector(sector)
    phrase_list = UNIVERSAL_EVASION + SECTOR_EVASION.get(sector_key, [])

    # 1. Hedging phrase detection
    hedging_found = []
    for phrase in phrase_list:
        count = analysis_text.lower().count(phrase.lower())
        if count > 0:
            idx = analysis_text.lower().find(phrase.lower())
            context = analysis_text[max(0, idx - 100):idx + len(phrase) + 200].strip()
            hedging_found.append({
                "phrase": phrase,
                "count": count,
                "context": context,
                "sector_specific": phrase in SECTOR_EVASION.get(sector_key, []),
            })
    hedging_found.sort(key=lambda x: -x["count"])

    # 2. Guidance language detection
    guidance_found = []
    for pattern in GUIDANCE_PATTERNS:
        for match in pattern.finditer(analysis_text):
            context = analysis_text[max(0, match.start() - 50):match.end() + 150].strip()
            guidance_found.append({
                "passage": context,
                "type": "forward_looking",
            })

    # 3. Analyst question detection
    questions_found = []
    for match in ANALYST_Q_PATTERN.finditer(analysis_text):
        q_start = max(0, match.start() - 50)
        q_end = min(len(analysis_text), match.end() + 300)
        questions_found.append(analysis_text[q_start:q_end].strip())

    return {
        "hedging_phrases": hedging_found[:15],
        "guidance_language": guidance_found[:10],
        "analyst_questions": questions_found[:10],
        "sector_detected": sector_key,
        "total_hedging_instances": sum(h["count"] for h in hedging_found),
    }


# ── Layer 2: LLM Structured Extraction ──────────────────────────────────────

_analysis_cache: dict[tuple[str, str], TranscriptAnalysis] = {}

TRANSCRIPT_EXTRACTION_PROMPT = """You are an institutional-grade earnings call analyst.

Analyze the following Q&A excerpts from {ticker}'s earnings call transcript and extract:

1. **TONE SHIFTS**: Compare management language in this call vs any earlier context. Look for confidence → caution shifts, specific → vague shifts, or vice versa.

2. **ANALYST DODGES**: Identify questions where management:
   - Answered a DIFFERENT question than what was asked (deflection)
   - Gave a qualitative answer when a quantitative one was expected (non-quantitative)
   - Pivoted to a different topic entirely (topic pivot)
   - Used hedging language to avoid commitment

3. **KEY PHRASES**: Flag significant phrases that reveal strategic direction or risk.
   Categories: margin_defense, competitive_intensity, forward_guidance, volume_commentary, pricing_power

4. **MANAGEMENT CONFIDENCE SCORE**: Rate 0-100 based on:
   - Specificity of answers (vague = low, precise = high)
   - Willingness to give forward guidance
   - Consistency with prior commitments
   - Frequency of hedging/evasion language

PRE-FILTER FINDINGS (from automated scan):
{prefilter_summary}

OUTPUT: Return valid JSON ONLY matching this exact structure:
{{
  "tone_shifts": [
    {{"topic": "...", "prior_tone": "...", "current_tone": "...", "shift_direction": "...", "significance": "HIGH|MEDIUM|LOW"}}
  ],
  "analyst_dodges": [
    {{"analyst_question": "...", "management_response": "...", "evasion_type": "...", "missing_data_point": "...", "significance": "HIGH|MEDIUM|LOW"}}
  ],
  "key_phrases": [
    {{"phrase": "...", "context": "...", "category": "...", "source_citation": "..."}}
  ],
  "management_confidence_score": 0-100,
  "summary": "2-3 sentence executive summary of management communication quality"
}}

TRANSCRIPT Q&A EXCERPTS:
{qa_text}"""


def analyze(
    ticker: str,
    scope: str = "latest",
    sector: str = "General",
    document_text: str = "",
) -> dict:
    """
    Run full NLP analysis on earnings call transcript(s).
    
    Args:
        ticker: Company ticker
        scope: "latest", "all", or specific filename
        sector: Company sector for evasion phrase selection
        document_text: Raw transcript text (if available)
    
    Returns: TranscriptAnalysis as dict
    """
    # Check cache
    cache_key = (ticker.upper(), scope)
    if cache_key in _analysis_cache:
        logger.info(f"[TranscriptNLP] Cache hit for {cache_key}")
        return _analysis_cache[cache_key].to_dict()

    # Get transcript text from RAG
    qa_text = ""
    if document_text:
        # Extract Q&A from provided text
        for pattern in [r"(?i)question\s*(?:and|&)\s*answer", r"(?i)q\s*&\s*a"]:
            match = re.search(pattern, document_text)
            if match:
                qa_text = document_text[match.start():match.start() + 10000]
                break
        if not qa_text:
            qa_text = document_text[:10000]
    else:
        # Semantic search for transcript Q&A
        results = rag_query(
            ticker,
            "earnings call transcript question answer analyst management response guidance",
            top_k=8,
        )
        if results:
            qa_text = "\n\n---\n\n".join(r["text"][:1500] for r in results)

    if not qa_text:
        empty = TranscriptAnalysis(summary="No transcript data available for analysis.")
        return empty.to_dict()

    # Layer 1: Regex pre-filter
    prefilter = regex_prefilter(qa_text, sector, qa_only=False)  # already Q&A isolated

    # Layer 2: LLM structured extraction
    try:
        from llm_clients import call_gemini

        prefilter_summary = (
            f"Hedging instances: {prefilter['total_hedging_instances']}\n"
            f"Top phrases: {', '.join(h['phrase'] for h in prefilter['hedging_phrases'][:5])}\n"
            f"Guidance statements found: {len(prefilter['guidance_language'])}\n"
            f"Analyst questions detected: {len(prefilter['analyst_questions'])}"
        )

        prompt = TRANSCRIPT_EXTRACTION_PROMPT.format(
            ticker=ticker,
            prefilter_summary=prefilter_summary,
            qa_text=qa_text[:8000],  # Cap to avoid token overflow
        )

        response = call_gemini(
            "You are a financial transcript analyst. Output valid JSON only.",
            prompt,
        )

        if response and not response.startswith("Error"):
            # Parse JSON response
            clean = response.strip()
            if '```json' in clean:
                clean = clean.split('```json', 1)[1].rsplit('```', 1)[0]
            elif '```' in clean:
                clean = clean.split('```', 1)[1].rsplit('```', 1)[0]

            parsed = json.loads(clean.strip())

            analysis = TranscriptAnalysis(
                tone_shifts=[ToneShift(**t) for t in parsed.get("tone_shifts", [])],
                analyst_dodges=[AnalystDodge(**d) for d in parsed.get("analyst_dodges", [])],
                key_phrases=[KeyPhrase(**k) for k in parsed.get("key_phrases", [])],
                management_confidence_score=parsed.get("management_confidence_score", 50),
                summary=parsed.get("summary", ""),
            )

            # Cache result
            _analysis_cache[cache_key] = analysis
            logger.info(f"[TranscriptNLP] Analysis complete for {ticker}: "
                       f"{len(analysis.tone_shifts)} shifts, {len(analysis.analyst_dodges)} dodges")
            return analysis.to_dict()

    except json.JSONDecodeError as e:
        logger.error(f"[TranscriptNLP] JSON parse failed: {e}")
    except Exception as e:
        logger.error(f"[TranscriptNLP] LLM extraction failed: {e}")

    # Fallback: return regex-only results
    analysis = TranscriptAnalysis(
        key_phrases=[
            KeyPhrase(
                phrase=h["phrase"],
                context=h["context"][:200],
                category="hedging_language",
                source_citation=f"[{ticker} Transcript]",
            )
            for h in prefilter["hedging_phrases"][:5]
        ],
        management_confidence_score=max(10, 80 - prefilter["total_hedging_instances"] * 5),
        summary=f"Regex-only analysis: {prefilter['total_hedging_instances']} hedging instances detected. LLM extraction unavailable.",
    )
    _analysis_cache[cache_key] = analysis
    return analysis.to_dict()
