import asyncio
import os
from datetime import datetime
from novus_v3.signals.pipeline import run_signal_pipeline, run_impact_mapping
from core.llm_client import LLMClient

# Mock PM Synthesis output
mock_pm_thesis = {
    "bull_case": [
        "Strong growth in US injectables and complex generics",
        "Margins expanding due to operating leverage"
    ],
    "bear_case": [
        "Regulatory risks at key facilities (Eugia) threatening approvals",
        "Pricing pressure in base US generics business"
    ],
    "kill_criteria": [
        {"id": "kc_1", "criterion": "USFDA issues an OAI or Warning Letter for Eugia Unit II or III"},
        {"id": "kc_2", "criterion": "EBITDA margins drop below 16% for two consecutive quarters"}
    ],
    "upside_triggers": [
        "USFDA clears Eugia Unit II with a VAI or NAI classification",
        "Launch of generic Revlimid accelerates revenue beyond 15%"
    ]
}

async def main():
    ticker = "AUROPHARMA"
    financial_context = "Revenue: Rs 29,000 Cr\nTarget Period: FY25"
    
    print("=== Phase A/B: Running Signal Fetch & Score ===")
    start_time = datetime.now()
    signals, unavailable = await run_signal_pipeline(ticker, financial_context)
    fetch_time = datetime.now() - start_time
    
    print(f"Time taken: {fetch_time.total_seconds():.2f}s")
    print(f"Unavailable Sources: {unavailable}")
    print(f"Signals Found: {len(signals)}")
    
    for s in signals:
        print("\n- SIGNAL -")
        print(s.model_dump_json(indent=2))
        
    print("\n=== Phase D: Running Impact Mapping ===")
    start_time = datetime.now()
    impacts = await run_impact_mapping(signals, mock_pm_thesis, ticker)
    map_time = datetime.now() - start_time
    
    print(f"Time taken: {map_time.total_seconds():.2f}s")
    print(f"Impacts Mapped: {len(impacts)}")
    
    for i in impacts:
        print("\n- IMPACT -")
        print(i.model_dump_json(indent=2))

if __name__ == "__main__":
    asyncio.run(main())
