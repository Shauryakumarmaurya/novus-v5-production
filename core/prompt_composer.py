"""
novus_v3/core/prompt_composer.py — Dynamic Prompt Composition

Replaces hardcoded system prompts with a modular system where:
  1. CORE modules load for every agent
  2. SECTOR modules load based on the company's sector
  3. SIGNAL modules load based on what the extraction pipeline found
  4. AGENT modules load based on which agent is running

Each module is a versioned, testable text block.
Analysts can edit modules without touching Python code.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PromptModule:
    name: str
    content: str
    priority: int = 5            # 1-10, higher = earlier in prompt
    sector_filter: list[str] = field(default_factory=list)   # empty = all sectors
    signal_key: str = ""         # only include when this extraction signal is True
    agent_filter: list[str] = field(default_factory=list)    # empty = all agents


# ═══════════════════════════════════════════════════════════════════════════
# Module Library
# ═══════════════════════════════════════════════════════════════════════════

MODULES: dict[str, PromptModule] = {}

def _reg(m: PromptModule):
    MODULES[m.name] = m

# ── CORE (always included) ───────────────────────────────────────────────

_reg(PromptModule(
    name="core_tool_use",
    priority=10,
    content=(
        "You have access to tools that search documents, retrieve financial data, "
        "and compute ratios using Python. USE THESE TOOLS — do not guess, assume, "
        "or calculate in your head.\n"
        "Investigation strategy:\n"
        "  1. Call list_available_data to understand what data exists\n"
        "  2. Search for the most critical risk areas first\n"
        "  3. Follow leads — if you find a reference to a note/page, go read it\n"
        "  4. Cross-reference findings across different data sources\n"
        "  5. Only output your final JSON when you are confident in your findings"
    ),
))

_reg(PromptModule(
    name="core_output_discipline",
    priority=9,
    content=(
        "OUTPUT RULES:\n"
        "- Missing data → write 'DATA NOT AVAILABLE'. Never fabricate.\n"
        "- Use compute_ratio for ALL numerical calculations. NEVER compute yourself.\n"
        "- Search for CONTRADICTORY evidence before concluding anything.\n"
        "- Severity: LOW (monitor) | MEDIUM (investigate) | HIGH (red flag) | CRITICAL (potential fraud)\n"
        "- Every finding needs a citation: exact passage from the document."
    ),
))

_reg(PromptModule(
    name="core_temporal_fidelity",
    priority=10,
    content=(
        "TIMELINE AND CAUSALITY RULES (CRITICAL):\n"
        "- NEVER attribute a financial performance spike or drop in Quarter X to a corporate event (e.g. auditor change, lawsuit) in Quarter Y unless causality is EXPLICITLY stated in the text.\n"
        "- Financial time series data and qualitative RAG events are often temporally distant. Always VERIFY chronological alignment.\n"
        "- If you draw a causal link between two events, you MUST output a structured JSON block containing \"cause_date\" and \"effect_date\" so the Critic Agent can verify it.\n"
        "  Format: { ..., \"causal_events\": [{\"event\": \"...\", \"cause_date\": \"April 2024\", \"effect_date\": \"Dec 2025\"}] }"
    )
))

_reg(PromptModule(
    name="core_indian_accounting",
    priority=8,
    content=(
        "INDIAN CONTEXT (IndAS / Companies Act 2013):\n"
        "- Fiscal year: April–March. FY24 = Apr 2023 – Mar 2024.\n"
        "- Revenue recognition: IndAS 115 (similar to IFRS 15).\n"
        "- Related Party Transactions: IndAS 24 — check RPT note.\n"
        "- Statutory auditor rotation mandatory every 5 years.\n"
        "- CWIP aging > 2 years must be disclosed per Schedule III.\n"
        "- Contingent liabilities: IndAS 37 — disputed tax demands.\n"
        "- Promoter holding changes: watch for pledge creation/release.\n"
        "- SEBI LODR compliance: board composition, audit committee independence."
    ),
))


# ── SECTOR MODULES ────────────────────────────────────────────────────────

_reg(PromptModule(
    name="sector_banking",
    priority=7,
    sector_filter=["Banking", "NBFC", "Financial_Services"],
    content=(
        "BANKING / NBFC RED FLAGS:\n"
        "- NPA divergence: RBI-assessed vs bank-reported gross NPAs.\n"
        "- IndAS 109 staging: watch Stage 2 buildup (early stress signal).\n"
        "- Income recognition on NPAs — interest should NOT be accrued.\n"
        "- CASA ratio trend: declining CASA = rising funding costs.\n"
        "- Priority Sector Lending shortfall penalties.\n"
        "- Group company lending and evergreening via AIF/MF route.\n"
        "- Provision Coverage Ratio manipulation (selling NPAs before quarter-end).\n"
        "- Treasury gains masking weak core NII growth.\n"
        "KEY METRICS: NIM, CASA ratio, GNPA, NNPA, PCR, Credit Cost, ROA."
    ),
))

_reg(PromptModule(
    name="sector_it_services",
    priority=7,
    sector_filter=["IT_Services", "Technology"],
    content=(
        "IT SERVICES RED FLAGS:\n"
        "- Revenue per employee trend — declining = commoditisation.\n"
        "- Unbilled revenue vs billed revenue — aggressive recognition.\n"
        "- Subcontractor cost as % of revenue — margin dilution.\n"
        "- Large deal TCV vs actual revenue conversion (check 2-3 quarter lag).\n"
        "- Client concentration: top 5/10 client dependency.\n"
        "- Subsidiary/step-down billing (RPT for margin shifting).\n"
        "- Hedge gains in Other Income inflating reported margins.\n"
        "- Attrition rate vs wage hike — margin sustainability.\n"
        "KEY METRICS: Revenue per employee, utilisation, attrition, TCV, book-to-bill."
    ),
))

_reg(PromptModule(
    name="sector_pharma",
    priority=7,
    sector_filter=["Pharma", "Healthcare"],
    content=(
        "PHARMA RED FLAGS:\n"
        "- R&D capitalisation policy — aggressive capitalisation inflates EBITDA.\n"
        "- USFDA Warning Letters / Import Alerts / 483 observations.\n"
        "- API supplier concentration — single-source risk.\n"
        "- ANDA pipeline filed vs approved vs commercialised conversion.\n"
        "- Inventory days spike — channel stuffing before patent expiry.\n"
        "- RPT: API sourcing from promoter entities at inflated prices.\n"
        "- Para IV first-to-file opportunities and litigation reserves.\n"
        "KEY METRICS: R&D as % of revenue, ANDA pipeline count, US vs India mix."
    ),
))

_reg(PromptModule(
    name="sector_fmcg",
    priority=7,
    sector_filter=["FMCG", "Consumer"],
    content=(
        "FMCG RED FLAGS:\n"
        "- Volume vs value growth: price hikes masking demand decline.\n"
        "- Channel inventory: trade loading before quarter-end.\n"
        "- GST credit / scheme expenses shifted below the line.\n"
        "- Distribution reach claims vs actual secondary sales.\n"
        "- Royalty payments to foreign parent (HUL→Unilever, Nestlé→parent).\n"
        "- Ad spend cuts boosting margins — unsustainable.\n"
        "- Rural vs urban mix claims vs actual SKU data.\n"
        "KEY METRICS: Volume growth, distribution reach, ad spend %, EBITDA/tonne."
    ),
))

_reg(PromptModule(
    name="sector_infra_capital_goods",
    priority=7,
    sector_filter=["Infrastructure", "Capital_Goods", "Construction", "Real_Estate"],
    content=(
        "INFRA / CAPITAL GOODS RED FLAGS:\n"
        "- CWIP aging — projects stuck beyond original timelines.\n"
        "- Percentage-of-completion method: revenue recognition before cash collection.\n"
        "- Receivable days > 180 — government payment delays.\n"
        "- Order book quality: repeat orders vs one-time, funded vs unfunded.\n"
        "- Debt-funded execution: interest capitalisation hiding true costs.\n"
        "- Joint venture off-balance-sheet debt.\n"
        "- Land bank revaluation gains in Other Income.\n"
        "KEY METRICS: Order book / revenue ratio, receivable days, debt/equity, CWIP aging."
    ),
))

_reg(PromptModule(
    name="sector_metals_mining",
    priority=7,
    sector_filter=["Metals", "Mining", "Commodities"],
    content=(
        "METALS / MINING RED FLAGS:\n"
        "- Inventory revaluation gains/losses distorting EBITDA.\n"
        "- Hedging gains in Other Income — not core profitability.\n"
        "- Mine closure provisions — are they adequate?\n"
        "- Captive vs purchased raw material cost differential.\n"
        "- Foreign currency borrowing exposure vs revenue currency.\n"
        "- Environmental compliance costs hidden in exceptional items.\n"
        "KEY METRICS: Realisation per tonne, cost per tonne, EBITDA/tonne, reserve life."
    ),
))

_reg(PromptModule(
    name="sector_auto",
    priority=7,
    sector_filter=["Auto", "Automobiles", "Auto_Ancillary"],
    content=(
        "AUTO / AUTO ANCILLARY RED FLAGS:\n"
        "- Dealer inventory buildup: wholesale vs retail sales gap.\n"
        "- Warranty provision adequacy — under-provisioning inflates margins.\n"
        "- EV transition capex: is legacy ICE business being milked?\n"
        "- Customer concentration: dependency on single OEM (ancillary cos.).\n"
        "- Tooling costs: capitalised vs expensed — accounting choice matters.\n"
        "- BS-VI / emission norm compliance costs — one-time or recurring?\n"
        "KEY METRICS: ASP, volumes by segment, EBITDA/vehicle, dealer count."
    ),
))


# ── SIGNAL MODULES (conditional on extraction findings) ───────────────────

_reg(PromptModule(
    name="signal_high_rpt",
    priority=9,
    signal_key="has_rpt_disclosures",
    content=(
        "⚠️ HIGH RPT ALERT: The extraction pipeline found significant related "
        "party transaction disclosures. This is your PRIMARY investigation target.\n"
        "  1. Use compute_ratio to get RPT as % of total revenue\n"
        "  2. Search for the RPT note to get the full breakdown\n"
        "  3. Check if transactions are at arm's length\n"
        "  4. Look for audit committee approval language\n"
        "  5. Compare RPT levels with prior year"
    ),
))

_reg(PromptModule(
    name="signal_auditor_change",
    priority=9,
    signal_key="auditor_changed",
    content=(
        "⚠️ AUDITOR CHANGE: A change in statutory auditor was detected.\n"
        "  1. Check if this is a routine 5-year rotation or premature exit\n"
        "  2. Search for the outgoing auditor's last report — any qualifications?\n"
        "  3. Look for any disagreements between management and outgoing auditor\n"
        "  4. Check the new auditor's profile — any tier downgrade?"
    ),
))

_reg(PromptModule(
    name="signal_contingent_liabilities",
    priority=8,
    signal_key="has_contingent_liabilities",
    content=(
        "⚠️ CONTINGENT LIABILITIES: Material contingent liabilities found.\n"
        "  1. Search for the contingent liabilities note\n"
        "  2. Use compute_ratio to get contingent liabilities as % of net worth\n"
        "  3. Classify: tax disputes, litigation, guarantees, regulatory penalties\n"
        "  4. Check if any disputes have adverse orders pending"
    ),
))

_reg(PromptModule(
    name="signal_high_promoter_pledge",
    priority=9,
    signal_key="promoter_shares_pledged",
    content=(
        "⚠️ PROMOTER PLEDGE: Promoter shares appear to be pledged.\n"
        "  1. Search for 'pledge' and 'encumbrance' in the document\n"
        "  2. What % of promoter holding is pledged?\n"
        "  3. Has the pledge increased or decreased vs prior period?\n"
        "  4. What is the pledge for — corporate guarantee or personal borrowing?"
    ),
))


# ── PM SYNTHESIS DISCIPLINE (only for pm_synthesis agent) ─────────────────

_reg(PromptModule(
    name="pm_synthesis_discipline",
    priority=10,
    agent_filter=["pm_synthesis"],
    content=(
        "PM SYNTHESIS — MANDATORY OUTPUT DISCIPLINE:\n\n"
        "A. VALUATION RIGOUR:\n"
        "   If you set pricing_verdict to 'FAIR' or 'CHEAP', you MUST populate the\n"
        "   'valuation' object with:\n"
        "   - reverse_dcf.wacc_pct, reverse_dcf.terminal_growth_pct, reverse_dcf.implied_fcf_growth_pct\n"
        "   - At least 2 entries in peer_comps[] with ev_ebitda and pe_ttm\n"
        "   - historical_multiple with current vs avg_5yr\n"
        "   If any valuation inputs are missing from the data provided, set\n"
        "   pricing_verdict to 'INSUFFICIENT DATA' and explain what is missing.\n\n"
        "B. ZERO AGENT BLEED-THROUGH:\n"
        "   Your output will be read by a portfolio manager. NEVER use phrases like:\n"
        "   - 'The forensic quant flags...', 'Our narrative decoder confirms...'\n"
        "   - 'The moat analysis shows...', 'Capital allocation review suggests...'\n"
        "   - 'According to our investigation...'\n"
        "   Write as if YOU personally performed all the analysis. State findings\n"
        "   with authority: 'ROIC declined to 9.2%', not 'the quant agent calculated\n"
        "   ROIC at 9.2%'.\n\n"
        "C. FINANCIAL VOCABULARY ONLY:\n"
        "   BANNED phrases and their replacements:\n"
        "   - 'direct contradiction' → 'divergence' or 'negative operating leverage'\n"
        "   - 'Revenue went up but profit went down' → 'Gross margin compressed by Xbps\n"
        "     driven by [input cost inflation / negative operating leverage / rising\n"
        "     finance costs]'\n"
        "   - 'growth is slowing' → 'Revenue CAGR decelerated from X% to Y%'\n"
        "   - 'cash is strong' → 'OCF/EBITDA conversion at X%, FCF yield of Y%'\n"
        "   Every metric claim must include the specific number.\n\n"
        "D. SEGMENTAL INTEGRITY:\n"
        "   NEVER attribute a sub-segment metric (e.g., US Injectables revenue) to\n"
        "   the total geographic segment (e.g., total US revenue = 41%). If you only\n"
        "   have the geographic total, say 'US geography contributes 41%' — do NOT\n"
        "   attach a product sub-segment label to that number unless the source data\n"
        "   explicitly provides the sub-segment split.\n\n"
        "E. STOCK STORY DISCIPLINE:\n"
        "   The 'stock_story' chronology may ONLY come from the PRICE ACTION DOSSIER\n"
        "   in your mandate. Every period, price level, and magnitude you cite must\n"
        "   appear verbatim in that dossier. Causes must be tied to evidence from the\n"
        "   specialist findings or the episode context provided — if an episode has no\n"
        "   identifiable cause in the evidence, write 'cause not identifiable from\n"
        "   available filings'. If NO dossier is present in your mandate, OMIT the\n"
        "   stock_story key entirely and record it under data_gaps. The\n"
        "   continuation_verdict (LIKELY|MIXED|UNLIKELY) must be justified by\n"
        "   'what_must_stay_true' conditions that are individually checkable.\n\n"
        "F. RECOMMENDATION DISCIPLINE:\n"
        "   'recommendation' is the answer to: should the client ADD (new money or\n"
        "   top-up), HOLD (keep but do not add), or SELL (exit)? It must be consistent\n"
        "   with the scoreboard and the bear_case probabilities, and\n"
        "   'recommendation_rationale' must state the single decisive factor in one\n"
        "   sentence — including the new-money vs existing-holder nuance when the two\n"
        "   actions differ."
    ),
))


# ═══════════════════════════════════════════════════════════════════════════
# Composer
# ═══════════════════════════════════════════════════════════════════════════

def compose_prompt(
    agent_name: str,
    agent_role: str,
    agent_output_instruction: str,
    sector: str,
    extraction_signals: dict,
    ticker: str = "",
) -> str:
    """
    Compose a system prompt from relevant modules.
    
    Replaces every hardcoded build_system_prompt() in your agents.
    
    Args:
        agent_name:              e.g. "forensic_investigator"
        agent_role:              Agent-specific role description
        agent_output_instruction: What JSON to output (with example)
        sector:                  e.g. "Banking", "FMCG", "IT_Services"
        extraction_signals:      {"has_rpt_disclosures": True, ...}
        ticker:                  Company ticker
    """
    selected = []

    for module in MODULES.values():
        # Agent filter
        if module.agent_filter and agent_name not in module.agent_filter:
            continue
        # Sector filter — case-insensitive substring match so raw Screener strings
        # like "Fast Moving Consumer Goods" match filters like ["FMCG", "Consumer"]
        if module.sector_filter:
            sector_lower = sector.lower()
            if not any(f.lower() in sector_lower or sector_lower in f.lower()
                       for f in module.sector_filter):
                continue
        # Signal filter
        if module.signal_key and not extraction_signals.get(module.signal_key, False):
            continue
        selected.append(module)

    selected.sort(key=lambda m: -m.priority)

    parts = [agent_role]
    if ticker:
        parts.append(f"\nCOMPANY: {ticker} | SECTOR: {sector}")

    for mod in selected:
        parts.append(mod.content)

    parts.append(agent_output_instruction)
    return "\n\n".join(parts)
