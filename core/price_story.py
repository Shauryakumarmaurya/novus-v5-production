"""
core/price_story.py — Price Action Dossier for the PM Synthesis "stock story".

Fetches ~5 years of weekly closes for an Indian ticker (NSE first, BSE
fallback), detects major decline/rally episodes with a zigzag swing
algorithm, and packages:

  - a structured dict (series + episodes) for the UI price chart
  - a markdown "dossier" block injected into the PM Synthesis mandate so
    the model can explain WHY the stock fell/rose and whether the current
    move is likely to continue — grounded in real dates and magnitudes.

Everything fails soft: any network/symbol/parse failure returns None and
the report simply omits the stock story (PM data-gap protocol covers it).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)

# Swing threshold for an "episode": a reversal of at least this magnitude
# from the running extreme closes the current episode. 25% filters noise
# while catching every drawdown/rally an analyst would actually narrate.
SWING_THRESHOLD = 0.25

# Cap the number of episodes narrated in the dossier (most recent kept).
MAX_EPISODES = 6


def _fiscal_year_label(d: date) -> str:
    """Indian fiscal year label for a calendar date (Apr-Mar). FY26 = Apr 2025 - Mar 2026."""
    fy = d.year + 1 if d.month >= 4 else d.year
    return f"FY{fy % 100:02d}"


def _fiscal_years_spanned(start: date, end: date) -> list[str]:
    """FY of start, FY of end, and every FY whose April-1 boundary falls inside."""
    labels = {_fiscal_year_label(start), _fiscal_year_label(end)}
    y = start.year
    while y <= end.year:
        apr1 = date(y, 4, 1)
        if start <= apr1 <= end:
            labels.add(_fiscal_year_label(apr1))
        y += 1
    return sorted(labels, key=lambda s: int(s[2:]))


def detect_episodes(dates: list[date], closes: list[float], threshold: float = SWING_THRESHOLD) -> list[dict]:
    """Zigzag swing detection over a close-price series.

    Returns chronological episodes:
      {type: "decline"|"rally", start, end, start_price, end_price,
       change_pct, duration_weeks, fiscal_years, ongoing}
    The final (in-progress) move is included with ongoing=True when its
    magnitude is at least half the threshold — the "current move" is
    exactly what the analyst wants explained.
    """
    n = len(closes)
    if n < 8:
        return []

    episodes: list[dict] = []
    pivot = 0           # index where the current episode started
    extreme = 0         # index of the running max (rally) or min (decline)
    trend: Optional[str] = None

    def _close_episode(start_i: int, end_i: int, ep_type: str, ongoing: bool = False):
        if end_i <= start_i:
            return
        sp, ep = closes[start_i], closes[end_i]
        if sp <= 0:
            return
        change = (ep - sp) / sp
        episodes.append({
            "type": ep_type,
            "start": dates[start_i].isoformat(),
            "end": dates[end_i].isoformat(),
            "start_price": round(sp, 2),
            "end_price": round(ep, 2),
            "change_pct": round(change * 100, 1),
            "duration_weeks": max(1, round((dates[end_i] - dates[start_i]).days / 7)),
            "fiscal_years": _fiscal_years_spanned(dates[start_i], dates[end_i]),
            "ongoing": ongoing,
        })

    # While no trend is established, track the running min/max so the first
    # episode anchors on the true extreme rather than the series start.
    flat_min = 0
    flat_max = 0

    for i in range(1, n):
        if trend is None:
            if closes[i] < closes[flat_min]:
                flat_min = i
            if closes[i] > closes[flat_max]:
                flat_max = i
            if closes[flat_min] > 0 and closes[i] / closes[flat_min] - 1 >= threshold:
                pivot, trend, extreme = flat_min, "rally", i
            elif closes[flat_max] > 0 and closes[i] / closes[flat_max] - 1 <= -threshold:
                pivot, trend, extreme = flat_max, "decline", i
            continue

        if trend == "rally":
            if closes[i] >= closes[extreme]:
                extreme = i
            elif closes[i] / closes[extreme] - 1 <= -threshold:
                _close_episode(pivot, extreme, "rally")
                pivot, trend, extreme = extreme, "decline", i
        else:  # decline
            if closes[i] <= closes[extreme]:
                extreme = i
            elif closes[i] / closes[extreme] - 1 >= threshold:
                _close_episode(pivot, extreme, "decline")
                pivot, trend, extreme = extreme, "rally", i

    # The in-progress move (from last pivot to current extreme/last point)
    if trend is not None:
        end_i = n - 1
        sp = closes[pivot]
        if sp > 0 and abs(closes[end_i] / sp - 1) >= threshold / 2:
            _close_episode(pivot, end_i, trend, ongoing=True)

    return episodes


def _pct(a: float, b: float) -> Optional[float]:
    """Percent change from a to b."""
    if a is None or b is None or a == 0:
        return None
    return round((b - a) / a * 100, 1)


def _fmt_inr(v: float) -> str:
    return f"\u20b9{v:,.0f}" if v >= 100 else f"\u20b9{v:,.2f}"


def _build_dossier_text(story: dict) -> str:
    """Render the structured story as a markdown block for the PM mandate."""
    lines = [
        "## PRICE ACTION DOSSIER (verified market data — weekly closes, ~5Y)",
        f"Symbol: {story['symbol']} | As of: {story['as_of']}",
        f"Current price: {_fmt_inr(story['current_price'])}"
        + (f" — {abs(story['pct_off_52w_high'])}% below its 52-week high." if story.get("pct_off_52w_high") is not None else ""),
    ]
    rets = story.get("returns") or {}
    ret_bits = [f"{k.upper()}: {'+' if v >= 0 else ''}{v}%" for k, v in rets.items() if v is not None]
    if ret_bits:
        lines.append("Total returns — " + " | ".join(ret_bits))

    eps = story.get("episodes") or []
    if eps:
        lines.append("")
        lines.append(f"Major price episodes (swings >= {int(SWING_THRESHOLD * 100)}%):")
        for i, e in enumerate(eps, 1):
            arrow = "DECLINE" if e["type"] == "decline" else "RALLY"
            ongoing = " [ONGOING — this is the current move]" if e.get("ongoing") else ""
            lines.append(
                f"{i}. {arrow}: {e['start']} \u2192 {e['end']} | "
                f"{'+' if e['change_pct'] >= 0 else ''}{e['change_pct']}% "
                f"({_fmt_inr(e['start_price'])} \u2192 {_fmt_inr(e['end_price'])}) | "
                f"~{e['duration_weeks']} weeks | spans {', '.join(e['fiscal_years'])}{ongoing}"
            )

    lines += [
        "",
        "INSTRUCTIONS FOR THE STOCK STORY:",
        "- For EACH episode above, explain the most plausible cause(s) using ONLY evidence from "
        "the specialist agent findings, the fiscal-period context provided, and the filings. "
        "Tie each cause to the episode's fiscal years.",
        "- If no evidence explains an episode, say so plainly ('cause not identifiable from "
        "available filings') — NEVER invent events, dates, or price levels.",
        "- For the ONGOING move: state what is driving it and give an explicit continuation "
        "verdict (LIKELY / MIXED / UNLIKELY) with the conditions that must hold.",
        "- Never quote price levels or dates that are not in this dossier.",
    ]
    return "\n".join(lines)


def fetch_price_story(ticker: str) -> Optional[dict]:
    """Fetch ~5Y weekly closes and build the full price story package.

    Returns None on any failure (offline VM, delisted symbol, etc.).
    """
    try:
        import yfinance as yf
    except Exception as e:
        logger.warning(f"[PriceStory] yfinance unavailable: {e}")
        return None

    df = None
    symbol_used = None
    for suffix in (".NS", ".BO"):
        symbol = f"{ticker.upper()}{suffix}"
        try:
            hist = yf.Ticker(symbol).history(period="5y", interval="1wk", auto_adjust=True)
            if hist is not None and len(hist) >= 30 and "Close" in hist.columns:
                df = hist
                symbol_used = symbol
                break
        except Exception as e:
            logger.warning(f"[PriceStory] fetch failed for {symbol}: {e}")
    if df is None:
        logger.warning(f"[PriceStory] No usable price history for {ticker} (.NS/.BO)")
        return None

    try:
        df = df.dropna(subset=["Close"])
        dates = [d.date() if isinstance(d, datetime) else d.to_pydatetime().date() for d in df.index]
        closes = [float(c) for c in df["Close"].tolist()]

        episodes = detect_episodes(dates, closes)
        if len(episodes) > MAX_EPISODES:
            episodes = episodes[-MAX_EPISODES:]

        current = closes[-1]
        as_of = dates[-1]

        # 52-week high from the trailing year of weekly closes
        one_year_ago = date(as_of.year - 1, as_of.month, min(as_of.day, 28))
        trailing = [c for d, c in zip(dates, closes) if d >= one_year_ago]
        high_52w = max(trailing) if trailing else None
        pct_off_high = _pct(high_52w, current) if high_52w else None

        def _ret(years: int) -> Optional[float]:
            cutoff = date(as_of.year - years, as_of.month, min(as_of.day, 28))
            past = [c for d, c in zip(dates, closes) if d <= cutoff]
            base = past[-1] if past else (closes[0] if (as_of - dates[0]).days >= years * 360 else None)
            return _pct(base, current) if base else None

        story = {
            "ticker": ticker.upper(),
            "symbol": symbol_used,
            "currency": "INR",
            "as_of": as_of.isoformat(),
            "current_price": round(current, 2),
            "high_52w": round(high_52w, 2) if high_52w else None,
            "pct_off_52w_high": pct_off_high,
            "returns": {"1y": _ret(1), "3y": _ret(3), "5y": _ret(5)},
            "series": {
                "dates": [d.isoformat() for d in dates],
                "closes": [round(c, 2) for c in closes],
            },
            "episodes": episodes,
        }
        story["dossier"] = _build_dossier_text(story)
        logger.info(
            f"[PriceStory] {symbol_used}: {len(closes)} weekly closes, "
            f"{len(episodes)} episodes detected."
        )
        return story
    except Exception as e:
        logger.warning(f"[PriceStory] Failed to build story for {ticker}: {e}")
        return None
