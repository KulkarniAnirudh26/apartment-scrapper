"""Apartment 1B1B rent scraper.

Visits each hardcoded apartment website, extracts 1-bed/1-bath floor plans
(rent, sqft, address, contacts) and writes them to apartments.csv.

Pipeline per site:
  1. HTTP fetch (fast, zero tokens) + rule-based extraction
  2. Playwright render fallback if HTTP found no usable pricing and the page
     looks JS-dependent (zero tokens; skipped if Chromium isn't installed)
  3. Claude fallback ONLY with --use-llm and still low-confidence (spends tokens)

Every site produces at least one CSV row; sites with no 1B1B / unavailable /
blocked get a row carrying whatever was found plus a status.

Each run overwrites a latest-snapshot file (--out, default apartments.csv) AND
appends every row to an append-only history file (--history, default
rent_history.csv) with a shared per-run timestamp, building a rent time series.
Use history.py to summarize/plot that history.

Discovery: pass --area to find apartments in a region via Google Places (the only
command that calls the API), add them to a persistent tracked_apartments.json
store, then scrape. Normal runs reuse that store and never call Places.

Usage:
  python scraper.py                 # scrape the tracked store (no API); + history
  python scraper.py --area "Sunnyvale, CA"          # discover via Places, then scrape
  python scraper.py --area "Cupertino, CA" --max 40 --resolve-missing
  python scraper.py --area "..." --discover-only    # just update the store
  python scraper.py --use-llm       # opt in to the Claude fallback
  python scraper.py --no-browser    # never launch Playwright
  python scraper.py --no-history    # snapshot only, don't append
  python scraper.py --urls https://x.com   # one-off, bypass the store
  python scraper.py --visualize --email    # charts + mail the report (see notify.py)
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import sys
import time

from dotenv import load_dotenv

import fetch
import extract
from models import (
    UnitRecord, SiteResult, CSV_FIELDS,
    STATUS_OK, STATUS_NO_1B1B, STATUS_UNAVAILABLE, STATUS_NEEDS_JS,
    STATUS_BLOCKED, STATUS_ERROR,
)

# ---- edit this list to add/remove communities --------------------------------
URLS = [
    "https://www.irvinecompanyapartments.com/locations/northern-california/santa-clara/monticello.html",
    "https://www.avanasunnyvale.com/",
    "https://www.encasaliving.com/",
    "https://prometheusapartments.com/ca/cupertino-apartments/the-markham",
    
]
# ------------------------------------------------------------------------------


def _now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def scrape_site(url: str, use_llm: bool, use_browser: bool,
                seed: dict | None = None) -> SiteResult:
    """Run the full pipeline for one URL. Never raises.

    `seed` optionally carries {name, address} from Places discovery, used to
    backfill fields the scrape couldn't get (e.g. a bot-blocked site).
    """
    blocked = False
    pages: list = []
    try:
        pages = fetch.fetch_http(url)
    except fetch.BlockedError:
        blocked = True
    except Exception as e:  # DNS, TLS, timeout, malformed
        r = SiteResult(source_url=url, status=STATUS_ERROR, notes=f"fetch error: {e}")
        return _apply_seed(r, seed)

    result = extract.extract_site(url, pages) if pages else SiteResult(source_url=url)

    # ---- Playwright fallback (free) -----------------------------------------
    # Trigger whenever we lack confident pricing, or HTTP was blocked entirely.
    if use_browser and not result.confident:
        reason = "blocked by HTTP" if blocked else "no confident pricing over HTTP"
        print(f"    ...{reason}, rendering with browser")
        rendered = fetch.render_site(url)
        if rendered:
            r2 = extract.extract_site(url, rendered)
            if r2.confident or (not result.units and r2.units) or blocked:
                # keep any contact fields whichever pass found them
                r2.community_name = r2.community_name or result.community_name
                r2.address = r2.address or result.address
                r2.phone = r2.phone or result.phone
                r2.email = r2.email or result.email
                r2.contact_url = r2.contact_url or result.contact_url
                result = r2
                pages = rendered
        elif blocked:
            result.status = STATUS_BLOCKED
            result.notes = (result.notes + "; browser render unavailable").strip("; ")
        else:
            result.notes = (result.notes + "; browser render unavailable").strip("; ")

    # note *why* a site stayed empty when it looks like an anti-bot wall
    if not result.confident and pages and extract.detect_bot_wall(pages):
        result.notes = (result.notes + "; bot/captcha challenge on pricing pages").strip("; ")

    # ---- LLM fallback (opt-in) ----------------------------------------------
    if use_llm and not result.confident and pages:
        print(f"    ...low confidence, using Claude fallback for {url}")
        from llm_extract import enrich_with_llm
        result = enrich_with_llm(result, pages)

    if not result.status:
        result.status = _classify(result, use_browser, blocked)
    if result.status == STATUS_BLOCKED and not result.units and not result.notes:
        result.notes = "site blocks automated access; captured contact info only"
    return _apply_seed(result, seed)


def _apply_seed(result: SiteResult, seed: dict | None) -> SiteResult:
    """Backfill name/address from Places discovery when the scrape lacks them."""
    if seed:
        result.community_name = result.community_name or seed.get("name", "")
        result.address = result.address or seed.get("address", "")
    return result


def _classify(result: SiteResult, use_browser: bool, blocked: bool) -> str:
    if result.units:
        avails = [u.get("availability") for u in result.units]
        if any(a == "available" for a in avails):
            return STATUS_OK
        if avails and all(a == "unavailable" for a in avails):
            return STATUS_UNAVAILABLE
        return STATUS_OK  # found plans; availability just not stated
    if blocked:
        return STATUS_BLOCKED
    # no units found: distinguish "site needs JS but browser was off" from parsed-empty
    if not use_browser:
        return STATUS_NEEDS_JS
    return STATUS_NO_1B1B


def to_rows(result: SiteResult, stamp: str | None = None) -> list[UnitRecord]:
    """Normalize a SiteResult into one or more CSV records.

    `stamp` is the run timestamp shared by every row in a run; defaults to now.
    """
    stamp = stamp or _now()
    common = dict(
        source_url=result.source_url,
        community_name=result.community_name,
        address=result.address,
        phone=result.phone,
        email=result.email,
        contact_url=result.contact_url,
        extraction_method=result.extraction_method,
        status=result.status,
        notes=result.notes,
        scraped_at=stamp,
    )
    if not result.units:
        # single status row still carrying whatever we captured
        status = result.status or STATUS_NO_1B1B
        return [UnitRecord(**{**common, "status": status})]

    rows = []
    for u in result.units:
        rows.append(UnitRecord(
            **common,
            floorplan_name=u.get("floorplan_name", ""),
            beds=u.get("beds"),
            baths=u.get("baths"),
            rent_min=u.get("rent_min"),
            rent_max=u.get("rent_max"),
            sqft=u.get("sqft"),
            availability=u.get("availability", ""),
        ))
    return rows


def _select_targets(args) -> list[tuple]:
    """Resolve what to scrape into a list of (url, seed) pairs.

    - --urls: one-off, exactly those (no store, no Places).
    - --area: call Places (the ONLY place we do), merge into the tracked store,
      optionally resolve missing websites.
    - --urls-file: add those URLs to the store.
    - default: scrape the tracked store UNION the hardcoded seed URLS. No API.
    """
    # one-off override — bypass the store entirely
    if args.urls:
        return [(u, None) for u in args.urls]

    import discover

    if args.urls_file:
        try:
            with open(args.urls_file, "r", encoding="utf-8") as f:
                file_urls = [ln.strip() for ln in f if ln.strip()]
            added, updated = discover.merge_records(discover.records_from_urls(file_urls))
            print(f"Added {added} URL(s) from {args.urls_file} to the tracked store.")
        except OSError as e:
            print(f"Could not read --urls-file: {e}")

    if args.area:
        try:
            records = discover.discover_area(args.area, args.max)
        except Exception as e:  # PlacesError etc.
            print(f"Discovery failed: {e}")
            records = []
        if records:
            with_site = sum(1 for r in records if r.get("website"))
            print(f"Places returned {len(records)} apartments in '{args.area}' "
                  f"({with_site} with a website).")
            if args.resolve_missing:
                import resolve
                got, miss = resolve.resolve_missing(records, args.resolve_max)
                print(f"Resolver: {got} website(s) found, {miss} still unresolved.")
            added, updated = discover.merge_records(records)
            print(f"Store: +{added} new, {updated} updated.")

    # default scrape set: tracked store ∪ hardcoded seed URLS
    store_urls, seed_by_url = discover.scrape_targets()
    targets: list[tuple] = []
    seen = set()
    for u in store_urls + list(URLS):
        key = u.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        targets.append((u, seed_by_url.get(u)))
    if not args.area and not args.urls_file:
        print(f"Using tracked list ({len(targets)} sites); Places not called.")
    return targets


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Scrape 1B1B apartment rents to CSV.")
    parser.add_argument("--use-llm", action="store_true",
                        help="Enable the Claude fallback for stubborn sites (spends tokens).")
    parser.add_argument("--no-browser", action="store_true",
                        help="Never launch the Playwright browser fallback.")
    parser.add_argument("--out", default="apartments.csv",
                        help="Latest-snapshot CSV (overwritten each run).")
    parser.add_argument("--history", default="rent_history.csv",
                        help="Append-only time-series CSV (one row per unit per run).")
    parser.add_argument("--no-history", action="store_true",
                        help="Do not append to the history file (e.g. for test runs).")
    parser.add_argument("--urls", nargs="*",
                        help="One-off: scrape exactly these URLs (bypasses the tracked store).")
    parser.add_argument("--area",
                        help="Discover apartments in this area via Google Places, "
                             "add them to the tracked store, then scrape. The only "
                             "flag that calls the Places API.")
    parser.add_argument("--max", type=int, default=20,
                        help="Max apartments to pull from Places for --area (cap 60).")
    parser.add_argument("--resolve-missing", action="store_true",
                        help="For discovered entries lacking a website, try a "
                             "best-effort browser search (Bing). Slow/flaky; opt-in.")
    parser.add_argument("--resolve-max", type=int, default=15,
                        help="Max website look-ups per run when --resolve-missing.")
    parser.add_argument("--discover-only", action="store_true",
                        help="Discover/merge into the tracked store, then stop (no scrape).")
    parser.add_argument("--urls-file",
                        help="Add newline-separated URLs to the tracked store.")
    parser.add_argument("--visualize", action="store_true",
                        help="After scraping, build the charts and the "
                             "recommendation shortlist (see visualize.py).")
    # Only read when --visualize is set. Declared here rather than imported from
    # visualize.py so a normal scrape never pays for the matplotlib import.
    charts = parser.add_argument_group("charts (with --visualize)")
    charts.add_argument("--plots-dir", default="plots",
                        help="Folder for the generated charts.")
    charts.add_argument("--top", type=int, default=5,
                        help="How many recommendations to shortlist.")
    charts.add_argument("--min-sqft", type=float, default=750,
                        help="Recommendations must be larger than this.")
    charts.add_argument("--min-rent", type=float, default=3200,
                        help="Recommendation rent floor.")
    charts.add_argument("--max-rent", type=float, default=3900,
                        help="Recommendation rent ceiling.")
    charts.add_argument("--email", nargs="?", const="", default=None,
                        metavar="ADDR",
                        help="Email the report once the charts are built. Bare "
                             "--email uses EMAIL_TO from .env; pass an address "
                             "to override it. Needs GMAIL_USER and "
                             "GMAIL_APP_PASSWORD in .env (see notify.py).")
    # spelled out rather than imported from notify.WATCH for the same reason the
    # chart options above are spelled out: a normal scrape imports neither module
    charts.add_argument("--email-plots", default="Monticello,Encasa,Mylo,1250 Lakeside",
                        metavar="NAMES",
                        help="Comma-separated communities whose rent-history "
                             "chart is attached to the email. Matched loosely "
                             "against the chart filenames.")
    args = parser.parse_args()

    if args.use_llm:
        from llm_extract import available
        if not available():
            print("WARNING: --use-llm set but no ANTHROPIC_API_KEY / anthropic SDK. "
                  "Continuing rule-based only.")
            args.use_llm = False

    targets = _select_targets(args)   # list of (url, seed_dict|None)
    if args.discover_only:
        return 0
    if not targets:
        print("Nothing to scrape (empty tracked store and no URLs). "
              "Try: python scraper.py --area \"Sunnyvale, CA\"")
        return 0

    run_stamp = _now()  # one timestamp shared by every row in this run
    all_rows: list[UnitRecord] = []
    timings: list[tuple] = []
    t_run = time.monotonic()
    for i, (url, seed) in enumerate(targets, 1):
        print(f"[{i}/{len(targets)}] {url}")
        t0 = time.monotonic()
        result = scrape_site(url, use_llm=args.use_llm,
                             use_browser=not args.no_browser, seed=seed)
        took = time.monotonic() - t0
        timings.append((took, url))
        rows = to_rows(result, run_stamp)
        method = result.extraction_method or "-"
        # per-site seconds: a run that suddenly takes twice as long should say
        # where it went, rather than leaving it to be inferred from file mtimes
        print(f"    -> {len(result.units)} 1B1B unit(s), method={method}, "
              f"status={result.status}, {took:.0f}s")
        all_rows.extend(rows)

    total = time.monotonic() - t_run
    print(f"\nScraped {len(targets)} site(s) in {total / 60:.0f} min "
          f"({total / max(1, len(targets)):.0f}s each). Slowest:")
    for took, url in sorted(timings, reverse=True)[:5]:
        print(f"    {took:5.0f}s  {url}")

    # latest-snapshot file (overwritten each run)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for rec in all_rows:
            writer.writerow(rec.to_row())
    print(f"\nWrote {len(all_rows)} row(s) to {args.out}")

    # append-only history file (the time series)
    if not args.no_history:
        total = _append_history(args.history, all_rows)
        print(f"Appended {len(all_rows)} row(s) to {args.history} "
              f"(now {total} total rows) @ {run_stamp}")

    if args.visualize:
        _visualize(args)
    return 0


def _visualize(args) -> None:
    """Build the charts off the CSVs this run just wrote. Imported lazily so the
    matplotlib dependency only matters to people who ask for charts."""
    try:
        import visualize
    except ImportError as exc:          # matplotlib missing, most likely
        print(f"\nSkipping --visualize: {exc}\n  pip install -r requirements.txt")
        return
    if not os.path.exists(args.history):
        print(f"\nSkipping --visualize: no history file at {args.history} "
              f"(don't pass --no-history on the first run).")
        return
    print()
    visualize.run(snapshot=args.out, history=args.history, outdir=args.plots_dir,
                  top=args.top, min_sqft=args.min_sqft, min_rent=args.min_rent,
                  max_rent=args.max_rent)
    # only now: history_charts() wipes and rebuilds plots/history/ on every run,
    # so anything reading those PNGs has to wait for run() to come back
    if args.email is not None:
        _email(args)


def _email(args) -> None:
    """Mail the report built above. Degrades with a warning like --use-llm does:
    the CSVs and charts are already on disk, and no mail problem should cost
    the run."""
    import notify
    ok, why = notify.available(args.email)
    if not ok:
        print(f"\nWARNING: --email set but {why}. Charts were still written.")
        return
    print()
    notify.send_report(plots_dir=args.plots_dir, to=args.email,
                       watch=notify.parse_watch(args.email_plots))


def _append_history(path: str, rows: list[UnitRecord]) -> int:
    """Append rows to the history CSV, writing the header only if the file is new
    or empty. Returns the total number of data rows in the file afterwards."""
    exists = os.path.exists(path) and os.path.getsize(path) > 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not exists:
            writer.writeheader()
        for rec in rows:
            writer.writerow(rec.to_row())
    # count data rows (excluding header) for a friendly log line
    with open(path, "r", encoding="utf-8") as f:
        return max(0, sum(1 for _ in f) - 1)


if __name__ == "__main__":
    sys.exit(main())
