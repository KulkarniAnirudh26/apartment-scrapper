"""View the rent time series accumulated in rent_history.csv.

The scraper appends one row per unit per run (see scraper.py). This tool groups
those rows by property + floor plan and reports how rent has moved over time.

Examples:
  python history.py                          # summary table for everything
  python history.py --property markham       # filter to one community
  python history.py --floorplan "Plan 03"    # filter to one floor plan
  python history.py --pivot pivot.csv        # wide table (timestamps x plans)
  python history.py --plot rent.png          # line chart of rent over time
"""
from __future__ import annotations

import argparse
import csv
import os
from collections import defaultdict

DEFAULT_HISTORY = "rent_history.csv"


def _load(path: str) -> list[dict]:
    if not os.path.exists(path):
        raise SystemExit(f"History file not found: {path}\n"
                         f"Run the scraper first (python scraper.py).")
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _label(row: dict) -> str:
    """Human label for a property, preferring community name over URL."""
    return (row.get("community_name") or row.get("source_url") or "").strip()


def _key(row: dict) -> tuple[str, str]:
    return (_label(row), (row.get("floorplan_name") or "").strip())


def _rent(row: dict):
    v = (row.get("rent_min") or "").strip()
    try:
        return int(v)
    except ValueError:
        return None


def _filtered(rows: list[dict], prop: str | None, fp: str | None) -> list[dict]:
    out = []
    for r in rows:
        if prop and prop.lower() not in (
            (r.get("community_name", "") + " " + r.get("source_url", "")).lower()
        ):
            continue
        if fp and fp.lower() not in (r.get("floorplan_name", "") or "").lower():
            continue
        out.append(r)
    return out


def summary(rows: list[dict]) -> None:
    """Print first/latest rent and change per property + floor plan."""
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        groups[_key(r)].append(r)

    header = f"{'Property':32.32} {'Floor plan':16.16} {'first':>7} {'latest':>7} {'change':>7} {'n':>3}  first_seen"
    print(header)
    print("-" * len(header))
    for (prop, fp), items in sorted(groups.items()):
        items.sort(key=lambda r: r.get("scraped_at", ""))
        priced = [r for r in items if _rent(r) is not None]
        n = len(items)
        if not priced:
            print(f"{prop:32.32} {fp:16.16} {'-':>7} {'-':>7} {'-':>7} {n:>3}  "
                  f"(no rent captured)")
            continue
        first_rent = _rent(priced[0])
        latest_rent = _rent(priced[-1])
        change = latest_rent - first_rent
        change_s = f"{change:+d}" if change else "0"
        print(f"{prop:32.32} {fp:16.16} {first_rent:>7} {latest_rent:>7} "
              f"{change_s:>7} {n:>3}  {priced[0].get('scraped_at', '')}")


def write_pivot(rows: list[dict], out_path: str) -> None:
    """Wide table: rows = run timestamp, columns = 'property | floorplan',
    cells = rent_min. Handy for charting in Excel/Sheets."""
    stamps = sorted({r.get("scraped_at", "") for r in rows})
    cols = sorted({f"{_label(r)} | {(r.get('floorplan_name') or '').strip()}"
                   for r in rows})
    # (stamp, col) -> rent
    cell: dict[tuple[str, str], str] = {}
    for r in rows:
        col = f"{_label(r)} | {(r.get('floorplan_name') or '').strip()}"
        rent = _rent(r)
        if rent is not None:
            cell[(r.get("scraped_at", ""), col)] = str(rent)

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["scraped_at"] + cols)
        for s in stamps:
            writer.writerow([s] + [cell.get((s, c), "") for c in cols])
    print(f"Wrote pivot ({len(stamps)} timestamps x {len(cols)} plans) to {out_path}")


def write_plot(rows: list[dict], out_path: str) -> None:
    """Line chart of rent_min over time, one line per property + floor plan."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        raise SystemExit("Plotting needs matplotlib. Install it:\n"
                         "  pip install matplotlib")
    import datetime as dt

    series: dict[tuple[str, str], list[tuple]] = defaultdict(list)
    for r in rows:
        rent = _rent(r)
        if rent is None:
            continue
        try:
            when = dt.datetime.strptime(r.get("scraped_at", ""), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        series[_key(r)].append((when, rent))

    if not series:
        raise SystemExit("No priced data points to plot (check filters).")

    fig, ax = plt.subplots(figsize=(11, 6))
    for (prop, fp), pts in sorted(series.items()):
        pts.sort()
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        # floor plan first so it stays visible even when the legend is tight
        label = f"{fp} ({prop[:18]})" if fp else prop[:24]
        ax.plot(xs, ys, marker="o", label=label)
    ax.set_xlabel("Date")
    ax.set_ylabel("Rent (min, $)")
    ax.set_title("1B1B rent over time")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=7, loc="best")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    print(f"Wrote chart ({len(series)} lines) to {out_path}")


def main() -> int:
    p = argparse.ArgumentParser(description="View the rent history time series.")
    p.add_argument("--history", default=DEFAULT_HISTORY, help="History CSV to read.")
    p.add_argument("--property", dest="prop", help="Filter by community/URL substring.")
    p.add_argument("--floorplan", dest="fp", help="Filter by floor-plan substring.")
    p.add_argument("--pivot", help="Write a wide pivot CSV to this path.")
    p.add_argument("--plot", help="Write a rent-over-time PNG chart to this path.")
    args = p.parse_args()

    rows = _filtered(_load(args.history), args.prop, args.fp)
    if not rows:
        print("No rows match the given filters.")
        return 0

    if args.pivot:
        write_pivot(rows, args.pivot)
    if args.plot:
        write_plot(rows, args.plot)
    if not args.pivot and not args.plot:
        summary(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
