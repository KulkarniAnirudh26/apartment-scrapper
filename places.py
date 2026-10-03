"""Minimal Google Places (New) Text Search client — apartment discovery.

Reuses the same v1 endpoint as maps-lead-finder/backend/places.py, but sync
(the scraper is sync) and with a trimmed field mask: name, address, website —
NO phone (we scrape the phone from the site). Only called on explicit --area.
"""
from __future__ import annotations

import os
import time

import httpx

TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

# Billed by field-mask group. websiteUri is required (it's the whole point) and
# is an Enterprise-tier field; we drop phone to avoid requesting more than needed.
FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.websiteUri",
    "nextPageToken",
])

PAGE_SIZE = 20          # Places max per page
MAX_CAP = 60            # Places allows up to ~60 via pagination


class PlacesError(RuntimeError):
    """Raised on missing key or an API error."""


def _load_key() -> str:
    """Env var first; fall back to the maps-lead-finder key so there's no setup."""
    key = os.getenv("GOOGLE_PLACES_API_KEY")
    if key:
        return key
    # fallback: read sibling project's .env without importing it
    sibling = os.path.join(
        os.path.dirname(__file__), "..", "maps-lead-finder", "backend", ".env"
    )
    try:
        with open(os.path.abspath(sibling), "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("GOOGLE_PLACES_API_KEY="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val
    except OSError:
        pass
    raise PlacesError(
        "No GOOGLE_PLACES_API_KEY found. Set it in apartment-scraper/.env "
        "(or in maps-lead-finder/backend/.env)."
    )


def find_apartments(area: str, max_results: int = 20) -> list[dict]:
    """Text-search apartments in `area`. Returns dicts with name/address/website/
    place_id (website may be '' when Places has none)."""
    max_results = max(1, min(max_results, MAX_CAP))
    api_key = _load_key()
    query = f"apartments for rent in {area}"

    out: list[dict] = []
    page_token = None
    with httpx.Client(timeout=20) as client:
        while len(out) < max_results:
            payload: dict = {"textQuery": query, "pageSize": PAGE_SIZE}
            if page_token:
                payload["pageToken"] = page_token
            try:
                resp = client.post(
                    TEXT_SEARCH_URL,
                    headers={
                        "Content-Type": "application/json",
                        "X-Goog-Api-Key": api_key,
                        "X-Goog-FieldMask": FIELD_MASK,
                    },
                    json=payload,
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                try:
                    msg = e.response.json().get("error", {}).get("message", str(e))
                except Exception:
                    msg = str(e)
                raise PlacesError(f"Places API error: {msg}") from e
            except httpx.HTTPError as e:
                raise PlacesError(f"Places request failed: {e}") from e

            body = resp.json()
            for p in body.get("places", []):
                out.append(_format(p))
            page_token = body.get("nextPageToken")
            if not page_token:
                break
            # nextPageToken needs a brief moment to become valid
            time.sleep(2)

    return out[:max_results]


def _format(p: dict) -> dict:
    return {
        "name": p.get("displayName", {}).get("text", ""),
        "address": p.get("formattedAddress", ""),
        "website": p.get("websiteUri", "") or "",
        "place_id": p.get("id", ""),
    }
