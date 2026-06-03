from pydantic import BaseModel, Field
from typing import List, Literal, Optional
from datetime import datetime

class Event(BaseModel):
    id: str
    source_name: str
    source_tier: int
    url: str
    published_at: datetime
    fetched_at: datetime
    raw_title: str
    raw_summary: str

class Signal(BaseModel):
    id: str
    event_ids: List[str]
    category: Literal["regulatory", "earnings", "management", "litigation", "M&A", "capital_allocation", "macro_sector", "other"]
    direction: Literal["positive", "negative", "neutral"]
    materiality_score: int = Field(ge=0, le=100)
    confidence: Literal["high", "medium", "low"]
    time_horizon: Literal["immediate", "quarters", "structural"]
    highest_source_tier: int
    as_of: datetime
    is_novel: bool
    summary: str

class Impact(BaseModel):
    signal_id: str
    affected_thesis_drivers: List[str]
    direction: Literal["positive", "negative", "neutral"]
    qualitative_magnitude: str
    horizon: str
    what_to_watch: str
    confirms_or_contradicts_thesis: Literal["confirms", "contradicts", "neutral"]
    triggers_kill_criterion_id: Optional[str] = None
    confidence: Literal["high", "medium", "low"]
    evidence_event_ids: List[str]

class SignalPayload(BaseModel):
    signals: List[Signal]
    impacts: List[Impact]
    unavailable_sources: List[str]
