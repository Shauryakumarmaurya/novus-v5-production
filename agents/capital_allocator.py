from core.agent_base_v3 import AgentV3
from core.tools import Tool
from .agent_utils import _search_capital, _safe_handler

class CapitalAllocatorV3(AgentV3):
    # Confidence completeness term: structured statements this agent needs.
    REQUIRED_INPUTS = ["profit_loss", "balance_sheet", "cash_flow"]

    @property
    def agent_name(self) -> str:
        return "capital_allocator"

    @property
    def agent_role(self) -> str:
        return (
            "You are the capital allocation analyst for an institutional fund. "
            "You assess whether management is a good STEWARD of shareholder capital. "
            "You look for: empire building (unrelated diversification), M&A quality, "
            "dividend/buyback discipline, and overall capital allocation coherence. "
            "You focus on QUALITATIVE signals from filings — quant metrics like ROIC "
            "are computed separately by the forensic quant agent."
        )

    @property
    def output_example(self) -> str:
        return """{
  "empire_building": {
    "unrelated_acquisitions": [
      {"event": "Acquired D2C beauty brand for Rs 450 Cr", "source_citation": "[Q2 Filings]"}
    ],
    "verdict": "1 unrelated acquisition in 12 months"
  },
  "mna_quality": {
    "acquisitions": [
      {"target": "Nutrition Co", "amount": "Rs 200 Cr", "source_citation": "[AR 2024]"}
    ]
  },
  "capital_return": {
    "dividend_pattern": "Growing — DPS Rs 34 to Rs 39",
    "source_citation": "[Screener / Cash Flow statement]"
  },
  "grade": "B",
  "data_gaps": null
}"""

    def build_agent_tools(self, doc: str, tables: dict, ticker: str = "") -> list[Tool]:
        return [
            Tool(
                name="search_capital_decisions",
                description=(
                    "Search for capital allocation decisions: acquisitions, "
                    "divestments, capex announcements, buyback programs, "
                    "dividend declarations, debt repayment plans."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string"},
                    },
                    "required": ["topic"],
                },
                handler=_safe_handler(lambda topic: _search_capital(doc, topic, ticker)),
            ),
        ]
