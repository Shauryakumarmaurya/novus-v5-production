import json

data = {
    "triage_result": "<p>Initial triage indicates strong revenue stability with minor margin pressures due to raw material costs. Core US generics business is tracking well.</p>",
    "forensic_scorecard": {
        "health_score": 88,
        "evasion_score": 12,
        "evasion_log": "<p>Management addressed all major questions directly. No significant dodging on US FDA warning letters or pricing pressure.</p>",
        "critical_alerts": [
            "No aggressive revenue recognition detected.",
            "Minor capitalization of R&D expenses noted, within industry norms."
        ],
        "rating": "Outperform"
    },
    "rag_stats": {
        "total_chunks": 1432,
        "docTypes": "annual_report, investor_presentation, earnings_transcript"
    },
    "agent_trails": {
        "forensic_quant": {
            "findings": """
Analysis complete.
[METRIC: Cash Quality|92%]
[METRIC: Working Capital Cycle|74 days]
[METRIC: ROIC Trend|Improving]
[METRIC: Implied Growth|8.5%]
[METRIC: EBITDA/OCF Match|96%]
[METRIC: FCF_Trend|[1200, 1450, 1600, 1550, 2100]]
[METRIC: Debt_Cash_Position|[{"debt":4000,"cash":2500},{"debt":3800,"cash":3100},{"debt":3500,"cash":4200}]]
[METRIC: Rev_Ebitda_Pat|[{"rev":19000,"ebitda":4000,"pat":2500},{"rev":21000,"ebitda":4500,"pat":2800},{"rev":23500,"ebitda":5200,"pat":3400}]]
[METRIC: DuPont_Components|{"net_margin":14.5,"asset_turnover":0.85,"equity_multiplier":1.4}]
"""
        },
        "confidence_radar": [90, 85, 95, 80, 88, 92]
    }
}

with open("static/sample_report.json", "w") as f:
    json.dump(data, f, indent=4)
