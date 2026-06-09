"""Temporal bleed guard: search_document must pass fiscal filters to RAG."""
import json

import core.tools as tools_mod
from core.tools import build_shared_tools, fiscal_year_window


def test_fiscal_year_window():
    assert fiscal_year_window("Q3_FY26") == ["FY26", "FY25"]
    assert fiscal_year_window("FY24") == ["FY24", "FY23"]
    assert fiscal_year_window("") is None
    assert fiscal_year_window("garbage") is None


def _capture_rag(monkeypatch):
    calls = []

    def fake_rag_query(ticker, query, top_k=5, min_year=None,
                       target_fiscal_period=None, target_fiscal_year=None, **kw):
        calls.append({
            "ticker": ticker,
            "min_year": min_year,
            "target_fiscal_period": target_fiscal_period,
            "target_fiscal_year": target_fiscal_year,
        })
        return [{
            "text": "some chunk", "relevance": 0.9, "chunk_id": "c_1",
            "metadata": {"filename": "ar.pdf", "page": 1, "section": "mdna"},
        }]

    monkeypatch.setattr(tools_mod, "rag_query", fake_rag_query)
    return calls


def test_search_document_defaults_to_recent_window(monkeypatch):
    calls = _capture_rag(monkeypatch)
    reg = build_shared_tools("", {}, ticker="TCS", fiscal_period="Q3_FY26")
    out = json.loads(reg.execute("search_document", {"query": "capex plans"}))
    assert calls[0]["target_fiscal_year"] == ["FY26", "FY25"]
    assert calls[0]["target_fiscal_period"] is None
    assert out[0]["chunk_id"] == "c_1"


def test_search_document_explicit_period_overrides_window(monkeypatch):
    calls = _capture_rag(monkeypatch)
    reg = build_shared_tools("", {}, ticker="TCS", fiscal_period="Q3_FY26")
    reg.execute("search_document", {"query": "guidance", "fiscal_period": "Q2_FY25"})
    assert calls[0]["target_fiscal_period"] == "Q2_FY25"
    assert calls[0]["target_fiscal_year"] is None


def test_search_document_all_periods_opt_out(monkeypatch):
    calls = _capture_rag(monkeypatch)
    reg = build_shared_tools("", {}, ticker="TCS", fiscal_period="Q3_FY26")
    reg.execute("search_document", {"query": "history of capex", "all_periods": True})
    assert calls[0]["target_fiscal_period"] is None
    assert calls[0]["target_fiscal_year"] is None


def test_search_document_min_year_disables_window(monkeypatch):
    calls = _capture_rag(monkeypatch)
    reg = build_shared_tools("", {}, ticker="TCS", fiscal_period="Q3_FY26")
    reg.execute("search_document", {"query": "strategy", "min_year": 2023})
    assert calls[0]["min_year"] == 2023
    assert calls[0]["target_fiscal_year"] is None
