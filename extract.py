"""Rule-based extraction (zero tokens).

Given the fetched pages for one site, pull out community name, address, contacts,
and any 1-bed/1-bath floor plans. Tries structured/embedded data first (most
reliable), then falls back to DOM/text heuristics.
"""
from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup

from models import SiteResult

# ---------------------------------------------------------------------------
# small regex helpers
# ---------------------------------------------------------------------------
PHONE_RE = re.compile(r"\(?\b\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}\b")
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
# US street address: number + street (starts with a letter) ... City, ST 12345
ADDRESS_RE = re.compile(
    r"\d{1,6}\s+[A-Za-z][\w.\-]*(?:\s+[\w.\-]+){0,5}\s*,?\s*"
    r"[A-Za-z .\-]+,\s*[A-Z]{2}\s*\d{5}(?:-\d{4})?"
)
# rent like $2,495 or $2495 (avoid 5-digit+ noise)
RENT_RE = re.compile(r"\$\s?(\d{1,2},?\d{3})\b")
# sqft: prefer the comma-thousands form (1,064) over a bare 3-4 digit run
SQFT_RE = re.compile(
    r"(\d{1,2},\d{3}|\d{3,4})\s*(?:sq\.?\s?ft|square\s?f(?:ee|oo)t|\bsf\b)", re.I
)


def _money_to_int(s: str) -> int | None:
    s = s.replace(",", "").strip()
    return int(s) if s.isdigit() else None


def _is_one_by_one(text: str) -> bool:
    """True if a blob of text describes a 1 bed / 1 bath plan."""
    t = text.lower()
    bed1 = re.search(r"\b1\s*(?:bed|bd|br|bedroom)s?\b", t) or "1x1" in t or "1 x 1" in t
    bath1 = re.search(r"\b1\s*(?:bath|ba)s?\b", t) or "1x1" in t or "1 x 1" in t
    # exclude explicit 2+ bed/bath that also mentions a "1"
    multi = re.search(r"\b[2-9]\s*(?:bed|bd|br|bedroom)s?\b", t)
    return bool(bed1 and bath1 and not multi)


# ---------------------------------------------------------------------------
# contacts / name / address (searched across all pages)
# ---------------------------------------------------------------------------
def _extract_contacts(pages) -> dict:
    name = address = phone = email = contact_url = ""
    for page in pages:
        soup = BeautifulSoup(page.html, "lxml")

        if not name:
            if soup.title and soup.title.get_text(strip=True):
                name = re.split(r"[|\-–—]", soup.title.get_text(strip=True))[0].strip()
            h1 = soup.find("h1")
            if h1 and h1.get_text(strip=True) and len(h1.get_text(strip=True)) < 80:
                name = name or h1.get_text(strip=True)

        # phone / email from explicit tel:/mailto: links first
        if not phone:
            tel = soup.select_one('a[href^="tel:"]')
            if tel:
                phone = tel["href"].replace("tel:", "").strip()
        if not email:
            mail = soup.select_one('a[href^="mailto:"]')
            if mail:
                email = mail["href"].replace("mailto:", "").split("?")[0].strip()

        if not contact_url and "contact" in page.url.lower():
            contact_url = page.url

        text = soup.get_text(" ", strip=True)
        if not phone:
            m = PHONE_RE.search(text)
            if m:
                phone = m.group(0)
        if not email:
            m = EMAIL_RE.search(text)
            if m:
                email = m.group(0)
        if not address:
            m = ADDRESS_RE.search(text)
            if m:
                address = re.sub(r"\s+", " ", m.group(0)).strip(" ,")

    return {
        "community_name": name,
        "address": address,
        "phone": phone,
        "email": email,
        "contact_url": contact_url,
    }


# ---------------------------------------------------------------------------
# embedded structured data
# ---------------------------------------------------------------------------
def _iter_json_blobs(html: str):
    """Yield parsed JSON objects from JSON-LD and framework state blobs."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text()
        if not raw:
            continue
        try:
            yield json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
    # __NEXT_DATA__ / __NUXT__ style blobs
    for tag in soup.find_all("script"):
        raw = tag.string or ""
        if "__NEXT_DATA__" in (tag.get("id") or "") and raw.strip():
            try:
                yield json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                continue


def _walk(obj):
    """Recursively yield every dict inside a nested JSON structure."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


# keys commonly holding beds/baths/rent/sqft across apartment platforms
BED_KEYS = ("beds", "bedrooms", "bedroomcount", "numbeds", "bed")
BATH_KEYS = ("baths", "bathrooms", "bathroomcount", "numbaths", "bath")
RENT_KEYS = ("rent", "minrent", "maxrent", "startingprice", "price", "marketrent",
             "rentmin", "rentmax", "pricemin", "pricemax", "lowprice", "highprice")
SQFT_KEYS = ("sqft", "squarefeet", "sqfeet", "size", "minsqft", "maxsqft", "area")
NAME_KEYS = ("name", "floorplanname", "planname", "title", "unittype")


def _num(v):
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        m = re.search(r"\d[\d,]*\.?\d*", v)
        if m:
            try:
                return float(m.group(0).replace(",", ""))
            except ValueError:
                return None
    return None


def _extract_units_from_json(html: str) -> list[dict]:
    units: list[dict] = []
    for blob in _iter_json_blobs(html):
        for d in _walk(blob):
            keys = {k.lower(): k for k in d.keys()}
            bed = next((_num(d[keys[k]]) for k in BED_KEYS if k in keys), None)
            bath = next((_num(d[keys[k]]) for k in BATH_KEYS if k in keys), None)
            if bed is None or bath is None:
                continue
            if not (abs(bed - 1) < 0.01 and abs(bath - 1) < 0.01):
                continue
            rents = [_num(d[keys[k]]) for k in RENT_KEYS if k in keys]
            rents = [int(r) for r in rents if r and 500 <= r <= 20000]
            sqfts = [_num(d[keys[k]]) for k in SQFT_KEYS if k in keys]
            sqfts = [int(s) for s in sqfts if s and 200 <= s <= 5000]
            name = next((str(d[keys[k]]) for k in NAME_KEYS
                         if k in keys and isinstance(d[keys[k]], str)), "")
            units.append({
                "floorplan_name": name[:80],
                "beds": 1, "baths": 1,
                "rent_min": min(rents) if rents else None,
                "rent_max": max(rents) if rents else None,
                "sqft": sqfts[0] if sqfts else None,
                "availability": "",
            })
    return _dedupe(units)


# ---------------------------------------------------------------------------
# DOM / text heuristics
# ---------------------------------------------------------------------------
def _extract_units_from_dom(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    units: list[dict] = []
    # candidate floor-plan blocks: elements whose class/id hints at a plan/card
    candidates = soup.find_all(
        lambda t: t.name in ("div", "li", "article", "tr", "section")
        and t.get("class")
        and re.search(r"floor|plan|card|unit|model", " ".join(t.get("class")), re.I)
    )
    seen_text = set()
    for block in candidates:
        text = re.sub(r"\s+", " ", block.get_text(" ", strip=True))
        if len(text) > 600 or text in seen_text:
            continue
        seen_text.add(text)
        if not _is_one_by_one(text):
            continue
        rents = [_money_to_int(m.group(1)) for m in RENT_RE.finditer(text)]
        rents = [r for r in rents if r and 500 <= r <= 20000]
        sq = SQFT_RE.search(text)
        units.append({
            "floorplan_name": text[:60],
            "beds": 1, "baths": 1,
            "rent_min": min(rents) if rents else None,
            "rent_max": max(rents) if rents else None,
            "sqft": _money_to_int(sq.group(1)) if sq else None,
            "availability": _availability(text, bool(rents)),
        })
    return _dedupe(units)


# core "1 bed / 1 bath" marker, tolerating "1 Bed / 1 Bath", "1bd 1ba", "1x1"
CORE_RE = re.compile(
    r"(?:\b1\s*(?:bed|bd|br)s?\b\s*[/,\-]?\s*1\s*(?:bath|ba)s?\b)"
    r"|(?:\b1\s?x\s?1\b)",
    re.I,
)


def _first_rent_range(seg: str):
    """First rent in `seg`, plus a high end if it's written as a range."""
    m = RENT_RE.search(seg)
    if not m:
        return None, None
    lo = _money_to_int(m.group(1))
    tail = seg[m.end(): m.end() + 14]
    m2 = re.match(r"\s*(?:-|to|–|—)\s*\$?\s?(\d{1,2},?\d{3})", tail)
    hi = _money_to_int(m2.group(1)) if m2 else lo
    return lo, hi


def _availability(text: str, has_rent: bool) -> str:
    """Classify a unit's availability from its block text, distinguishing a
    genuine 'call for pricing' from a real extraction miss."""
    t = text.lower()
    if re.search(r"\b0\s+available|sold\s?out|wait\s?list|not\s+available|\bunavailable\b", t):
        return "unavailable"
    if not has_rent and re.search(
        r"call for pric|please call|contact (?:us )?for pric|contact for pric|call for detail", t
    ):
        return "call_for_pricing"
    if has_rent or re.search(r"\bavailab", t):
        return "available"
    return ""


def _plan_name(pre: str) -> str:
    """Best-effort floor-plan label from the text just before the bed/bath marker."""
    m = re.findall(r"(?:plan|floorplan|floor\s*plan|residence|unit|the)\s+[\w\-.]+", pre, re.I)
    if m:
        return m[-1].strip()
    m = re.findall(r"\b\d\s?x\s?\d[a-z]?\b", pre, re.I)
    if m:
        return m[-1].strip()
    return ""


def _extract_units_from_text(html: str) -> list[dict]:
    """Backstop: scan the page's visible text for the recurring 1B1B pattern.
    Handles regular listings like 'Plan 03 1 Bed / 1 Bath 704 Sq. Ft. $3,990'
    that don't sit in a class-tagged card element."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))

    cores = [mm.start() for mm in CORE_RE.finditer(text)]
    units: list[dict] = []
    for i, m in enumerate(CORE_RE.finditer(text)):
        pre = text[max(0, m.start() - 45): m.start()]
        # tight window for sqft (always adjacent to the bed/bath marker)
        seg = text[m.start(): m.start() + 95]
        # reject if a larger bed count appears in the tight segment (mislabelled)
        if re.search(r"\b[2-9]\s*(?:bed|bd|br)s?\b", seg, re.I):
            continue
        sq = SQFT_RE.search(seg)
        # wider window for rent: up to the next 1x1 marker or a 2+ bed marker,
        # capped at 400 chars. RENT_RE only matches 4-5 digit $ amounts, so a
        # nearby $500 deposit / $ special won't be mistaken for rent.
        nxt = cores[i + 1] if i + 1 < len(cores) else len(text)
        after = text[m.end(): nxt]
        big = re.search(r"\b[2-9]\s*(?:bed|bd|br)s?\b", after, re.I)
        rent_end = m.end() + min(len(after), big.start() if big else len(after), 400)
        rent_seg = text[m.end(): rent_end]
        lo, hi = _first_rent_range(rent_seg)
        if sq is None and lo is None:
            continue  # nothing useful near this marker
        units.append({
            "floorplan_name": _plan_name(pre)[:60],
            "beds": 1, "baths": 1,
            "rent_min": lo, "rent_max": hi,
            "sqft": _money_to_int(sq.group(1)) if sq else None,
            "availability": _availability(rent_seg, lo is not None),
        })
    return _dedupe(units)


def _name_prefix(name: str) -> str:
    """Normalized first token of a floor-plan name, for grouping duplicates."""
    m = re.search(r"[a-z0-9]+", (name or "").lower())
    return m.group(0) if m else ""


def _dedupe(units: list[dict]) -> list[dict]:
    """Collapse the same plan appearing twice — e.g. a priced summary row and a
    rentless 'Approx./Furnish This Plan' detail row. Group by
    (beds, baths, sqft, name_prefix); within a group keep the richest row
    (rent present wins; then higher rent_max). Distinct real plans differ in sqft
    so they stay separate."""
    groups: dict[tuple, dict] = {}
    order: list[tuple] = []
    for u in units:
        key = (u.get("beds"), u.get("baths"), u.get("sqft"),
               _name_prefix(u.get("floorplan_name", "")))
        if key not in groups:
            groups[key] = u
            order.append(key)
            continue
        if _richer(u, groups[key]):
            groups[key] = u
    return [groups[k] for k in order]


def _richer(a: dict, b: dict) -> bool:
    """True if unit `a` is a better representative than `b` (prefer having rent,
    then the higher rent_max, then a longer/more specific name)."""
    a_has, b_has = a.get("rent_min") is not None, b.get("rent_min") is not None
    if a_has != b_has:
        return a_has
    if (a.get("rent_max") or 0) != (b.get("rent_max") or 0):
        return (a.get("rent_max") or 0) > (b.get("rent_max") or 0)
    return len(a.get("floorplan_name") or "") > len(b.get("floorplan_name") or "")


BOT_WALL_RE = re.compile(
    r"performing security verification|are (?:not )?a bot|"
    r"verify you are (?:a )?human|cloudflare|captcha|access denied|"
    r"enable javascript to continue",
    re.I,
)


def detect_bot_wall(pages) -> bool:
    """True if the (thin) rendered pages look like an anti-bot challenge."""
    for page in pages:
        soup = BeautifulSoup(page.html, "lxml")
        for t in soup(["script", "style", "noscript"]):
            t.decompose()
        text = soup.get_text(" ", strip=True)
        if len(text) < 1200 and BOT_WALL_RE.search(text):
            return True
    return False


# 1-bed-specific starting price, e.g. "1 Bed $3,494+", "1 Bedroom Starting at $X".
# Excludes generic community ranges like "1 - 3 Bedrooms Starting at $X".
ONE_BED_PRICE_RE = re.compile(
    r"\b1\s*(?:bed|bedroom)s?\b[^$0-9]{0,30}\$\s?(\d{1,2},?\d{3})", re.I
)


def _one_bed_starting_price(pages) -> int | None:
    """The community's own advertised '1 bed starting at $X', used as a floor
    when a unit has no per-unit rent.

    Takes the FIRST such match in document order, not the minimum: big-operator
    pages (Equity, UDR) list many *other* nearby communities each with their own
    '1 Bed $X+' line, so a min would grab a different property's price. The page's
    own community appears first (in the hero/summary)."""
    for page in pages:
        soup = BeautifulSoup(page.html, "lxml")
        for t in soup(["script", "style", "noscript"]):
            t.decompose()
        text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
        m = ONE_BED_PRICE_RE.search(text)
        if m:
            v = _money_to_int(m.group(1))
            if v and 500 <= v <= 20000:
                return v
    return None


# ---------------------------------------------------------------------------
# public entry
# ---------------------------------------------------------------------------
def extract_site(source_url: str, pages) -> SiteResult:
    """Run the full rule-based pass over a site's fetched pages."""
    result = SiteResult(source_url=source_url)
    contacts = _extract_contacts(pages)
    result.community_name = contacts["community_name"]
    result.address = contacts["address"]
    result.phone = contacts["phone"]
    result.email = contacts["email"]
    result.contact_url = contacts["contact_url"]

    units: list[dict] = []
    method = ""
    for page in pages:
        json_units = _extract_units_from_json(page.html)
        if json_units:
            units.extend(json_units)
            method = "embedded"
    if not units:
        # bounded text-pattern scan first — cleaner names/prices than card blocks
        for page in pages:
            text_units = _extract_units_from_text(page.html)
            if text_units:
                units.extend(text_units)
                method = "dom"  # still rule-based; reported as render source below
    if not units:
        # last resort: whole-card DOM blocks (noisier, but catches odd layouts)
        for page in pages:
            dom_units = _extract_units_from_dom(page.html)
            if dom_units:
                units.extend(dom_units)
                method = "dom"

    units = _dedupe(units)

    # Fallback: units with no per-unit rent inherit the community's advertised
    # "1 bed starting at $X" as a floor (rent_min only; rent_max left blank to
    # signal it's a starting price, not an exact quote).
    used_start = False
    if any(u.get("rent_min") is None for u in units):
        start = _one_bed_starting_price(pages)
        if start:
            for u in units:
                if u.get("rent_min") is None and u.get("availability") != "unavailable":
                    u["rent_min"] = start
                    u["availability"] = u.get("availability") or "available"
                    used_start = True

    result.units = units
    # method reflects the render source when a browser was used
    render = "playwright" if any(p.method == "playwright" for p in pages) else ""
    if units:
        result.extraction_method = (render or method) + ("+start" if used_start else "")
    result.confident = any(
        u.get("rent_min") is not None and u.get("sqft") is not None for u in units
    )
    return result
