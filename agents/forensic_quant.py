import time
from typing import Optional
from core.agent_base_v3 import AuditTrail
from .agent_utils import _fget, _reverse_dcf
from rag_engine import query as rag_query


# ── Null-Safe Ratio Calculator ───────────────────────────────────────────────
# Every ratio computation goes through this gate.  If either input is None or
# the denominator is zero the gap is logged and the downstream PM synthesis is
# explicitly told NOT to hallucinate around the missing metric.

def _safe_ratio(
    numerator, denominator, ratio_name: str, data_gaps: list,
    year: str = "", num_label: str = "numerator", den_label: str = "denominator",
) -> Optional[float]:
    """Compute a ratio or log a structured data gap.  Never returns a number
    when either input is None or denominator is zero."""
    if numerator is None:
        data_gaps.append({
            "metric": ratio_name,
            "reason": f"{num_label} is null/missing",
            "year": year,
            "action": "SKIP",
        })
        return None
    if denominator is None:
        data_gaps.append({
            "metric": ratio_name,
            "reason": f"{den_label} is null/missing",
            "year": year,
            "action": "SKIP",
        })
        return None
    if denominator == 0:
        data_gaps.append({
            "metric": ratio_name,
            "reason": f"{den_label} is zero — division undefined",
            "year": year,
            "action": "SKIP",
        })
        return None
    return round(numerator / denominator, 4)


class ForensicQuantV3:
    agent_name = "forensic_quant"

    def execute(self, ticker: str, financial_tables: dict, **kwargs) -> AuditTrail:
        start = time.time()

        # Temporal bleed guard: confine RAG lookups to the active fiscal window
        from core.tools import fiscal_year_window
        self._fiscal_window = fiscal_year_window(kwargs.get("fiscal_period", ""))

        # Data is pre-normalized at the boundary (structured_data_fetcher.py)
        # Internal keys: profit_loss, balance_sheet, cash_flow
        pl = financial_tables.get("profit_loss", {})
        bs = financial_tables.get("balance_sheet", {})
        cf = financial_tables.get("cash_flow", {})
        qr = financial_tables.get("quarterly_results", {})
        
        findings = {}
        data_gaps = []
        flags = []

        years = list(pl.keys())
        if not years:
            data_gaps.append({"metric": "ALL", "reason": "No P&L data available", "year": "", "action": "ABORT"})
            return self._build_trail(ticker, findings, data_gaps, flags, start)

        latest = years[-1]
        latest_pl = pl.get(latest, {})
        latest_bs = bs.get(latest, {})
        latest_cf = cf.get(latest, {})

        # ── Extract base metrics ──
        revenue = _fget(latest_pl, "Revenue", "Sales", "Sales+", "Sales +", "Net Sales", "Revenue from Operations")
        ebit = _fget(latest_pl, "EBIT", "Operating Profit")
        pat = _fget(latest_pl, "Net Profit", "PAT", "Profit after tax")
        total_assets = _fget(latest_bs, "Total Assets")
        
        # Defensive Taxonomy: Screener splits Equity and Reserves
        equity_cap = _fget(latest_bs, "Equity Capital", default=0)
        reserves = _fget(latest_bs, "Reserves", default=0)
        if equity_cap > 0 or reserves > 0:
            total_equity = equity_cap + reserves
        else:
            total_equity = _fget(latest_bs, "Shareholders Funds", "Total Equity", "Equity", default=0)
            
        total_debt = _fget(latest_bs, "Borrowings", "Total Debt", "Long Term Borrowings", default=0)
        cash = _fget(latest_bs, "Cash Equivalents", "Cash and Bank", "Cash", default=0)

        # ── Profitability (DuPont) ──
        try:
            margin = _safe_ratio(pat, revenue, "DuPont:Net Margin", data_gaps, latest, "PAT", "Revenue")
            turnover = _safe_ratio(revenue, total_assets, "DuPont:Asset Turnover", data_gaps, latest, "Revenue", "Total Assets")
            multiplier = _safe_ratio(total_assets, total_equity, "DuPont:Equity Multiplier", data_gaps, latest, "Total Assets", "Total Equity")
            
            if margin is not None and turnover is not None and multiplier is not None:
                roe = round(margin * turnover * multiplier, 4)
                findings["dupont"] = {
                    "roe": roe, "net_margin": margin,
                    "asset_turnover": turnover, "equity_multiplier": multiplier,
                    "primary_driver": "margin" if margin > 0.15 else ("leverage" if multiplier > 2.5 else "turnover"),
                }
        except Exception as e:
            data_gaps.append({"metric": "DuPont", "reason": f"Computation failed: {e}", "year": latest, "action": "SKIP"})

        # ── ROIC ──
        try:
            if ebit is not None:
                nopat = ebit * 0.75
                invested_capital = (total_equity or 0) + (total_debt or 0) - (cash or 0)
                
                # Defensive Taxonomy for FMCG negative working capital
                if invested_capital > 0:
                    if revenue and invested_capital < (revenue * 0.05):
                        findings["roic_latest"] = "Unable to Verify (Potential massive goodwill or negative working capital skewing base)"
                    else:
                        roic = _safe_ratio(nopat, invested_capital, "ROIC", data_gaps, latest, "NOPAT", "Invested Capital")
                        if roic is not None:
                            findings["roic_latest"] = roic
                            wacc = kwargs.get("wacc", 0.12)
                            if roic < wacc:
                                flags.append(f"ROIC ({roic:.1%}) < WACC ({wacc:.1%}) — value destruction")
                else:
                    findings["roic_latest"] = "Unable to Verify (Invested capital is negative/zero)"
            else:
                data_gaps.append({"metric": "ROIC", "reason": "EBIT is null/missing", "year": latest, "action": "SKIP"})
        except Exception as e:
            data_gaps.append({"metric": "ROIC", "reason": f"Computation failed: {e}", "year": latest, "action": "SKIP"})

        # ── Earnings Quality ──
        try:
            ocf = _fget(latest_cf, "Operating Cash Flow", "Cash from Operating", "CFO", "Cash from Operating Activity +", "Cash from Operating Activity")
            depreciation = _fget(latest_pl, "Depreciation", "Depreciation and Amortisation", default=0)
            capex = _fget(latest_cf, "Capital Expenditure", "Purchase of Fixed Assets", "Capex")

            ebitda = (ebit or 0) + (depreciation or 0)
            
            ocf_ebitda = _safe_ratio(ocf, ebitda if ebitda else None, "OCF/EBITDA", data_gaps, latest, "OCF", "EBITDA")
            if ocf_ebitda is not None:
                findings["ocf_ebitda_ratio"] = ocf_ebitda

            if ocf is not None and capex is not None:
                fcf = ocf - abs(capex)
                fcf_pat = _safe_ratio(fcf, pat, "FCF/PAT", data_gaps, latest, "FCF", "PAT")
                if fcf_pat is not None:
                    findings["fcf_pat_ratio"] = fcf_pat
        except Exception as e:
            data_gaps.append({"metric": "Earnings Quality", "reason": f"Computation failed: {e}", "year": latest, "action": "SKIP"})

        # ── Working Capital (CCC) ──
        try:
            inventory = _fget(latest_bs, "Inventories", "Inventory", default=0)
            receivables = _fget(latest_bs, "Trade Receivables", "Debtors", "Receivables", default=0)
            payables = _fget(latest_bs, "Trade Payables", "Sundry Creditors", "Creditors", default=0)
            cogs = _fget(latest_pl, "Cost of Materials", "Cost of Goods Sold", "COGS",
                          "Material Cost", "Raw Material Cost", default=0)

            dio = dso = dpo = ccc = None
            if cogs and cogs > 0 and revenue and revenue > 0:
                dio = round((inventory / cogs) * 365, 1) if inventory else None
                dso = round((receivables / revenue) * 365, 1) if receivables is not None else None
                dpo = round((payables / cogs) * 365, 1) if payables else None
                
                # Sanity bounds check to prevent LLM hallucination on garbage data
                if dio is not None and (dio > 3650 or dio < -3650): dio = None
                if dso is not None and (dso > 3650 or dso < -3650): dso = None
                if dpo is not None and (dpo > 3650 or dpo < -3650): dpo = None

                if dio is not None and dso is not None and dpo is not None:
                    ccc = round(dio + dso - dpo, 1)

            # Screener's consolidated statements omit inventory/receivables/payables
            # line items, but its Ratios table ships the day-counts pre-computed.
            if dio is None and dso is None and dpo is None:
                latest_ratios = financial_tables.get("ratios", {}).get(latest, {})
                dio = _fget(latest_ratios, "Inventory Days")
                dso = _fget(latest_ratios, "Debtor Days")
                dpo = _fget(latest_ratios, "Days Payable")
                ccc = _fget(latest_ratios, "Cash Conversion Cycle")
                if ccc is None and None not in (dio, dso, dpo):
                    ccc = round(dio + dso - dpo, 1)

            if any(v is not None for v in (dio, dso, dpo, ccc)):
                findings["working_capital"] = {"dio": dio, "dso": dso, "dpo": dpo, "ccc_days": ccc}
            else:
                data_gaps.append({"metric": "Working Capital/CCC", "reason": "No WC line items in BS and no Ratios table day-counts", "year": latest, "action": "SKIP"})
        except Exception as e:
            data_gaps.append({"metric": "Working Capital/CCC", "reason": f"Computation failed: {e}", "year": latest, "action": "SKIP"})

        # ── Revenue CAGR ──
        try:
            if len(years) >= 4:
                rev_first = _fget(pl.get(years[0], {}), "Revenue", "Sales+", "Sales +", "Net Sales")
                rev_last = _fget(pl.get(years[-1], {}), "Revenue", "Sales+", "Sales +", "Net Sales")
                if rev_first and rev_last and rev_first > 0 and rev_last > 0:
                    n = len(years) - 1
                    cagr = ((rev_last / rev_first) ** (1 / n) - 1) * 100
                    findings["revenue_cagr"] = {"pct": round(cagr, 2), "years": n}
        except Exception as e:
            data_gaps.append({"metric": "Revenue CAGR", "reason": f"Computation failed: {e}", "year": latest, "action": "SKIP"})

        # ── Leverage ──
        try:
            interest = _fget(latest_pl, "Interest", "Finance Costs", "Interest Expense", default=0)
            ic = _safe_ratio(ebit, interest if interest else None, "Interest Coverage", data_gaps, latest, "EBIT", "Interest")
            if ic is not None:
                findings["interest_coverage"] = ic
                if ic < 3:
                    flags.append(f"Interest coverage {ic}x — debt servicing risk")

            if ebitda and ebitda > 0:
                net_debt = (total_debt or 0) - (cash or 0)
                nd_ebitda = _safe_ratio(net_debt, ebitda, "Net Debt/EBITDA", data_gaps, latest, "Net Debt", "EBITDA")
                if nd_ebitda is not None:
                    findings["net_debt_ebitda"] = nd_ebitda
        except Exception as e:
            data_gaps.append({"metric": "Leverage", "reason": f"Computation failed: {e}", "year": latest, "action": "SKIP"})

        # ── Reverse DCF ──
        try:
            market_cap = kwargs.get("market_cap")
            if market_cap and ocf and capex:
                fcf_base = ocf - abs(capex)
                if fcf_base > 0:
                    wacc = kwargs.get("wacc", 0.12)
                    tg = kwargs.get("terminal_growth", 0.05)
                    implied_g = _reverse_dcf(market_cap, fcf_base, wacc, tg)
                    if implied_g is not None:
                        findings["reverse_dcf_implied_growth"] = implied_g
        except Exception as e:
            data_gaps.append({"metric": "Reverse DCF", "reason": f"Computation failed: {e}", "year": latest, "action": "SKIP"})

        # ── Anomaly Bridge (RAG Integration) ──
        try:
            # 1. Check Quarterly Anomalies
            q_quarters = list(qr.keys())
            if len(q_quarters) >= 2:
                latest_q = q_quarters[-1]
                prev_q = q_quarters[-2]
                curr_pat_q = _fget(qr[latest_q], "Net Profit", "PAT", default=0)
                prev_pat_q = _fget(qr[prev_q], "Net Profit", "PAT", default=0)
                curr_other_q = _fget(qr[latest_q], "Other Income", default=0)
                
                pat_growth_q = None
                if curr_pat_q and prev_pat_q and prev_pat_q > 0:
                    pat_growth_q = (curr_pat_q - prev_pat_q) / prev_pat_q
                
                # Null-safe Other Income / PBT check
                pbt_q = _fget(qr[latest_q], "Profit before tax", "PBT", default=None)
                other_inc_ratio = _safe_ratio(
                    curr_other_q, pbt_q, "Q:Other Income/PBT", data_gaps, latest_q,
                    "Other Income", "PBT"
                )

                if (pat_growth_q and pat_growth_q > 0.30) or (other_inc_ratio and other_inc_ratio > 0.15):
                    res = rag_query(ticker, f"Why did net profit or other income jump heavily in the {latest_q} quarter? exceptional items", top_k=2, target_fiscal_year=getattr(self, "_fiscal_window", None))
                    ex = " | ".join(r['text'][:400] for r in res) if res else "No context found."
                    findings["anomaly_flag"] = f"Quarterly Spike: {latest_q} PAT changed {pat_growth_q:.1%} QoQ. RAG: {ex}"
            
            # 2. Check Annual Anomalies if Quarterly didn't trigger
            if "anomaly_flag" not in findings and len(years) >= 2:
                prev_pl = pl.get(years[-2], {})
                prev_cf = cf.get(years[-2], {})
                prev_pat = _fget(prev_pl, "Net Profit", "PAT", "Profit after tax", default=0)
                curr_pat = _fget(latest_pl, "Net Profit", "PAT", "Profit after tax", default=0)
                
                prev_ocf = _fget(prev_cf, "Operating Cash Flow", "Cash from Operating", "CFO", "Cash from Operating Activity +", "Cash from Operating Activity", default=0)
                curr_ocf = _fget(latest_cf, "Operating Cash Flow", "Cash from Operating", "CFO", "Cash from Operating Activity +", "Cash from Operating Activity", default=0)
                
                if curr_pat and prev_pat and prev_pat > 0:
                    pat_growth = (curr_pat - prev_pat) / prev_pat
                    ocf_growth = (curr_ocf - prev_ocf) / abs(prev_ocf) if prev_ocf else 0
                    
                    if (pat_growth > ocf_growth + 0.10) or (curr_pat > curr_ocf):
                        res = rag_query(ticker, f"Why did net profit grow faster than operating cash flow or exceed it in {latest}? exceptional items, other income", top_k=2, target_fiscal_year=getattr(self, "_fiscal_window", None))
                        ex = " | ".join(r['text'][:400] for r in res) if res else "No context found."
                        findings["anomaly_flag"] = f"Earnings Quality Divergence: {latest} PAT ({curr_pat}) vs OCF ({curr_ocf}). RAG: {ex}"
        except Exception as e:
            flags.append(f"Bridge analysis failed: {e}")

        # Filter out valid zeros
        findings = {k: v for k, v in findings.items() if v is not None}
        findings["flags"] = flags
        findings["data_gaps"] = data_gaps

        return self._build_trail(ticker, findings, data_gaps, flags, start)

    def _build_trail(self, ticker, findings, gaps, flags, start):
        elapsed = round(time.time() - start, 2)
        gap_count = len(gaps)
        confidence = max(0.5, 1.0 - (gap_count * 0.1))
        return AuditTrail(
            agent_name=self.agent_name,
            ticker=ticker,
            findings=findings,
            data_gaps=gaps,
            confidence=round(confidence, 2),
            execution_time_s=elapsed,
            steps=[{"action": "python_computation", "thought": "Pure deterministic calculation with null-safe ratio guards"}],
        )
