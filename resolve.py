"""Best-effort website resolver: apartment name -> official leasing site.

Only used for discovered entries that have a name but no website. Raw HTTP search
scraping is bot-blocked (measured), so this drives a real Playwright browser and
searches Bing (more headless-tolerant than Google/DDG). Results are confidence-
flagged; callers should only auto-scrape high-confidence hits.
"""
from __future__ import annotations

import base64
import os
import re
import time
from urllib.parse import quote_plus, urlparse, parse_qs

import httpx
from bs4 import BeautifulSoup

from fetch import USER_AGENT

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"

# hosts that are never the apartment's own leasing site
AGGREGATORS = re.compile(
    r"(apartments\.com|zillow|yelp|facebook|google\.|bing\.|microsoft|trulia|"
    r"rent\.com|apartmentguide|padmapper|wikipedia|instagram|linkedin|youtube|"
    r"tripadvisor|mapquest|loopnet|hotpads|realtor\.com|costar|walkscore|"
    r"niche\.com|apartmentlist|redfin|craigslist|twitter|x\.com|pinterest|"
    r"tiktok|glassdoor|bbb\.org|yellowpages)",
    re.I,
)

# generic words that shouldn't count as a distinguishing name token
STOPWORDS = {
    "the", "at", "apartments", "apartment", "apts", "homes", "home", "living",
    "residences", "residence", "luxury", "rentals", "rental", "community",
    "villas", "villa", "flats", "lofts", "place", "park", "square", "court",
}


def _host(url: str) -> str:
    if "://" not in url:
        url = "http://" + url
    h = (urlparse(url).netloc or "").lower()
    return h[4:] if h.startswith("www.") else h


def _name_tokens(name: str) -> list[str]:
    toks = re.findall(r"[a-z0-9]+", name.lower())
    return [t for t in toks if len(t) >= 4 and t not in STOPWORDS]


def _pick(name: str, urls: list[str]) -> tuple[str | None, str]:
    """Choose the best organic result. Prefer a host containing a name token
    (high confidence); else the first non-aggregator result (low confidence)."""
    candidates = [u for u in urls if u.startswith("http") and not AGGREGATORS.search(u)]
    if not candidates:
        return None, ""
    tokens = _name_tokens(name)
    for u in candidates:
        host = _host(u)
        host_flat = host.replace("-", "").replace(".", "")
        if any(t in host_flat for t in tokens):
            return u, "high"
    return candidates[0], "low"


def _decode_bing(href: str) -> str:
    """Bing wraps result links in bing.com/ck/a?...&u=a1<base64url>. Decode to
    the real destination; pass through non-wrapped links unchanged."""
    if "bing.com/ck/a" not in href:
        return href
    u = parse_qs(urlparse(href).query).get("u", [""])[0]
    if u.startswith("a1"):
        b = u[2:]
        b += "=" * (-len(b) % 4)  # restore base64 padding
        try:
            return base64.urlsafe_b64decode(b).decode("utf-8", "ignore")
        except (ValueError, UnicodeDecodeError):
            return ""
    return ""


def _organic_links(html: str) -> list[str]:
    """Extract organic result URLs from a Bing results page (decoding Bing's
    click-tracking redirects)."""
    soup = BeautifulSoup(html, "lxml")
    out: list[str] = []
    for algo in soup.select("li.b_algo"):
        a = algo.select_one("h2 a[href]") or algo.select_one("a[href]")
        if a and a.get("href", "").startswith("http"):
            real = _decode_bing(a["href"])
            if real.startswith("http"):
                out.append(real)
    return out


def _query(name: str, address: str) -> str:
    city = address.split(",")[1].strip() if address.count(",") >= 1 else address
    return f"{name} {city} apartments".strip()


# ---------------------------------------------------------------------------
# Brave Search API — reliable path (no CAPTCHA/cloaking); needs a free key
# ---------------------------------------------------------------------------
def brave_available() -> bool:
    return bool(os.getenv("BRAVE_API_KEY"))


def _brave_links(query: str) -> list[str]:
    key = os.getenv("BRAVE_API_KEY")
    if not key:
        return []
    try:
        resp = httpx.get(
            BRAVE_URL,
            headers={"X-Subscription-Token": key, "Accept": "application/json"},
            params={"q": query, "count": 10, "country": "us"},
            timeout=15,
        )
        resp.raise_for_status()
    except httpx.HTTPError:
        return []
    results = resp.json().get("web", {}).get("results", [])
    return [r.get("url", "") for r in results if r.get("url")]


def resolve_via_brave(name: str, address: str) -> tuple[str | None, str]:
    return _pick(name, _brave_links(_query(name, address)))


def resolve_website(name: str, address: str, page) -> tuple[str | None, str]:
    """Search for `name` in an existing Playwright page. Returns (url|None,
    confidence in {'high','low',''})."""
    city = address.split(",")[1].strip() if address.count(",") >= 1 else address
    query = f"{name} {city} apartments".strip()
    url = f"https://www.bing.com/search?q={quote_plus(query)}&setlang=en-US&cc=US"
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(1200)
        html = page.content()
    except Exception:
        return None, ""
    links = _organic_links(html)
    return _pick(name, links)


def resolve_missing(records: list[dict], max_lookups: int = 15) -> tuple[int, int]:
    """For records with website_status='missing', try to fill the website via
    Bing. Mutates records in place. Returns (resolved_high, still_unresolved).

    Only high-confidence hits set the website + source='resolved'; low-confidence
    guesses are recorded (website_status='low_confidence') but NOT used to scrape.
    """
    targets = [r for r in records if r.get("website_status") == "missing"][:max_lookups]
    if not targets:
        return 0, 0

    resolved = unresolved = 0

    def _record(rec, url, conf):
        nonlocal resolved, unresolved
        if url and conf == "high":
            rec.update(website=url, website_source="resolved",
                       website_confidence="high", website_status="ok")
            resolved += 1
        elif url:  # kept for review; scrape_targets skips low-confidence
            rec.update(website=url, website_source="resolved",
                       website_confidence="low", website_status="low_confidence")
            unresolved += 1
        else:
            rec["website_status"] = "unresolved"
            unresolved += 1

    # Reliable path: Brave Search API (no browser, no cloaking).
    if brave_available():
        for i, rec in enumerate(targets):
            url, conf = resolve_via_brave(rec.get("name", ""), rec.get("address", ""))
            _record(rec, url, conf)
            if i < len(targets) - 1:
                time.sleep(1.1)  # Brave free tier ~1 req/s
        return resolved, unresolved

    # Best-effort fallback: scrape Bing in a real browser (often cloaked -> fails).
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return 0, len(targets)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception:
                return 0, len(targets)
            page = browser.new_context(user_agent=USER_AGENT, locale="en-US").new_page()
            for i, rec in enumerate(targets):
                url, conf = resolve_website(rec.get("name", ""), rec.get("address", ""), page)
                _record(rec, url, conf)
                if i < len(targets) - 1:
                    time.sleep(2.5)
            browser.close()
    except Exception:
        pass
    return resolved, unresolved
