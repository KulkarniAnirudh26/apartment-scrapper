"""Fetching layer: fast HTTP first, headless-browser render only as a fallback.

Neither path costs tokens. `fetch_pages` returns a list of Page objects (landing
page first, then a few relevant subpages like /floorplans, /contact).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# link text / href keywords that usually lead to pricing or contact info
SUBPAGE_KEYWORDS = [
    "floor", "plan", "pricing", "availab", "apartment", "rent", "lease", "contact",
]

# how many subpages (beyond the landing page) to follow
MAX_SUBPAGES = 4
HTTP_TIMEOUT = 20.0


@dataclass
class Page:
    url: str
    html: str
    method: str  # "http" | "playwright"


class BlockedError(Exception):
    """Raised when a site refuses the request (403 / bot wall)."""


def _same_host(base: str, link: str) -> bool:
    try:
        return urlparse(base).netloc == urlparse(link).netloc
    except ValueError:
        return False


def _discover_subpages(base_url: str, html: str) -> list[str]:
    """Return same-host URLs whose link text or href hints at pricing/contact."""
    soup = BeautifulSoup(html, "lxml")
    scored: dict[str, int] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(base_url, href)
        if not absolute.startswith("http") or not _same_host(base_url, absolute):
            continue
        absolute = absolute.split("#")[0]
        if absolute.rstrip("/") == base_url.rstrip("/"):
            continue
        haystack = (href + " " + a.get_text(" ", strip=True)).lower()
        score = sum(1 for kw in SUBPAGE_KEYWORDS if kw in haystack)
        if score:
            scored[absolute] = max(scored.get(absolute, 0), score)
    # highest-scoring first, capped
    ordered = sorted(scored, key=lambda u: scored[u], reverse=True)
    return ordered[:MAX_SUBPAGES]


def _http_get(client: httpx.Client, url: str) -> str:
    try:
        resp = client.get(url)
    except httpx.HTTPError as e:
        raise BlockedError(f"request failed: {e}") from e
    if resp.status_code in (401, 403, 429):
        raise BlockedError(f"HTTP {resp.status_code}")
    resp.raise_for_status()
    return resp.text


def fetch_http(base_url: str) -> list[Page]:
    """Fetch landing page + discovered subpages over plain HTTP."""
    pages: list[Page] = []
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    with httpx.Client(
        headers=headers, timeout=HTTP_TIMEOUT, follow_redirects=True
    ) as client:
        landing = _http_get(client, base_url)
        pages.append(Page(url=base_url, html=landing, method="http"))
        for sub in _discover_subpages(base_url, landing):
            try:
                html = _http_get(client, sub)
                pages.append(Page(url=sub, html=html, method="http"))
            except (BlockedError, httpx.HTTPError):
                continue  # a bad subpage shouldn't sink the whole site
    return pages


def looks_js_dependent(html: str) -> bool:
    """Heuristic: page shell that renders content client-side (little/no text,
    obvious SPA root, or no dollar signs at all)."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    if len(text) < 500:
        return True
    if "$" not in text and not re.search(r"\bsq\s?\.?\s?ft\b", text, re.I):
        return True
    if re.search(r'id="(root|app|__next)"', html) and len(text) < 1500:
        return True
    return False


def fetch_playwright(urls: list[str], discover_from_first: bool = False) -> list[Page]:
    """Render the given URLs in headless Chromium. Returns [] if Playwright or
    the browser binary isn't available (caller logs a warning).

    If `discover_from_first` is set, subpages are discovered from the first
    rendered page and appended to the render queue (used when HTTP-based
    discovery was blocked and the landing page has no pricing itself)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return []

    pages: list[Page] = []
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception:
                # browser binary not installed (`playwright install chromium`)
                return []
            context = browser.new_context(user_agent=USER_AGENT)
            page = context.new_page()

            queue = list(urls)
            rendered_urls: set[str] = set()
            discovered = False
            while queue:
                url = queue.pop(0)
                if url in rendered_urls:
                    continue
                rendered_urls.add(url)
                try:
                    page.goto(url, wait_until="networkidle", timeout=30000)
                except Exception:
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    except Exception:
                        continue
                html = page.content()
                pages.append(Page(url=url, html=html, method="playwright"))
                if discover_from_first and not discovered:
                    discovered = True
                    for sub in _discover_subpages(url, html):
                        if sub not in rendered_urls:
                            queue.append(sub)
            browser.close()
    except Exception:
        return []
    return pages


def render_site(base_url: str) -> list[Page]:
    """Render a site with the browser, discovering floor-plan/contact subpages
    from the rendered landing page. Works even when plain HTTP was blocked."""
    return fetch_playwright([base_url], discover_from_first=True)
