"""Opt-in Claude fallback (the only token-costing path).

Only invoked when the user passes --use-llm AND the rule-based pass came back
low-confidence. Sends a small cleaned text snippet (not raw HTML) and asks Claude
for structured 1B1B data. Import of `anthropic` is lazy so a normal run never
needs the dependency or an API key.
"""
from __future__ import annotations

import json
import os
import re

from bs4 import BeautifulSoup

from models import SiteResult

# Default: reliable structured extraction. Swap to the haiku id for lower cost.
DEFAULT_MODEL = "claude-sonnet-4-6"
CHEAP_MODEL = "claude-haiku-4-5-20251001"

# cap the text we send per site to keep token use small
MAX_CHARS = 12000

SCHEMA_HINT = """Return ONLY a JSON object with this exact shape:
{
  "community_name": string,
  "address": string,
  "phone": string,
  "email": string,
  "units": [
    {
      "floorplan_name": string,
      "rent_min": number|null,
      "rent_max": number|null,
      "sqft": number|null,
      "availability": "available"|"unavailable"|""
    }
  ]
}
Only include units that are 1 bed / 1 bath (a "1x1"). If none exist, use an empty
"units" list. Use null for unknown numbers and "" for unknown strings. No prose."""


def _clean_text(pages) -> str:
    chunks = []
    for page in pages:
        soup = BeautifulSoup(page.html, "lxml")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        text = re.sub(r"\n{2,}", "\n", soup.get_text("\n", strip=True))
        chunks.append(f"URL: {page.url}\n{text}")
    return ("\n\n---\n\n".join(chunks))[:MAX_CHARS]


def available() -> bool:
    """True if the SDK is importable and a key is set."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def enrich_with_llm(result: SiteResult, pages, model: str = DEFAULT_MODEL) -> SiteResult:
    """Fill gaps in `result` using Claude. Returns the (possibly) updated result.
    On any error, returns the input result unchanged."""
    try:
        import anthropic
    except ImportError:
        result.notes = (result.notes + "; llm unavailable (no anthropic sdk)").strip("; ")
        return result

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    content = _clean_text(pages)
    prompt = (
        "Extract 1-bed/1-bath apartment info from the page text below.\n\n"
        f"{SCHEMA_HINT}\n\n=== PAGE TEXT ===\n{content}"
    )
    try:
        msg = client.messages.create(
            model=model,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:  # network / auth / rate limit
        result.notes = (result.notes + f"; llm error: {e}").strip("; ")
        return result

    raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    data = _parse_json(raw)
    if not data:
        result.notes = (result.notes + "; llm returned no parseable json").strip("; ")
        return result

    # merge: rules win where present, LLM fills blanks
    result.community_name = result.community_name or data.get("community_name", "")
    result.address = result.address or data.get("address", "")
    result.phone = result.phone or data.get("phone", "")
    result.email = result.email or data.get("email", "")

    llm_units = []
    for u in data.get("units", []) or []:
        llm_units.append({
            "floorplan_name": (u.get("floorplan_name") or "")[:80],
            "beds": 1, "baths": 1,
            "rent_min": _as_int(u.get("rent_min")),
            "rent_max": _as_int(u.get("rent_max")),
            "sqft": _as_int(u.get("sqft")),
            "availability": u.get("availability") or "",
        })
    if llm_units and not result.units:
        result.units = llm_units
        result.extraction_method = "llm"
    elif llm_units and result.units:
        result.extraction_method = "mixed"
    result.confident = result.confident or any(
        u.get("rent_min") is not None for u in result.units
    )
    return result


def _as_int(v):
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        m = re.search(r"\d[\d,]*", v)
        if m:
            return int(m.group(0).replace(",", ""))
    return None


def _parse_json(raw: str):
    raw = raw.strip()
    # strip ```json fences if present
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except (json.JSONDecodeError, ValueError):
                return None
    return None
