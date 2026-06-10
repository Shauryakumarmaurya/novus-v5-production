"""Unit tests for core/price_story.py episode detection (no network)."""

from datetime import date, timedelta

import pytest

from core.price_story import detect_episodes, _fiscal_year_label, _fiscal_years_spanned


def _weekly_dates(n: int, start: date = date(2021, 6, 7)) -> list[date]:
    return [start + timedelta(weeks=i) for i in range(n)]


def _ramp(start: float, end: float, steps: int) -> list[float]:
    return [start + (end - start) * i / (steps - 1) for i in range(steps)]


class TestFiscalYearLabel:
    def test_april_starts_new_fy(self):
        assert _fiscal_year_label(date(2025, 4, 1)) == "FY26"

    def test_march_belongs_to_current_fy(self):
        assert _fiscal_year_label(date(2026, 3, 31)) == "FY26"

    def test_span_includes_intermediate_years(self):
        fys = _fiscal_years_spanned(date(2022, 1, 1), date(2024, 6, 1))
        assert fys == ["FY22", "FY23", "FY24", "FY25"]


class TestDetectEpisodes:
    def test_too_short_series_returns_empty(self):
        dates = _weekly_dates(5)
        assert detect_episodes(dates, [100, 101, 102, 103, 104]) == []

    def test_flat_series_has_no_episodes(self):
        dates = _weekly_dates(60)
        closes = [100 + (i % 3) for i in range(60)]  # +/- 2% noise
        assert detect_episodes(dates, closes) == []

    def test_single_decline_then_rally(self):
        # 100 -> 60 (-40%) over 20 weeks, then 60 -> 110 (+83%) over 40 weeks
        closes = _ramp(100, 60, 20) + _ramp(60, 110, 40)[1:]
        dates = _weekly_dates(len(closes))
        episodes = detect_episodes(dates, closes)

        assert len(episodes) == 2
        decline, rally = episodes
        assert decline["type"] == "decline"
        assert decline["change_pct"] == pytest.approx(-40.0, abs=0.5)
        assert not decline["ongoing"]

        assert rally["type"] == "rally"
        assert rally["change_pct"] == pytest.approx(83.3, abs=1.0)
        assert rally["ongoing"]  # last move runs to the end of the series

    def test_episodes_are_chronological_and_contiguous(self):
        closes = _ramp(100, 150, 30) + _ramp(150, 90, 25)[1:] + _ramp(90, 140, 30)[1:]
        dates = _weekly_dates(len(closes))
        episodes = detect_episodes(dates, closes)

        assert [e["type"] for e in episodes] == ["rally", "decline", "rally"]
        for prev, nxt in zip(episodes, episodes[1:]):
            assert prev["end"] == nxt["start"]  # each episode starts at the prior extreme

    def test_small_swings_below_threshold_ignored(self):
        # 15% swings never cross the 25% threshold
        closes = _ramp(100, 115, 10) + _ramp(115, 100, 10)[1:] + _ramp(100, 115, 10)[1:]
        dates = _weekly_dates(len(closes))
        assert detect_episodes(dates, closes) == []

    def test_fiscal_years_attached(self):
        closes = _ramp(100, 50, 60)  # long decline crossing FY boundaries
        dates = _weekly_dates(60, start=date(2022, 1, 3))
        episodes = detect_episodes(dates, closes)
        assert episodes, "expected at least one episode"
        assert episodes[0]["fiscal_years"][0] == "FY22"
        assert len(episodes[0]["fiscal_years"]) >= 2

    def test_ongoing_move_requires_half_threshold(self):
        # Rally completes, then a -10% pullback: below the 12.5% ongoing cutoff
        closes = _ramp(100, 140, 30) + _ramp(140, 126, 10)[1:]
        dates = _weekly_dates(len(closes))
        episodes = detect_episodes(dates, closes)
        assert [e["type"] for e in episodes] == ["rally"]
