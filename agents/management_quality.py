from core.agent_base_v3 import AgentV3
from core.tools import Tool
from .agent_utils import _search_governance, _safe_handler

class ManagementQualityV3(AgentV3):
    # Confidence completeness term: governance work needs filings text plus the
    # shareholding pattern table (promoter holding / pledge trend).
    REQUIRED_INPUTS = ["document_text", "shareholding"]

    @property
    def agent_name(self) -> str:
        return "management_quality"

    @property
    def agent_role(self) -> str:
        return (
            "You are the management quality analyst for an institutional fund. "
            "You assess GOVERNANCE risk: promoter integrity, board independence, "
            "insider trading patterns, compensation alignment, and KMP stability. "
            "In Indian markets, promoter-driven governance failures (Satyam, DHFL, "
            "Yes Bank) are among the biggest risk factors. Your job is to catch "
            "the early signals before they become front-page news."
        )

    @property
    def output_example(self) -> str:
        return """{
  "promoter_analysis": {
    "holding_pct": 67.2,
    "pledge_pct": 0.0,
    "holding_trend": "Stable for 3 years — no stake sales",
    "insider_transactions": "No significant insider transactions in last 12 months",
    "source_citation": "[Shareholding Pattern table / SAST filings]"
  },
  "board_quality": {
    "independent_directors_pct": 50,
    "meets_sebi_requirement": true,
    "audit_committee_independence": "All independent — compliant",
    "related_directors": "None identified",
    "tenure_risk": "2 independent directors serving > 8 years — possible entrenchment",
    "source_citation": "[Annual Report — Corporate Governance section]"
  },
  "kmp_stability": {
    "cfo_tenure": "3 years — stable",
    "ceo_tenure": "5 years — stable",
    "recent_departures": "Company Secretary resigned Q3 — minor flag",
    "succession_plan": "No formal succession plan disclosed",
    "source_citation": "[Q3 concall / stock exchange filings]"
  },
  "compensation_alignment": {
    "md_compensation_vs_profit": "MD comp Rs 42 Cr on Rs 9,800 Cr PAT — 0.4% — reasonable",
    "variable_vs_fixed": "60% variable — aligned with performance",
    "esos_dilution": "ESOS pool is 0.8% of outstanding shares — minimal dilution",
    "source_citation": "[Annual Report — Remuneration disclosure]"
  },
  "insider_transactions": [
    {"transaction": "CEO bought 10,000 shares in open market", "source_citation": "[SAST Filings]"}
  ],
  "governance_grade": "B+",
  "data_gaps": null
}"""

    def build_agent_tools(self, doc: str, tables: dict, ticker: str = "") -> list[Tool]:
        def _get_shareholding(scope: str = "full", **_ignored) -> dict:
            shareholding = tables.get("shareholding") if isinstance(tables, dict) else None
            if not shareholding:
                return {
                    "shareholding_pattern": "DATA NOT AVAILABLE",
                    "note": "Shareholding table missing from structured feed — record under data_gaps.",
                }
            return {
                "shareholding_pattern_pct": shareholding,
                "note": (
                    "Quarter-keyed holdings (%) by category: Promoters, FIIs, DIIs, "
                    "Government, Public. Use the Promoters row trend for stake "
                    "sale/accumulation analysis. Cite as [Shareholding Pattern table]."
                ),
            }

        return [
            Tool(
                name="search_governance",
                description=(
                    "Search for governance information: board composition, "
                    "promoter holdings, KMP changes, compensation details, "
                    "audit committee, insider transactions, SEBI compliance."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string"},
                    },
                    "required": ["topic"],
                },
                handler=_safe_handler(lambda topic: _search_governance(doc, topic, ticker)),
            ),
            Tool(
                name="get_shareholding_pattern",
                description=(
                    "Return the quarterly shareholding pattern table (promoter, FII, "
                    "DII, government, public holding %). Use this for promoter "
                    "holding trend and stake-sale analysis — do NOT estimate these "
                    "numbers from the document text."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "scope": {"type": "string", "description": "Always 'full' — the entire table is returned."},
                    },
                    "required": [],
                },
                handler=_safe_handler(_get_shareholding),
            ),
        ]
