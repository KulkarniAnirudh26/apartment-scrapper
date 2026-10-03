"""Apartment discovery + the persistent tracked-apartments store.

`tracked_apartments.json` is the master list of apartments being tracked. It is
populated explicitly (via --area Places discovery, or --urls-file) and then
reused by every normal scrape run, so the Places API is only ever hit on demand.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from urllib.parse import urlparse

import places

STORE_PATH = os.path.join(os.path.dirname(__file__), "tracked_apartments.json")


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------
def _clean_url(url: str) -> str:
    """Drop tracking query params and fragment; keep scheme://host/path.
    Leasing landing pages work without ?utm_... and it keeps the store tidy."""
    if not url:
        return ""
    if "://" not in url:
        url = "http://" + url
    p = urlparse(url)
    path = p.path.rstrip("/")
    return f"{p.scheme}://{p.netloc}{path}"


def _norm_key(url: str) -> str:
    """Dedupe key: host (no www) + path, lowercased, no query/fragment. Keeps
    distinct communities on a shared management domain separate (different paths)
    while collapsing the same URL with different tracking params."""
    if not url:
        return ""
    if "://" not in url:
        url = "http://" + url
    p = urlparse(url)
    host = (p.netloc or "").lower()
    host = host[4:] if host.startswith("www.") else host
    return host + p.path.rstrip("/").lower()


def load_store(path: str = STORE_PATH) -> list[dict]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_store(records: list[dict], path: str = STORE_PATH) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)


def merge_records(new: list[dict], path: str = STORE_PATH) -> tuple[int, int]:
    """Merge `new` into the store, deduped by website domain (or, when a record
    has no website yet, by place_id). Never drops existing entries. Fills blank
    fields on an existing record from the new one. Returns (added, updated)."""
    store = load_store(path)
    by_url = { _norm_key(r.get("website", "")): r for r in store if r.get("website") }
    by_place = { r.get("place_id"): r for r in store if r.get("place_id") }

    added = updated = 0
    for rec in new:
        key = _norm_key(rec.get("website", ""))
        existing = by_url.get(key) if key else by_place.get(rec.get("place_id"))
        if existing:
            changed = False
            for k, v in rec.items():
                if v and not existing.get(k):
                    existing[k] = v
                    changed = True
            updated += 1 if changed else 0
        else:
            store.append(rec)
            if key:
                by_url[key] = rec
            if rec.get("place_id"):
                by_place[rec["place_id"]] = rec
            added += 1

    save_store(store, path)
    return added, updated


def _now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------
def discover_area(area: str, max_results: int = 20) -> list[dict]:
    """Query Places for apartments in `area` and shape them into store records.
    Records with no website are kept (a resolver may fill them later)."""
    found = places.find_apartments(area, max_results)
    stamp = _now()
    records = []
    for p in found:
        records.append({
            "website": _clean_url(p.get("website", "")),
            "name": p.get("name", ""),
            "address": p.get("address", ""),
            "area": area,
            "place_id": p.get("place_id", ""),
            "discovered_at": stamp,
            "website_source": "places" if p.get("website") else "",
            "website_confidence": "high" if p.get("website") else "",
            "website_status": "ok" if p.get("website") else "missing",
        })
    return records


def records_from_urls(urls: list[str]) -> list[dict]:
    """Turn bare URLs (from --urls-file) into store records (name/address unknown)."""
    stamp = _now()
    return [{
        "website": _clean_url(u.strip()),
        "name": "", "address": "", "area": "", "place_id": "",
        "discovered_at": stamp,
        "website_source": "manual", "website_confidence": "high",
        "website_status": "ok",
    } for u in urls if u.strip()]


# ---------------------------------------------------------------------------
# scrape targets
# ---------------------------------------------------------------------------
def scrape_targets(path: str = STORE_PATH) -> tuple[list[str], dict]:
    """Return (urls, seed_by_url) for every tracked apartment that has a website
    we're willing to scrape (source=places/manual, or a high-confidence resolve).
    seed_by_url maps website -> {name, address} to backfill blocked sites.

    Records carrying "skip": true are left out. Marking beats deleting, because
    merge_records() re-adds anything the store doesn't already have - so a
    deleted entry comes straight back on the next --area run over the same city,
    while a skipped one is found as `existing` and keeps its flag.
    """
    urls: list[str] = []
    seed: dict = {}
    for r in load_store(path):
        url = (r.get("website") or "").strip()
        if not url:
            continue
        if r.get("skip"):
            continue
        # skip low-confidence resolver guesses so we don't scrape wrong sites
        if r.get("website_source") == "resolved" and r.get("website_confidence") != "high":
            continue
        urls.append(url)
        seed[url] = {"name": r.get("name", ""), "address": r.get("address", "")}
    return urls, seed
