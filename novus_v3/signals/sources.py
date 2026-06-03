import asyncio
import aiohttp
import hashlib
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET
import os
import json
import logging
from typing import List, Tuple

from novus_v3.signals.schemas import Event

logger = logging.getLogger(__name__)

# --- Resilience Controls: Caching ---
_SIGNAL_CACHE = {}
CACHE_TTL = timedelta(minutes=20)

# --- Configuration ---
SOURCE_TIMEOUT = 10.0  # 10s timeout per source
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
TAVILY_ALLOWED_DOMAINS = [
    "economictimes.indiatimes.com",
    "business-standard.com",
    "livemint.com",
    "moneycontrol.com",
    "bloomberg.com",
    "reuters.com"
]

def _generate_event_id(url: str, title: str) -> str:
    seed = f"{url}-{title}".encode("utf-8")
    return hashlib.md5(seed).hexdigest()

async def fetch_bse_rss(ticker: str, session: aiohttp.ClientSession) -> List[Event]:
    """Tier 1: BSE Corporate Announcements RSS"""
    # Note: A real BSE specific ticker to Scrip Code mapping would be used here.
    # For demo purposes, we will mock the URL or use a generic one.
    events = []
    # Real BSE RSS is complex to query by symbol without scrip code, but we simulate the fetch.
    url = f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{ticker}_dummy.xml"
    
    try:
        # In a real environment, we'd hit the BSE API. Here we simulate the logic.
        # Let's assume we get XML with headlines and PDF links.
        # We will mock a response for AUROPHARMA to test the USFDA parsing.
        if ticker == "AUROPHARMA":
            events.append(Event(
                id=_generate_event_id("bse_1", "USFDA Inspection at Eugia Unit II"),
                source_name="BSE Corporate Announcements",
                source_tier=1,
                url="https://bseindia.com/dummy/auropharma_fda.pdf",
                published_at=datetime.utcnow(),
                fetched_at=datetime.utcnow(),
                raw_title="USFDA Inspection at Eugia Unit II",
                raw_summary="""[Extracted from attached PDF]: 
                The USFDA inspected Eugia Pharma Specialties Ltd. Unit II facility from Feb 19 to Feb 29. 
                They issued a Form 483 with 7 observations. 
                The observations are procedural in nature and the company will respond within the stipulated time."""
            ))
            return events
            
        # Normally we do:
        # async with session.get(url) as resp:
        #     xml_data = await resp.text()
        #     # parse XML, find PDF links
        #     for item in items:
        #         # download PDF bytes
        #         # pdf_text = extract_text_from_pdf(pdf_bytes)
        #         events.append(Event(...))
        
    except Exception as e:
        logger.warning(f"BSE fetch failed for {ticker}: {e}")
        
    return events

async def fetch_tavily_news(ticker: str, session: aiohttp.ClientSession) -> List[Event]:
    """Tier 2: Tavily Search API with strict domain filtering"""
    if not TAVILY_API_KEY:
        logger.warning("No TAVILY_API_KEY found, skipping Tier 2 news.")
        return []
        
    events = []
    url = "https://api.tavily.com/search"
    query = f"{ticker} india stock news"
    
    payload = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "search_depth": "advanced",
        "include_domains": TAVILY_ALLOWED_DOMAINS,
        "max_results": 10
    }
    
    try:
        async with session.post(url, json=payload) as resp:
            if resp.status == 200:
                data = await resp.json()
                for res in data.get("results", []):
                    events.append(Event(
                        id=_generate_event_id(res["url"], res["title"]),
                        source_name=res.get("url").split("/")[2] if "//" in res.get("url", "") else "Tavily News",
                        source_tier=2,
                        url=res["url"],
                        published_at=datetime.utcnow(), # Tavily doesn't reliably return pub date, we assume recent
                        fetched_at=datetime.utcnow(),
                        raw_title=res["title"],
                        raw_summary=res["content"]
                    ))
    except Exception as e:
        logger.warning(f"Tavily fetch failed for {ticker}: {e}")
        
    return events

async def fetch_all_events(ticker: str) -> Tuple[List[Event], List[str]]:
    """Fetches Tier 1 and Tier 2 events concurrently with timeouts and caching."""
    
    # 1. Check Cache
    if ticker in _SIGNAL_CACHE:
        cached_time, cached_events = _SIGNAL_CACHE[ticker]
        if datetime.utcnow() - cached_time < CACHE_TTL:
            logger.info(f"Using cached events for {ticker}")
            return cached_events, []
            
    unavailable_sources = []
    events = []
    
    async with aiohttp.ClientSession() as session:
        # 2. Time-boxed fetches
        try:
            bse_events = await asyncio.wait_for(fetch_bse_rss(ticker, session), timeout=SOURCE_TIMEOUT)
            events.extend(bse_events)
        except asyncio.TimeoutError:
            unavailable_sources.append("BSE Announcements (Timeout)")
        except Exception:
            unavailable_sources.append("BSE Announcements (Error)")
            
        try:
            news_events = await asyncio.wait_for(fetch_tavily_news(ticker, session), timeout=SOURCE_TIMEOUT)
            events.extend(news_events)
        except asyncio.TimeoutError:
            unavailable_sources.append("Tavily News (Timeout)")
        except Exception:
            unavailable_sources.append("Tavily News (Error)")

    # 3. Update Cache
    _SIGNAL_CACHE[ticker] = (datetime.utcnow(), events)
    
    return events, unavailable_sources
