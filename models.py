"""Data model for a scraped apartment unit and the CSV column order."""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Optional

# CSV columns, in output order. Keep in sync with UnitRecord fields.
CSV_FIELDS = [
    "source_url",
    "community_name",
    "address",
    "floorplan_name",
    "beds",
    "baths",
    "rent_min",
    "rent_max",
    "sqft",
    "availability",
    "phone",
    "email",
    "contact_url",
    "extraction_method",
    "status",
    "notes",
    "scraped_at",
]

# status values used across the pipeline
STATUS_OK = "ok"                    # at least one 1B1B unit captured
STATUS_NO_1B1B = "no_1b1b_found"    # site parsed, but no 1-bed/1-bath plan
STATUS_UNAVAILABLE = "unavailable"  # 1B1B exists but none currently available
STATUS_NEEDS_JS = "needs_js"        # pricing is JS-only and browser fallback unavailable
STATUS_BLOCKED = "blocked"          # request blocked / forbidden / bot wall
STATUS_ERROR = "error"              # unexpected failure fetching or parsing


@dataclass
class UnitRecord:
    """One CSV row. A site with no 1B1B still emits one row (with a status)."""
    source_url: str
    community_name: str = ""
    address: str = ""
    floorplan_name: str = ""
    beds: Optional[float] = None
    baths: Optional[float] = None
    rent_min: Optional[int] = None
    rent_max: Optional[int] = None
    sqft: Optional[int] = None
    availability: str = ""
    phone: str = ""
    email: str = ""
    contact_url: str = ""
    extraction_method: str = ""     # embedded | dom | playwright | llm | mixed
    status: str = ""
    notes: str = ""
    scraped_at: str = ""

    def to_row(self) -> dict:
        row = asdict(self)
        # CSV-friendly: render None as empty string
        return {k: ("" if row.get(k) is None else row.get(k)) for k in CSV_FIELDS}


@dataclass
class SiteResult:
    """Everything the extractors gather for a single site before CSV normalization."""
    source_url: str
    community_name: str = ""
    address: str = ""
    phone: str = ""
    email: str = ""
    contact_url: str = ""
    # list of dicts: {floorplan_name, beds, baths, rent_min, rent_max, sqft, availability}
    units: list = field(default_factory=list)
    extraction_method: str = ""
    confident: bool = False
    status: str = ""
    notes: str = ""
