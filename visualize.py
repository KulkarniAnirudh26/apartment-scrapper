"""Charts for the scraped rent data, plus a shortlist of the best-value units.

Reads the two CSVs the scraper writes and fills a `plots/` folder:

  plots/sqft_vs_rent.png      size vs. rent for every priced 1B1B in the snapshot
  plots/history/<name>.png    rent over time, one image per community
  plots/recommendations.md    the top picks for the size/price filter

`history.py` stays the text/pivot tool; this is the picture side of it. It also
runs straight off the back of a scrape via `python scraper.py --visualize`.

Examples:
  python visualize.py                       # everything, into plots/
  python visualize.py --outdir charts       # somewhere else
  python visualize.py --history-only        # just the per-community charts
  python visualize.py --min-sqft 800 --min-rent 3000 --max-rent 4200
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import shutil
from collections import defaultdict
from urllib.parse import urlparse

import matplotlib
matplotlib.use("Agg")               # file output only, no display needed
# Every label here is money. Left on, a pair of '$' in one string is read as
# mathtext and "$3,200-$3,900" renders as an equation.
matplotlib.rcParams["text.parse_math"] = False
import matplotlib.pyplot as plt          # noqa: E402
import matplotlib.dates as mdates        # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

from history import _load, _rent, _label  # noqa: E402

DEFAULT_SNAPSHOT = "apartments.csv"
DEFAULT_HISTORY = "rent_history.csv"
DEFAULT_OUTDIR = "plots"

# Categorical palette, used in this fixed order and never cycled past slot 8.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
          "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_MUTED = "#52514e"
GRID = "#c9c8c3"

# A scatter compares every pair of colours at once, and only the first three
# slots stay separable for colourblind readers under all-pairs. Hence 3 cities.
SCATTER_SLOTS = 3
# Past 8 series a legend stops working; those communities become small multiples.
MAX_LINES = 8

KNOWN_CITIES = ["Santa Clara", "Sunnyvale", "Mountain View", "Cupertino",
                "San Jose", "Campbell", "Milpitas", "Los Altos", "Palo Alto"]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _num(v):
    """Float or None; the CSV leaves unknown numbers blank."""
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def _city(address: str) -> str:
    """City from a messy address.

    Addresses arrive as '3555 Monroe Street Santa Clara , CA 95051' as often as
    the tidy comma-separated form, so matching a known city name inside the text
    before 'CA' beats a generic 'last field before the state' regex (which
    returns street names on this data).
    """
    head = re.split(r",?\s*\bCA\b", address or "")[0]
    for city in KNOWN_CITIES:
        if city.lower() in head.lower():
            return city
    return "Other"


# "1 bed", "1 bath", "720 sqft" - repeated in every name, so they distinguish
# nothing. Stripping them is what leaves the unit id visible.
_PLAN_NOISE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:bed|bd|bedroom|bath|ba|bathroom|sq\.?\s*ft|sqft|"
    r"square\s+feet)s?\b|approx\.?", re.I)
_PLAN_JUNK = re.compile(r"[�•|·]+")
PLAN_MAXLEN = 30


def _plan_text(name: str, community: str = "") -> str:
    """Readable floor-plan name. Some sites dump a whole sentence in here.

    'Special Virtual tour 006-1120 Avalon Silicon Valley 1 bed - 1 bath - 720
    sqft' -> 'Special Virtual tour 006-1120'. The unit id is the only part that
    differs between Avalon's eleven plans, and it sits behind the boilerplate.

    Display only - never group by this. Distinct plans share a truncation, so
    grouping on it merges them into one bogus series.
    """
    name = (name or "").strip()
    if not name:
        return "Unnamed plan"
    text = _PLAN_NOISE.sub(" ", name)
    for token in (community or "").split():
        if len(token) > 2:
            text = re.sub(rf"\b{re.escape(token)}\b", " ", text, flags=re.I)
    text = _PLAN_JUNK.sub(" ", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" ,-·|")
    text = text or name.strip()
    if len(text) <= PLAN_MAXLEN:
        return text
    # elide the middle: both ends carry identity, the middle is usually filler
    head = PLAN_MAXLEN * 2 // 3
    return text[:head].rstrip() + "…" + text[-(PLAN_MAXLEN - head - 1):].lstrip()


def _unique_plan_labels(keys, community: str = "") -> dict[tuple, str]:
    """Labels for one community's units, guaranteed distinct.

    Keys are (plan name, sqft), so the size that separates two same-named units
    is also what tells them apart in the legend.
    """
    labels: dict[tuple, str] = {}
    taken: dict[str, int] = {}
    for key in keys:
        name, sqft = key
        label = _plan_text(name, community)
        if sqft:
            label = f"{label} · {sqft:,.0f} sq ft"
        if label in taken:
            taken[label] += 1
            label = f"{label} ({taken[label]})"
        else:
            taken[label] = 1
        labels[key] = label
    return labels


def _plan(row: dict) -> str:
    return _plan_text(row.get("floorplan_name"))


def _slug(text: str, maxlen: int = 60) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", (text or "").strip()).strip("-")
    return s[:maxlen].rstrip("-") or "unnamed"


def _domain(url: str) -> str:
    try:
        net = urlparse(url or "").netloc.lower()
    except ValueError:
        return ""
    return net[4:] if net.startswith("www.") else net


def _is_start_price(row: dict) -> bool:
    """rent_min is an advertised 'starting at' floor, not a real unit price."""
    return (row.get("extraction_method") or "").endswith("+start")


def _when(row: dict):
    try:
        return dt.datetime.strptime(row.get("scraped_at", ""), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _dollars(x, _pos=None) -> str:
    return f"${x:,.0f}"


def _phone(raw: str) -> str:
    """Some sites hand back a tel: href, so the number arrives as '//844-...'."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return (raw or "").strip() or "-"
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


# Words that identify nothing on their own: generic apartment vocabulary plus
# every city token, so "West Santa Clara, CA Apartments" scores as empty.
_FILLER = {
    "apartment", "apartments", "apartmenthomes", "apts", "homes", "home",
    "rent", "rentals", "for", "in", "near", "at", "on", "of", "and", "a", "an",
    "the", "is", "ca", "california", "pet", "pets", "friendly", "gated",
    "luxury", "living", "community", "communities", "new", "your", "west",
    "east", "north", "south", "downtown",
}
_FILLER |= {t for city in KNOWN_CITIES for t in city.lower().split()}

# Where an SEO page title stops being the property's name.
_CUT = re.compile(r"\s*(?::|\||,| - | in | for rent\b| for | near | is )", re.I)
# Glued-together domain labels worth splitting back apart.
_GLUED = ["apartmenthomes", "apartments", "apartment", "apts"] + \
         [c.lower().replace(" ", "") for c in KNOWN_CITIES]


def _clean_name(name: str) -> str:
    """Trim the SEO tail off a page-title-derived community name.

    'Flora Vista Apartments in Santa Clara, CA' -> 'Flora Vista Apartments'
    'The Montclaire: Reimagined Living Underway' -> 'The Montclaire'
    'Savoy is a pet'                            -> 'Savoy'
    """
    m = _CUT.search(name or "")
    return (name[:m.start()] if m else (name or "")).strip(" ,-:|")


def _is_distinctive(name: str) -> bool:
    """Does anything survive that actually names a building?"""
    return any(t not in _FILLER for t in re.findall(r"[a-z]+", name.lower()))


def _name_from_domain(url: str) -> str:
    """'www.the-meadows.com' -> 'The Meadows'.

    Only a fallback: operator domains (deanzaproperties, irvinecompanyapartments)
    host many buildings, so this must never outrank a usable community name.
    """
    label = _domain(url).split(".")[0]
    for prefix in ("liveat", "live", "visit", "stay", "rentat", "the"):
        if label.startswith(prefix) and len(label) - len(prefix) >= 4:
            label = (prefix + " " if prefix == "the" else "") + label[len(prefix):]
            break
    parts: list[str] = []
    for tok in re.split(r"[-_\s]+", label):
        for glued in _GLUED:
            if tok.endswith(glued) and len(tok) > len(glued):
                parts += [tok[:-len(glued)], glued]
                break
        else:
            parts.append(tok)
    return " ".join(p.capitalize() for p in parts if p) or _domain(url)


def _display_name(row: dict) -> str:
    """The name a person would use for this building."""
    cleaned = _clean_name(row.get("community_name", ""))
    if _is_distinctive(cleaned):
        return cleaned
    return _name_from_domain(row.get("source_url", ""))


def _span(values):
    """A y-range with breathing room, safe when every value is identical."""
    lo, hi = min(values), max(values)
    pad = max(hi * 0.03, 25) if hi - lo < 1 else (hi - lo) * 0.18
    return lo - pad, hi + pad


def _style_axes(ax) -> None:
    ax.set_facecolor(SURFACE)
    ax.grid(True, color=GRID, alpha=0.45, linewidth=0.7)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_MUTED, labelsize=8)


def _priced(rows: list[dict]) -> list[dict]:
    """Snapshot rows carrying both a rent and a size."""
    return [r for r in rows
            if _rent(r) is not None and _num(r.get("sqft")) is not None]


# --------------------------------------------------------------------------- #
# 1. size vs. rent
# --------------------------------------------------------------------------- #
def scatter(rows: list[dict], picks: list[dict], out_path: str,
            min_sqft: float, min_rent: float, max_rent: float) -> None:
    pts = _priced(rows)
    if not pts:
        print("No rows with both a rent and a size - skipping the scatter.")
        return

    fig, ax = plt.subplots(figsize=(12, 7.5))
    fig.patch.set_facecolor(SURFACE)
    _style_axes(ax)

    xs_all = [_num(r["sqft"]) for r in pts]
    ys_all = [_rent(r) for r in pts]
    x_lo, x_hi = min(xs_all) - 40, max(xs_all) + 90
    y_lo, y_hi = min(ys_all) - 180, max(ys_all) + 260

    # the search box, drawn under everything
    ax.add_patch(Rectangle((min_sqft, min_rent), x_hi - min_sqft, max_rent - min_rent,
                           facecolor="#2a78d6", alpha=0.06, edgecolor="#2a78d6",
                           linewidth=0.8, linestyle=(0, (4, 3)), zorder=0))
    ax.annotate(f"filter: >{min_sqft:,.0f} sq ft, {_dollars(min_rent)}-{_dollars(max_rent)}",
                xy=(x_hi - 12, max_rent - 30), fontsize=7.5, color="#2a78d6",
                ha="right", va="top", zorder=1)

    # median price per sq ft, as a "fair value" reference
    ppsf = sorted(_rent(r) / _num(r["sqft"]) for r in pts)
    med = ppsf[len(ppsf) // 2]
    ax.plot([x_lo, x_hi], [med * x_lo, med * x_hi], linestyle=(0, (5, 4)),
            color=INK_MUTED, linewidth=1.2, alpha=0.6, zorder=1,
            label=f"median ${med:.2f}/sq ft")

    # colour by city, capped at three so the palette stays colourblind-safe
    counts = defaultdict(int)
    for r in pts:
        counts[_city(r["address"])] += 1
    # "Other" is the unknown-city bucket, so it belongs with the catch-all
    # rather than competing for a named slot
    named = [c for c, _ in sorted(counts.items(), key=lambda kv: -kv[1]) if c != "Other"]
    # Reserve the last slot for a catch-all only when something actually has to
    # share it. Rolling a single city into "Elsewhere" just hides its name.
    if len(named) + ("Other" in counts) > SCATTER_SLOTS:
        named = named[:SCATTER_SLOTS - 1]
    top = named
    color_of = {c: SERIES[i] for i, c in enumerate(top)}
    color_of["Elsewhere"] = SERIES[len(top)]

    def bucket(row):
        c = _city(row["address"])
        return c if c in color_of and c != "Elsewhere" else "Elsewhere"

    for name in top + ["Elsewhere"]:
        group = [r for r in pts if bucket(r) == name]
        if not group:
            continue
        exact = [r for r in group if not _is_start_price(r)]
        start = [r for r in group if _is_start_price(r)]
        label = f"{name} ({len(group)})"
        if exact:
            ax.scatter([_num(r["sqft"]) for r in exact], [_rent(r) for r in exact],
                       s=64, color=color_of[name], edgecolors=SURFACE, linewidths=1.4,
                       zorder=3, label=label)
            label = None
        if start:
            ax.scatter([_num(r["sqft"]) for r in start], [_rent(r) for r in start],
                       s=64, facecolors="none", edgecolors=color_of[name],
                       linewidths=1.6, zorder=3, label=label)

    ax.scatter([], [], s=64, facecolors="none", edgecolors=INK_MUTED, linewidths=1.6,
               label='hollow = "starting at" price')

    # The picks cluster inside the filter box, so per-point callouts overlap each
    # other. Number the points and spell the numbers out in one key instead.
    for i, r in enumerate(picks, 1):
        x, y = _num(r["sqft"]), _rent(r)
        ax.scatter([x], [y], s=210, facecolors="none", edgecolors=INK,
                   linewidths=1.5, zorder=4)
        ax.annotate(str(i), xy=(x, y), xytext=(11, 6), textcoords="offset points",
                    fontsize=9, fontweight="bold", color=INK, zorder=5)
    if picks:
        key = "\n".join(f"{i}.  {r['_short']} - {_dollars(r['_rent'])} / "
                        f"{r['_sqft']:,.0f} sq ft  (${r['_ppsf']:.2f}/sq ft)"
                        for i, r in enumerate(picks, 1))
        ax.text(0.985, 0.03, f"Top {len(picks)} picks\n{key}",
                transform=ax.transAxes, ha="right", va="bottom",
                fontsize=8, color=INK, zorder=6, linespacing=1.5,
                bbox=dict(boxstyle="round,pad=0.6", facecolor=SURFACE,
                          edgecolor=GRID, linewidth=0.8, alpha=0.96))

    ax.set_xlim(x_lo, x_hi)
    ax.set_ylim(y_lo, y_hi)
    ax.set_xlabel("Size (sq ft)", color=INK_MUTED, fontsize=9)
    ax.set_ylabel("Rent (min, $)", color=INK_MUTED, fontsize=9)
    ax.yaxis.set_major_formatter(FuncFormatter(_dollars))
    ax.set_title(f"1B1B rent vs. size  -  {len(pts)} priced floor plans",
                 color=INK, fontsize=13, loc="left", pad=12)
    leg = ax.legend(fontsize=8, loc="upper left", frameon=True, framealpha=0.95)
    leg.get_frame().set_edgecolor(GRID)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print(f"Wrote {out_path}  ({len(pts)} points)")


# --------------------------------------------------------------------------- #
# 2. rent over time, one image per community
# --------------------------------------------------------------------------- #
def _unit_key(row: dict) -> tuple:
    """What makes one plotted line one thing.

    Size belongs in the key, not just the plan name: Buckingham Place publishes
    five different units (669-836 sq ft) with no plan name at all, and Citra
    lists two sizes under '1x1'. Keyed on the name alone they collapse into a
    single series holding several rents at the same timestamp, which draws as a
    vertical spike joined by a meaningless line. Name + size is unique per run
    across the whole history file.
    """
    return ((row.get("floorplan_name") or "").strip(), _num(row.get("sqft")))


def _identity(row: dict) -> tuple:
    """What counts as one property.

    Not the community name alone: three generic page titles are reused by two
    different sites each ("Santa Clara, CA Gated Apartments for Rent" is both
    Nantucket and The Carlyle), which merges two buildings into one chart and
    loses one of them. Not the domain alone either: De Anza runs four
    properties off one domain. The pair separates both cases.
    """
    return (_domain(row.get("source_url", "")), _label(row))


def _name_rank(row: dict) -> tuple:
    """Most informative floor-plan name first.

    Named beats blank; a name opening with a unit id beats the same unit
    restated behind a promo phrase ("Special Virtual tour 006-1120 ..."). Both
    variants arrive truncated to 60 characters, so length alone cannot separate
    them.
    """
    name = (row.get("floorplan_name") or "").strip()
    first = name.split()[0] if name else ""
    # the name itself breaks ties: without it two equally-ranked variants of one
    # unit can win in different runs, splitting it into two one-point series
    return (not name, 0 if any(c.isdigit() for c in first) else 1, len(name), name)


def _same_unit(a: str, b: str) -> bool:
    """Do two plan names, already known to share a run, rent and size, describe
    the same unit?

    Three shapes occur in the data:
      - the unit logged once named and once with no name at all;
      - Avalon repeating every unit behind a "Special Virtual tour" prefix,
        caught by the unit id appearing in both;
      - Prado listing each unit twice, as "Amaya 803-. 1 Available From ..."
        and "Amaya 803-. Furnish This Plan", caught by the shared opening.

    The last test has to be careful: "Special Virtual tour 006-1120" and
    "Special Virtual tour 007-1145" also share an opening but are different
    apartments. Two ids both carrying digits means distinct units, not one.
    """
    na, nb = (a or "").strip().lower(), (b or "").strip().lower()
    if not na or not nb or na == nb:
        return True

    # one name restates the other's unit id
    for x, y in ((na, nb), (nb, na)):
        token = x.split()[0]
        if len(token) >= 3 and any(c.isdigit() for c in token) and token in y:
            return True

    # same unit, two marketing blurbs: shared opening, and the words that first
    # differ are not a pair of unit ids
    ta, tb = na.split(), nb.split()
    shared = 0
    for x, y in zip(ta, tb):
        if x != y:
            break
        shared += 1
    if shared >= 2 and len(ta) > shared and len(tb) > shared:
        # Two unit ids mean two apartments ("... tour 006-1120" vs "007-1145").
        # A bare count does not: Varo appears as "769 sq. ft. 1 Available" in
        # one run and "0 Available" in the next - one unit that filled up.
        def looks_like_id(token: str) -> bool:
            # a price is not a unit id: Prado's names carry the rent inline and
            # arrive truncated, so one unit reads "... From $3,62" one run and
            # "... From $3,63" the next - differing tokens, same apartment
            if token.startswith("$"):
                return False
            return len(token) >= 4 and any(c.isdigit() for c in token)

        return not (looks_like_id(ta[shared]) and looks_like_id(tb[shared]))
    return False


def _canonical_units(rows: list[dict]) -> dict[tuple, tuple]:
    """Map every (plan name, size) seen at one property onto one unit.

    A unit has to keep the same identity across runs. Prado lists Antoni as
    "... 770 sq. ft. 3 Available From $3,620" in one run and "... Approx. 770
    sq. ft. Furnish This Plan" in the next, so collapsing duplicates run by run
    picks a different name each time and splits one apartment into two
    one-point series. Resolving names once, over the whole history, fixes that.

    Candidates are considered best-name-first and matched against the
    representatives already chosen, never chained through each other, so a
    blank name cannot quietly weld two real units together.
    """
    best: dict[tuple, dict] = {}
    for r in rows:
        key = _unit_key(r)
        if key not in best or _name_rank(r) < _name_rank(best[key]):
            best[key] = r

    # rent per (unit, run) - the evidence that settles what the names cannot
    rents: dict[tuple, dict] = defaultdict(dict)
    for r in rows:
        rent, when = _rent(r), _when(r)
        if rent is None or when is None:
            continue
        k = _unit_key(r)
        prev = rents[k].get(when)
        rents[k][when] = rent if prev is None else min(prev, rent)

    def mergeable(a: tuple, b: tuple) -> bool:
        """Same unit? Where the data can answer, it outranks the spelling.

        When two same-size units are both priced in the same run, that run
        decides. Identical rents in every shared run is one listing recorded
        twice - The Montclaire publishes its single 710 sq ft unit as both
        "Plan 1A" and "Unit Plan", names with nothing textual in common, which
        then draw as two lines exactly on top of each other. One run pricing
        them differently proves two apartments however alike the names read -
        Lorien Ivy's two 712 sq ft plans, which the name test alone wrongly
        welds into one, hiding a real unit.

        With no run pricing both there is nothing to compare, so fall back to
        the name test: that is the renamed-between-runs case it exists for.
        """
        shared = rents[a].keys() & rents[b].keys()
        if shared:
            return all(rents[a][w] == rents[b][w] for w in shared)
        return _same_unit(a[0], b[0])

    canon: dict[tuple, tuple] = {}
    reps: list[tuple] = []
    for key in sorted(best, key=lambda k: _name_rank(best[k])):
        for rep in reps:
            if rep[1] == key[1] and mergeable(key, rep):
                canon[key] = rep
                break
        else:
            reps.append(key)
            canon[key] = key
    return canon


def _unit_series(rows: list[dict]) -> dict[tuple, list[tuple]]:
    """One property's rows -> {unit: [(when, rent), ...]} sorted by time.

    Collapses the three ways this data records one unit twice: named plus
    unnamed, Avalon's "Special Virtual tour" restatement, and Prado's two
    marketing blurbs. Where a run somehow still holds two rents for one unit,
    the lower one wins - it is the rent you could actually have signed.
    """
    canon = _canonical_units(rows)
    seen: dict[tuple, dict] = defaultdict(dict)
    for r in rows:
        rent, when = _rent(r), _when(r)
        if rent is None or when is None:
            continue
        key = _unit_key(r)
        key = canon.get(key, key)
        prev = seen[key].get(when)
        seen[key][when] = rent if prev is None else min(prev, rent)
    return {k: sorted(v.items()) for k, v in seen.items()}


def _draw_line(ax, pts, color, label=None) -> tuple:
    """Draw one unit's rent line. Returns its last point, for labelling."""
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    if len(pts) == 1:
        # A plan seen in only one run would render as nothing without a marker,
        # and it usually sits exactly on another plan's point - so draw it as a
        # ring, which stays readable under an overlapping filled marker.
        ax.plot(xs, ys, marker="o", markersize=11, color=color, linestyle="none",
                markerfacecolor="none", markeredgewidth=2, label=label)
    else:
        ax.plot(xs, ys, marker="o", markersize=6, linewidth=2, color=color,
                markeredgecolor=SURFACE, markeredgewidth=1.2, label=label)
    return xs[-1], ys[-1]


def _label_ends(ax, ends: list[tuple], y_lo: float, y_hi: float) -> None:
    """Write the latest rent beside each line, nudged apart where they collide.

    Several units in one building often sit within a few dollars of each other,
    and their end labels then print on top of one another.
    """
    x_last = max(x for x, _ in ends) if ends else None
    gap = (y_hi - y_lo) * 0.038          # smallest legible vertical separation
    placed: list[float] = []
    for x, y in sorted(ends, key=lambda e: e[1]):
        if x != x_last:
            # not at the right-hand edge: a side label would cross the lines
            ax.annotate(_dollars(y), xy=(x, y), xytext=(0, 12), ha="center",
                        textcoords="offset points", fontsize=7.5, color=INK_MUTED)
            continue
        # ascending y, so the last placed label is the highest one so far.
        # A `while at - placed[-1] < gap` loop here never terminates: floating
        # point makes (a + gap) - a compare slightly under gap.
        at = max(y, placed[-1] + gap) if placed else y
        placed.append(at)
        if at == y:
            ax.annotate(_dollars(y), xy=(x, y), xytext=(8, 0), va="center",
                        textcoords="offset points", fontsize=7.5, color=INK_MUTED,
                        annotation_clip=False)
        else:
            # nudged off its line - a leader keeps the number attached
            ax.annotate(_dollars(y), xy=(x, y), xytext=(14, at), va="center",
                        textcoords=("offset points", "data"), fontsize=7.5,
                        color=INK_MUTED, annotation_clip=False,
                        arrowprops=dict(arrowstyle="-", color=GRID, linewidth=0.6))


def _headroom(ax, frac: float = 0.13) -> None:
    """Widen the x-range so the '$3,990' label on the last point stays inside."""
    x0, x1 = ax.get_xlim()
    ax.set_xlim(x0, x1 + (x1 - x0) * frac)


def _fmt_time_axis(ax) -> None:
    # the two 2026-08-29 runs are 5 minutes apart and collide under "%m-%d"
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    ax.yaxis.set_major_formatter(FuncFormatter(_dollars))


def history_charts(rows: list[dict], outdir: str) -> int:
    by_community: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        by_community[_identity(r)].append(r)

    if os.path.isdir(outdir):
        shutil.rmtree(outdir)          # never leave a stale PNG behind
    os.makedirs(outdir, exist_ok=True)

    used: set[str] = set()
    written = 0
    for _, items in sorted(by_community.items(),
                           key=lambda kv: _display_name(kv[1][0]).lower()):
        series = _unit_series(items)
        if not series:
            continue                    # nothing priced, nothing to draw

        name = _display_name(items[0])
        city = _city(items[0].get("address", ""))
        title = f"{name}  -  {city}" if city != "Other" else name

        base = _slug(name)
        candidate, n = base, 1
        while candidate in used:
            n += 1
            candidate = f"{base}-{n}"
        used.add(candidate)
        path = os.path.join(outdir, f"{candidate}.png")

        # sqft may be missing, so sort on a plain tuple rather than None vs float
        plans = sorted(series, key=lambda k: (k[0], k[1] or 0))
        values = [p[1] for plan in plans for p in series[plan]]
        labels = _unique_plan_labels(plans, name)
        # every panel shares the run window, so a one-point plan gets a real
        # date axis instead of a degenerate one
        times = [p[0] for plan in plans for p in series[plan]]
        t0, t1 = min(times), max(times)
        if t1 - t0 < dt.timedelta(hours=1):
            t0, t1 = t0 - dt.timedelta(hours=6), t1 + dt.timedelta(hours=6)
        margin = (t1 - t0) * 0.06
        # runs less than an hour apart are the same visit as far as a tick goes
        run_ticks: list[dt.datetime] = []
        for t in sorted(set(times)):
            if not run_ticks or t - run_ticks[-1] > dt.timedelta(hours=1):
                run_ticks.append(t)

        if len(plans) <= MAX_LINES:
            fig, ax = plt.subplots(figsize=(9.5, 5.5))
            fig.patch.set_facecolor(SURFACE)
            _style_axes(ax)
            ends = [_draw_line(ax, series[plan], SERIES[i], labels[plan])
                    for i, plan in enumerate(plans)]
            y_lo, y_hi = _span(values)
            ax.set_ylim(y_lo, y_hi)
            ax.set_xlim(t0 - margin, t1 + margin)
            _headroom(ax)
            _label_ends(ax, ends, y_lo, y_hi)
            _fmt_time_axis(ax)
            ax.set_ylabel("Rent (min, $)", color=INK_MUTED, fontsize=9)
            ax.set_title(title, color=INK, fontsize=12, loc="left", pad=10)
            # below the axes, not "best" - inside a flat chart every location
            # matplotlib picks still lands on a line
            ax.legend(fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.16),
                      ncol=min(3, len(plans)), frameon=False, handlelength=1.8,
                      columnspacing=1.6, labelcolor=INK_MUTED)
            fig.autofmt_xdate()
        else:
            # past 8 series a legend stops working - facet instead of cycling hues
            ncols = 3
            nrows = (len(plans) + ncols - 1) // ncols
            fig, axes = plt.subplots(nrows, ncols, figsize=(12, 2.6 * nrows + 1.2),
                                     sharey=True, sharex=True, squeeze=False)
            fig.patch.set_facecolor(SURFACE)
            for idx, plan in enumerate(plans):
                ax = axes[idx // ncols][idx % ncols]
                _style_axes(ax)
                end = _draw_line(ax, series[plan], SERIES[0])
                _label_ends(ax, [end], *_span(values))
                ax.set_title(labels[plan], fontsize=8, color=INK, loc="left")
                _fmt_time_axis(ax)
                # tick the runs themselves; a generic date locator can't find an
                # interval for a range this short and warns
                ax.set_xticks(run_ticks)
                for lab in ax.get_xticklabels():
                    lab.set_rotation(30)
                    lab.set_horizontalalignment("right")
            for idx in range(len(plans), nrows * ncols):
                axes[idx // ncols][idx % ncols].axis("off")
            # sharex only labels the bottom grid row; when the last row is a
            # partial one the other columns lose their dates entirely
            for col in range(ncols):
                occupied = [i // ncols for i in range(len(plans)) if i % ncols == col]
                if not occupied:
                    continue
                ax = axes[max(occupied)][col]
                ax.tick_params(labelbottom=True)
                for lab in ax.get_xticklabels():
                    lab.set_rotation(30)
                    lab.set_horizontalalignment("right")
            axes[0][0].set_ylim(*_span(values))
            axes[0][0].set_xlim(t0 - margin, t1 + margin * 4)
            fig.suptitle(f"{title}  ({len(plans)} floor plans)", color=INK,
                         fontsize=12, x=0.01, ha="left")

        fig.tight_layout()
        fig.savefig(path, dpi=150, facecolor=SURFACE)
        plt.close(fig)
        written += 1

    print(f"Wrote {written} community charts to {outdir}{os.sep}")
    return written


# --------------------------------------------------------------------------- #
# 3. recommendations
# --------------------------------------------------------------------------- #
def _trends(hist: list[dict]) -> dict[tuple, list[tuple]]:
    """(property, unit) -> rent over time, using the same unit resolution as
    the charts so a shortlist trend and its chart cannot disagree."""
    by_property: dict[tuple, list[dict]] = defaultdict(list)
    for r in hist:
        by_property[_identity(r)].append(r)

    out: dict[tuple, list[tuple]] = {}
    for prop, rows in by_property.items():
        canon = _canonical_units(rows)
        for unit, pts in _unit_series(rows).items():
            out[prop + unit] = pts
        # a snapshot row may name the unit by a variant that resolved elsewhere
        for key, rep in canon.items():
            if rep != key and prop + rep in out:
                out[prop + key] = out[prop + rep]
    return out


def recommend(snapshot: list[dict], hist: list[dict], n: int,
              min_sqft: float, min_rent: float, max_rent: float):
    matches = [r for r in _priced(snapshot)
               if _num(r["sqft"]) > min_sqft and min_rent <= _rent(r) <= max_rent]
    for r in matches:
        r["_sqft"] = _num(r["sqft"])
        r["_rent"] = _rent(r)
        r["_ppsf"] = r["_rent"] / r["_sqft"]
        r["_city"] = _city(r["address"])
        r["_start"] = _is_start_price(r)
        r["_short"] = _display_name(r)

    trends = _trends(hist)
    for r in matches:
        pts = trends.get(_identity(r) + _unit_key(r), [])
        if len(pts) < 2 or pts[0][1] == pts[-1][1]:
            r["_trend"] = "flat" if pts else "new"
        else:
            delta = pts[-1][1] - pts[0][1]
            r["_trend"] = f"{_dollars(pts[0][1])} -> {_dollars(pts[-1][1])} ({delta:+,.0f})"

    # exact prices outrank advertised "starting at" floors, then best $/sq ft
    ranked = sorted(matches, key=lambda r: (r["_start"], r["_ppsf"]))

    picks: list[dict] = []
    alternates: dict[str, list[dict]] = defaultdict(list)
    seen_community: set[str] = set()
    cluster_owner: dict[tuple, str] = {}
    for r in ranked:
        # several buildings under one operator list an identical price and size;
        # one entry each, the rest recorded as equivalent alternates
        cluster = (_domain(r["source_url"]), r["_rent"], r["_sqft"])
        if cluster in cluster_owner:
            alternates[cluster_owner[cluster]].append(r)
            continue
        if r["community_name"] in seen_community:
            continue
        if len(picks) < n:
            picks.append(r)
            seen_community.add(r["community_name"])
            cluster_owner[cluster] = r["_short"]

    worst_pick = max((p["_ppsf"] for p in picks), default=0)
    demoted = [r for r in ranked if r["_start"] and r["_ppsf"] < worst_pick][:3]
    return picks, alternates, demoted, matches


def write_recommendations(picks, alternates, demoted, matches, path,
                          min_sqft, min_rent, max_rent) -> None:
    lines = [
        f"# Top {len(picks)} picks",
        "",
        f"Filter: **> {min_sqft:,.0f} sq ft**, rent "
        f"**{_dollars(min_rent)}-{_dollars(max_rent)}** - {len(matches)} floor plans "
        f"matched, ranked by price per square foot (exact per-unit prices ahead of "
        f'advertised "starting at" floors).',
        "",
        "| # | Community | Plan | Size | Rent | $/sq ft | City | Rent history |",
        "|---|-----------|------|-----:|-----:|--------:|------|--------------|",
    ]
    for i, r in enumerate(picks, 1):
        lines.append(
            f"| {i} | [{r['_short']}]({r['source_url']}) | {_plan(r)} | "
            f"{r['_sqft']:,.0f} sq ft | {_dollars(r['_rent'])} | ${r['_ppsf']:.2f} | "
            f"{r['_city']} | {r['_trend']} |")

    lines += ["", "### Contact", "",
              "| # | Community | Phone | Address |", "|---|---|---|---|"]
    for i, r in enumerate(picks, 1):
        lines.append(f"| {i} | {r['_short']} | {_phone(r.get('phone'))} | {r['address']} |")

    if alternates:
        lines += ["", "### Same price, same operator", "",
                  "These list the identical size and rent as a pick above, so they are "
                  "interchangeable on paper - worth a call if the pick has nothing left."]
        for owner, others in alternates.items():
            for o in others:
                lines.append(f"- **{o['_short']}** - {o['_sqft']:,.0f} sq ft, "
                             f"{_dollars(o['_rent'])} ({o['address']}) - same listing as *{owner}*")

    if demoted:
        lines += ["", "### Cheaper on paper, but the price is a floor", "",
                  "`extraction_method` ends in `+start`, so the rent is the community's "
                  'advertised "1 bed starting at" number rather than a real unit price. '
                  "Call before counting on it."]
        for r in demoted:
            lines.append(f"- **{r['_short']}** {_plan(r)} - {r['_sqft']:,.0f} sq ft, "
                         f"{_dollars(r['_rent'])} (${r['_ppsf']:.2f}/sq ft)")

    lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Wrote {path}")


def print_recommendations(picks, matches, min_sqft, min_rent, max_rent) -> None:
    print(f"\nTop {len(picks)} of {len(matches)} matches  (>{min_sqft:,.0f} sq ft, "
          f"{_dollars(min_rent)}-{_dollars(max_rent)}, best $/sq ft first)")
    head = (f"{'#':>2}  {'Community':32.32} {'Plan':14.14} {'sqft':>5} {'rent':>6} "
            f"{'$/sqft':>7}  {'city':12.12} trend")
    print(head)
    print("-" * len(head))
    for i, r in enumerate(picks, 1):
        print(f"{i:>2}  {r['_short']:32.32} {_plan(r):14.14} {r['_sqft']:>5,.0f} "
              f"{r['_rent']:>6,.0f} {r['_ppsf']:>7.2f}  {r['_city']:12.12} {r['_trend']}")


# --------------------------------------------------------------------------- #
# entry points
# --------------------------------------------------------------------------- #
def run(snapshot: str = DEFAULT_SNAPSHOT, history: str = DEFAULT_HISTORY,
        outdir: str = DEFAULT_OUTDIR, top: int = 5, min_sqft: float = 750,
        min_rent: float = 3200, max_rent: float = 3900,
        do_scatter: bool = True, do_history: bool = True,
        do_explorer: bool = True) -> int:
    """Build every chart. Called by main() and by scraper.py --visualize."""
    os.makedirs(outdir, exist_ok=True)
    snap_rows = _load(snapshot)
    hist_rows = _load(history)

    picks: list[dict] = []
    if do_scatter:
        picks, alternates, demoted, matches = recommend(
            snap_rows, hist_rows, top, min_sqft, min_rent, max_rent)
        scatter(snap_rows, picks, os.path.join(outdir, "sqft_vs_rent.png"),
                min_sqft, min_rent, max_rent)
        write_recommendations(picks, alternates, demoted, matches,
                              os.path.join(outdir, "recommendations.md"),
                              min_sqft, min_rent, max_rent)
        if do_explorer:
            # local import: explorer imports back from this module, and this
            # keeps the two from importing each other at load time
            import explorer
            explorer.build(snap_rows, hist_rows,
                           os.path.join(outdir, "explorer.html"),
                           dict(min_sqft=min_sqft, min_rent=min_rent,
                                max_rent=max_rent, top=top))

    if do_history:
        history_charts(hist_rows, os.path.join(outdir, "history"))

    if picks:
        print_recommendations(picks, matches, min_sqft, min_rent, max_rent)
    return 0


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Chart options, shared with scraper.py so both CLIs stay in step."""
    parser.add_argument("--plots-dir", default=DEFAULT_OUTDIR,
                        help="Folder for the generated charts.")
    parser.add_argument("--top", type=int, default=5,
                        help="How many recommendations to shortlist.")
    parser.add_argument("--min-sqft", type=float, default=750,
                        help="Recommendations must be larger than this.")
    parser.add_argument("--min-rent", type=float, default=3200,
                        help="Recommendation rent floor.")
    parser.add_argument("--max-rent", type=float, default=3900,
                        help="Recommendation rent ceiling.")
    parser.add_argument("--email", nargs="?", const="", default=None, metavar="ADDR",
                        help="Email the report once the charts are built. Bare "
                             "--email uses EMAIL_TO from .env; pass an address to "
                             "override it. Needs GMAIL_USER and GMAIL_APP_PASSWORD "
                             "in .env (see notify.py).")
    parser.add_argument("--email-plots", default="Monticello,Encasa,Mylo,1250 Lakeside",
                        metavar="NAMES",
                        help="Comma-separated communities whose rent-history chart "
                             "is attached to the email. Matched loosely against the "
                             "chart filenames.")


def main() -> int:
    p = argparse.ArgumentParser(description="Chart the scraped rent data.")
    p.add_argument("--snapshot", default=DEFAULT_SNAPSHOT,
                   help="Latest-snapshot CSV written by the scraper.")
    p.add_argument("--history", default=DEFAULT_HISTORY,
                   help="Append-only time-series CSV written by the scraper.")
    add_arguments(p)
    p.add_argument("--scatter-only", action="store_true",
                   help="Only the size-vs-rent chart and the recommendations.")
    p.add_argument("--history-only", action="store_true",
                   help="Only the per-community rent-over-time charts.")
    p.add_argument("--no-explorer", action="store_true",
                   help="Skip the interactive explorer.html page.")
    args = p.parse_args()

    code = run(snapshot=args.snapshot, history=args.history, outdir=args.plots_dir,
               top=args.top, min_sqft=args.min_sqft, min_rent=args.min_rent,
               max_rent=args.max_rent,
               do_scatter=not args.history_only, do_history=not args.scatter_only,
               do_explorer=not args.no_explorer)

    # sending lives here rather than in run(): run() is the shared entry point
    # scraper.py calls, and it should stay scoped to writing chart files
    if args.email is not None:
        import notify
        print()
        notify.send_report(plots_dir=args.plots_dir, to=args.email,
                           watch=notify.parse_watch(args.email_plots))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
