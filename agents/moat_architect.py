from core.agent_base_v3 import AgentV3
from core.tools import Tool
from .agent_utils import _search_competitive, _safe_handler

class MoatArchitectV3(AgentV3):
    # Confidence completeness term: needs filings/transcripts to assess the moat.
    REQUIRED_INPUTS = ["document_text"]

    @property
    def agent_name(self) -> str:
        return "moat_architect"

    @property
    def agent_role(self) -> str:
        return (
            "You are an industry strategist assessing competitive moats and pricing "
            "power for an institutional equity fund. Your job is to determine whether "
            "this company's competitive advantages are DURABLE or ERODING. "
            "You must back every claim with specific numerical evidence from the filings. "
            "If you claim 'strong distribution moat', you must cite the exact outlet count."
        )

    @property
    def output_example(self) -> str:
        return """{
  "volume_vs_value": {
    "revenue_growth_pct": 8.2,
    "volume_growth_pct": 2.1,
    "price_driven_pct": 6.1,
    "verdict": "75% of growth is price-driven — real demand is weak"
  },
  "market_share": {
    "trend": "Losing in mass, gaining in premium",
    "evidence": "Mass portfolio declined 2%, premium grew 14% per Q4 transcript",
    "risk": "Premium is 25% of revenue — mass erosion outweighs premium gains"
  },
  "porters_five_forces": {
    "new_entrants":        {"strength": "LOW",    "evidence": "Direct reach 3.8M outlets + Rs 4,500 Cr ad spend creates high barrier"},
    "supplier_power":      {"strength": "MEDIUM", "evidence": "Palm oil is 30% of COGS — commodity with multiple suppliers"},
    "buyer_power":         {"strength": "HIGH",   "evidence": "Mass segment volumes dropped 4% after 6% price hike — high elasticity"},
    "substitutes":         {"strength": "MEDIUM", "evidence": "Private label share grew from 8% to 12% in modern trade"},
    "rivalry":             {"strength": "HIGH",   "evidence": "Category promo intensity up 200 bps YoY per industry data"}
  },
  "moat_durability": "WEAKENING",
  "competitive_advantages": [
    {"advantage": "Distribution moat intact (3.8M outlets)", "source_citation": "[AR 2024 | CEO Letter]"}
  ],
  "data_gaps": null
}"""

    def build_agent_tools(self, doc: str, tables: dict, ticker: str = "") -> list[Tool]:
        sector = tables.get("_sector", "General") if isinstance(tables, dict) else "General"
        
        return [
            Tool(
                name="search_competitive_data",
                description=(
                    "Search for competitive intelligence: market share, "
                    "distribution reach, competitive actions, new launches, "
                    "pricing actions, channel changes."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string", "description": "e.g. 'market share', 'distribution reach', 'new product launches'"},
                    },
                    "required": ["topic"],
                },
                handler=_safe_handler(lambda topic: _search_competitive(doc, topic, ticker)),
            ),
            Tool(
                name="get_segment_breakdown_moat",
                description=(
                    "Extract business segment revenue, margins, and growth. "
                    "Critical for assessing which segments are driving growth "
                    "and where pricing power exists or is eroding."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "period": {"type": "string", "description": "'latest' or specific period like 'Mar 2025'"},
                    },
                    "required": ["period"],
                },
                handler=_safe_handler(lambda period: _get_segments_for_moat(ticker, period, sector)),
            ),
            Tool(
                name="get_volume_vs_price_growth_moat",
                description=(
                    "Extract volume growth vs price/mix growth decomposition. "
                    "This is THE key tool for pricing power analysis: if growth "
                    "is entirely price-driven with flat volumes, the moat is weakening."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "period": {"type": "string", "description": "'latest' or specific period"},
                    },
                    "required": ["period"],
                },
                handler=_safe_handler(lambda period: _get_volume_for_moat(ticker, period, sector)),
            ),
        ]


def _get_segments_for_moat(ticker, period, sector):
    from .segment_extractor import get_segments
    return get_segments(ticker, period, sector)


def _get_volume_for_moat(ticker, period, sector):
    from .segment_extractor import get_volume_price_split
    return get_volume_price_split(ticker, period, sector)

