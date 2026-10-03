# Apartment 1B1B Rent Scraper

Scrapes a list of apartment community websites for **1-bed / 1-bath** floor plans
and writes **rent, size (sqft), address, and contacts** to `apartments.csv`.

## How it works

For each site, in order (stops as soon as it has confident data):

1. **HTTP fetch** the landing page + a few relevant subpages (`/floorplans`,
   `/contact`, …) and parse data already embedded in the HTML — JSON-LD,
   `__NEXT_DATA__` blobs, and DOM/text heuristics. Fast, **zero tokens**.
2. **Playwright fallback** — for sites that render pricing purely client-side,
   re-render in a local headless browser and parse again. Still **zero tokens**.
   Skipped (with a warning) if Chromium isn't installed.
3. **Claude fallback** — *only* with `--use-llm`, and only when the rules are
   still low-confidence. Sends a small cleaned text snippet (not raw HTML) to
   Claude for structured extraction. **This is the only path that spends tokens.**

Every site produces at least one CSV row. Sites with no 1B1B, unavailable units,
JS-only pricing (no browser), or that block the request get a row carrying
whatever was found plus a `status`.

## Setup

```bash
cd apartment-scraper
python -m venv venv
venv\Scripts\activate          # Windows (PowerShell: venv\Scripts\Activate.ps1)
pip install -r requirements.txt

# Optional, enables the browser fallback:
playwright install chromium

# Optional, enables --use-llm:
cp .env.example .env           # then set ANTHROPIC_API_KEY
```

## Run

```bash
python scraper.py                 # zero tokens, HTTP + browser fallback
python scraper.py --no-browser    # HTTP only, no browser
python scraper.py --use-llm       # opt in to the Claude fallback (spends tokens)
python scraper.py --out mine.csv
python scraper.py --urls https://example-apartments.com   # override the list
```

## Adding communities

Edit the `URLS` list at the top of `scraper.py`, or pass `--urls` on the command
line.

## Output columns

`source_url, community_name, address, floorplan_name, beds, baths, rent_min,
rent_max, sqft, availability, phone, email, contact_url, extraction_method,
status, notes, scraped_at`

- `extraction_method`: `embedded` | `dom` | `playwright` | `llm` | `mixed`; a
  `+start` suffix (e.g. `playwright+start`) means at least one row's `rent_min`
  is the community's advertised "1 bed starting at $X" floor (with `rent_max`
  left blank), used when no exact per-unit price was on the page.
- `availability`: `available` | `unavailable` | `call_for_pricing` (the site
  shows the plan + size but gates the price behind "Call for Pricing"/"Contact
  us") | "" (unknown)
- `status`: `ok` | `no_1b1b_found` | `unavailable` | `needs_js` | `blocked` | `error`

### Why some `available` rows have a size but no rent

Some communities publish the floor plan and square footage but not a rent number
(the page says "Call for Pricing" / "Please Call") — those rows are labeled
`availability=call_for_pricing`. Others show pricing only in a summary or a
separate "check availability" widget that rule-based scraping can't tie to the
unit. For maximum rent coverage on those, run with **`--use-llm`**: the Claude
fallback reads the whole rendered page (which does contain the prices) and fills
them in. It only fires on sites the rules couldn't price, and sends a small text
snippet, so a full area run costs a few cents.

## Tracking rent over time

Every run does two things:

- **overwrites** `apartments.csv` — the latest snapshot, and
- **appends** every row to `rent_history.csv` — an append-only log with a shared
  per-run `scraped_at` timestamp. Run it repeatedly (e.g. twice a day) and this
  file becomes a rent time series: one row per unit per run.

```bash
python scraper.py                 # snapshot + append to rent_history.csv
python scraper.py --no-history    # snapshot only, skip the append
python scraper.py --history other.csv   # append somewhere else
```

### View the history

`history.py` reads `rent_history.csv` and reports rent movement:

```bash
python history.py                        # first/latest/change per property + plan
python history.py --property markham     # filter to one community
python history.py --floorplan "Plan 03"  # filter to one floor plan
python history.py --pivot pivot.csv      # wide table: timestamps x plans (for Excel)
python history.py --plot rent.png        # line chart of rent over time (needs matplotlib)
```

## Charts

`visualize.py` turns the two CSVs into a `plots/` folder. Add `--visualize` to a
scrape to build them straight after the run, or run it standalone any time:

```bash
python scraper.py --visualize          # scrape, then chart
python visualize.py                    # chart what's already on disk
python visualize.py --history-only     # just the per-community charts
python visualize.py --scatter-only     # just the scatter + recommendations
python visualize.py --min-sqft 800 --min-rent 3000 --max-rent 4200 --top 8
```

Output:

| File | What it shows |
|------|---------------|
| `plots/sqft_vs_rent.png` | Every priced 1B1B as size vs. rent, coloured by city, with a median $/sq ft line, your filter box shaded, and the picks numbered. Hollow markers are `+start` advertised floors. |
| `plots/history/<Community>.png` | Rent over time for one community, one line per floor plan (legend carries the plan name + sq ft). A plan seen in only one run is drawn as a ring. |
| `plots/recommendations.md` | The shortlist, with $/sq ft, rent trend, phone, and address. |
| `plots/explorer.html` | **Interactive.** The same scatter with the thresholds as sliders — the picks recompute as you drag. Open it by double-clicking. |

A community with more than 8 units falls back to small multiples rather than a
legend of nine-plus colours (nothing currently trips this). `plots/history/` is
wiped and rewritten each run, so a community that drops out of the data doesn't
leave a stale chart behind.

Two quirks of the source data are corrected before charting, because both
otherwise invent apartments that don't exist:

- **A property is identified by site + name, not name alone.** Three generic page
  titles are reused by two different sites each - "Santa Clara, CA Gated
  Apartments for Rent" is both Nantucket and The Carlyle - so keying on the name
  merges two buildings into one chart and loses the other.
- **One unit often gets logged more than once per run** - named plus unnamed, or
  under two marketing blurbs ("Amaya 803-. 1 Available From $3,920" and "Amaya
  803-. Furnish This Plan"). Units are resolved once across the whole history,
  so a name that changes between runs doesn't split one apartment into two.

The filter defaults to **> 750 sq ft, $3,200-$3,900**; `--min-sqft`, `--min-rent`,
`--max-rent`, `--top`, and `--plots-dir` work on both commands.

### The interactive page

`plots/explorer.html` is where to change your mind about the filter. Min size, rent
floor and ceiling, and how many picks are all live controls; the chart and the
shortlist recompute together, and it opens on the same defaults as the CLI so it
starts out agreeing with `recommendations.md`. It also toggles two rules the static
output applies silently: collapsing an operator's duplicate listings (De Anza's four
identical $3,399 / 836 sq ft entries), and hiding advertised "starting at" floors.

The data is embedded in the page rather than read from the CSV, because a page opened
over `file://` can't fetch a sibling file — that's what lets it work by double-clicking,
offline, with no server. It's rebuilt on every `visualize.py` run (so `scraper.py
--visualize` keeps it current); pass `--no-explorer` to skip it.

### Email yourself the report

Every `--visualize` run can mail the shortlist to you, so a scheduled run lands in
your inbox instead of sitting in `plots/`.

```bash
python scraper.py --visualize --email          # scrape, chart, mail
python visualize.py --email                    # re-send what's already on disk
python scraper.py --visualize --email you@work.com   # override the recipient
```

The mail carries `recommendations.md` rendered as real HTML tables (with a plain-text
copy for clients that want one), the rent-history charts for Monticello, Encasa, Mylo
and 1250 Lakeside inline in the body, and `explorer.html` as an attachment - about
0.6 MB in total. Change which charts ride along with `--email-plots`:

```bash
python scraper.py --visualize --email --email-plots "Prado,Savoy,Verve"
```

Those names are matched loosely against the chart filenames (case and punctuation are
ignored, substring is enough), because a chart's name comes from the *scraped*
community name and drifts between runs - Encasa's chart is `Encasaliving.png`. A name
that matches nothing simply gets a note in the email rather than failing the run.

**Setup.** Gmail won't accept your account password over SMTP, so generate a
16-character App Password at
[myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
(needs 2-step verification on the account) and put it in `.env`:

```
GMAIL_USER=you@gmail.com
GMAIL_APP_PASSWORD=abcd efgh ijkl mnop
EMAIL_TO=you@gmail.com          # optional, defaults to GMAIL_USER
```

`.env` is gitignored. With the password missing the run still completes and still
writes every file - it just prints a warning instead of sending. Same if the send
itself fails: mail is never allowed to cost you a scrape. See `notify.py`.

**Testing it.** `notify.py` runs on its own and mails whatever is already in `plots/` -
no scraping, no chart rebuilding. Work down this list; each step costs more than the
one above it:

```bash
venv\Scripts\python.exe notify.py --dry-run   # build it, send nothing
venv\Scripts\python.exe notify.py --check     # verify the login, send nothing
venv\Scripts\python.exe notify.py             # actually send
```

`--dry-run` needs no credentials at all. It prints the MIME tree and writes
`plots/email-preview.eml` (open it in Windows Mail or Outlook to see the real message,
attachments and all) plus `plots/email-preview.html` (the body alone, charts inlined,
for a browser). `--check` just logs in to Gmail and hangs up, so a wrong App Password
shows up in two seconds instead of after a full scrape.

To test against *freshly built* charts rather than the ones on disk, use
`visualize.py --scatter-only --email` - it rebuilds the shortlist, the scatter and
`explorer.html`, reuses the existing history PNGs, then sends.

Use `venv\Scripts\python.exe`, not a bare `python`. The system Python has no
`python-dotenv`, so `.env` is never read and you get a "no GMAIL_USER" complaint about
keys that are plainly set. (`notify.py` detects that case and says so.)

### Schedule it twice a day (Windows)

`schedule.ps1` registers one task, **RentScrape**, with two daily triggers (08:30 and
20:30). Each run executes `run.bat --visualize --email`: scrape, rebuild the charts and
`explorer.html`, then email the shortlist.

Run it once from an **elevated** PowerShell (Start menu -> type `powershell` ->
Ctrl+Shift+Enter):

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Users\aniru\Personal\apartment-scraper\schedule.ps1"
```

Add `-RunNow` to kick off a run immediately, `-Remove` to unregister, or
`-MorningTime 07:00 -EveningTime 19:00` to change the times. Re-running it updates the
existing task rather than creating a second one.

Elevation is needed because the task is registered **S4U** - it runs whether or not
you're logged on, with no password stored anywhere and no console window.

```powershell
Get-ScheduledTask RentScrape | Get-ScheduledTaskInfo    # next/last run, last result
Get-Content run.log -Wait -Tail 20                      # watch a run live
Start-ScheduledTask -TaskName RentScrape                # run it now
```

A full run takes about **two hours** - 174 sites at roughly 40s each, because
Playwright's browser fallback is slow on the ones that need it. Measured: 1h55m on
2026-09-04. The task's `ExecutionTimeLimit` is 4h to leave real headroom; if you add
many more sites, re-check that it still fits.

**Laptop settings.** Task Scheduler by default refuses to start a task on battery and
kills a running one the moment you unplug, so `schedule.ps1` sets
`AllowStartIfOnBatteries` and `DontStopIfGoingOnBatteries`. Without both, runs vanish
with no error anywhere. It also sets `StartWhenAvailable`, so a run missed while the
machine was off happens when it next wakes rather than being lost from the rent history.

`WakeToRun` is set, but **it only works on AC**: DC wake timers are disabled on this
machine, so unplugged the laptop won't wake itself at 08:30 - the run catches up when
you open the lid. To allow waking on battery too (costs some battery life):

```powershell
powercfg /setdcvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1
powercfg /setactive SCHEME_CURRENT
```

**When a run fails.** Two different failures, handled two ways.

A run that *exits* non-zero is caught by `run.bat`, which returns the scraper's real exit
code and emails a plain-text alert with the last 50 lines of `run.log`.

A run that is *killed outright* - the scheduler's `ExecutionTimeLimit` firing, a reboot, a
power cut - cannot do that, because the very process that checks the exit code dies with
it. So `run.bat` writes a `.run-in-progress` marker when it starts and deletes it when it
finishes; if the next run finds that marker still there, the previous run died without
finishing and it emails that fact before starting work. This happened on 2026-09-08: a
catch-up run hit the 4h limit at site 68 and reported nothing at all.

Note what a kill costs: the scraper accumulates every row in memory and writes the CSVs
only after all 174 sites finish, so an interrupted run saves nothing.

`run.log` rotates to `run.log.1` at 5 MB. `run.bat` also sets `PYTHONUTF8=1` - without it a
scraped community name containing a character outside cp1252 crashes the run when stdout is
a redirected file, which is exactly the unattended case - and `PYTHONUNBUFFERED=1`, so
`run.log` fills in as the run goes rather than in 8 KB chunks.

**On timing.** Each site's elapsed seconds are logged, with the five slowest listed at the
end of a run. A run started while the laptop is closed can be several times slower than one
on an open machine (Modern Standby throttles background work), which is what pushed the
2026-09-08 catch-up run past its limit.

Scheduling is local because the browser fallback needs a local Chromium; a cloud/remote
schedule won't have it.

## Discovering apartments in an area

Instead of hardcoding URLs, you can discover apartments in a region with the
Google Places API and add them to a persistent tracked list.

```bash
python scraper.py --area "Sunnyvale, CA"              # discover + scrape
python scraper.py --area "Cupertino, CA" --max 40     # up to 40 (cap 60)
python scraper.py --area "San Jose, CA" --discover-only   # just update the store
python scraper.py --area "..." --resolve-missing      # find sites Places lacks
```

- **`--area` is the only command that calls Places.** It looks up apartment
  complexes (name, address, website), merges them into `tracked_apartments.json`
  (deduped by website), and then scrapes.
- **Normal runs (`python scraper.py`) never call Places** — they just re-scrape
  the tracked store, so your twice-daily schedule costs nothing. `run.bat` is
  unchanged.
- Places name/address also **backfill sites that block scraping** (so a blocked
  complex still gets a real name/address in the CSV).
- `--resolve-missing` (opt-in) tries to find a website for entries Places
  returned without one. Only high-confidence matches (result domain contains the
  apartment name) are scraped; anything else is stored as `low_confidence` /
  `unresolved` and **never** scraped, so a bad guess can't pollute your data.
  - **Reliable:** set `BRAVE_API_KEY` (free tier at
    https://brave.com/search/api/) and it uses the Brave Search API — a proper
    JSON API, no CAPTCHAs.
  - **Keyless fallback:** without a Brave key it scrapes Bing in the browser, but
    search engines actively **cloak/serve decoy results to headless bots** (in
    testing, Bing returned unrelated pages), so keyless resolution usually comes
    back `unresolved`. In practice Places already has the website for almost
    every complex, so this fallback rarely matters.

### API key

`--area` needs `GOOGLE_PLACES_API_KEY`. If it isn't set in this project's `.env`,
the scraper automatically reuses the key from
`../maps-lead-finder/backend/.env`. No new setup if that project already works.

### Cost

Places Text Search is ~$0.032/call; a 60-result area ≈ 3 calls ≈ $0.10, and the
current free tier is ~1,000 calls/month — so ~300+ area discoveries are free.
Since discovery only runs on `--area` (not the scheduled scrapes), you'll
realistically stay within the free tier. Set a budget alert in Google Cloud
Console for peace of mind.

### The tracked store

`tracked_apartments.json` is the master list. To add sites by hand, use
`--urls-file urls.txt` (one URL per line).

To stop tracking a complex, **mark it rather than deleting it** - add `"skip": true`
to its record (a `"skip_reason"` string is optional but worth writing down):

```json
{
  "website": "https://www.deanzaproperties.com/south-bay-area/the-podium",
  "name": "The Podium Apartments",
  "skip": true,
  "skip_reason": "duplicate listing of Flora Vista - same De Anza feed"
}
```

Deleting the entry does not stick. `merge_records()` never drops entries and re-adds
anything the store is missing, so the next `--area` run over that city puts a deleted
complex straight back. A skipped record is found as an existing one and keeps its flag,
and `scrape_targets()` leaves it out of every run.

Skipping stops *future* scrapes only. Rows already in `rent_history.csv` stay (it is
append-only, and that history is real), so a skipped complex keeps its chart in
`plots/history/` - it just stops gaining new points. It drops out of `apartments.csv`
and the recommendations on the next scrape.
