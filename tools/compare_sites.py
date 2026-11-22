"""Compare 2025 air temperature at Sokol M1 Kr1 (clearing) and Kr2 (field).

Run: python3 tools/compare_sites.py data/raw/meteo_hack_dataset.zip
Outputs JSON. Temperature differences use the sign Kr1 minus Kr2.
"""

import argparse
import calendar
import csv
import io
import json
from collections import defaultdict
from datetime import date, datetime
from itertools import chain, groupby
from math import isfinite
from statistics import fmean
from zipfile import ZipFile


def read_site(rows):
    """Keep source readings grouped by civil date and hour in the input clock."""
    readings = defaultdict(lambda: defaultdict(list))
    for row in rows:
        flags = {flag.strip() for flag in row["qc_flag"].split(";")}
        if "dup_conflict" in flags or any(flag.startswith("t_air:") for flag in flags):
            continue
        if not row["t_air"].strip():
            continue
        temperature = float(row["t_air"])
        if not isfinite(temperature):
            raise ValueError(f"Non-finite t_air at {row['datetime']}")
        timestamp = datetime.fromisoformat(row["datetime"])
        if timestamp.year != 2025:
            raise ValueError(f"Unexpected year at {timestamp}")
        readings[timestamp.date()][timestamp.hour].append(temperature)
    return readings


def mean_pair(pairs):
    """Each pair has the same weight. An empty sample has no temperature."""
    if not pairs:
        return {"clearing_c": None, "field_c": None, "delta_c": None}
    return {
        "clearing_c": fmean(a for a, b in pairs),
        "field_c": fmean(b for a, b in pairs),
        "delta_c": fmean(a - b for a, b in pairs),
    }


def valid_month(presence):
    longest_gap = max((sum(1 for _ in run) for present, run in groupby(presence)
                       if not present), default=0)
    return presence.count(False) <= 10 and longest_gap < 5


def summarize(clearing, field):
    full_clearing = {day for day, hours in clearing.items() if len(hours) >= 18}
    full_field = {day for day, hours in field.items() if len(hours) >= 18}
    common = full_clearing & full_field
    annual_pairs = []
    hour_pairs = defaultdict(list)
    months = []
    for month in range(1, 13):
        dates = [date(2025, month, number)
                 for number in range(1, calendar.monthrange(2025, month)[1] + 1)]
        available = [day for day in dates if day in common]
        valid = valid_month([day in common for day in dates])
        pairs = []
        if valid:
            for day in available:
                left, right = clearing[day], field[day]
                pairs.append((fmean(chain.from_iterable(left.values())),
                              fmean(chain.from_iterable(right.values()))))
                for hour in left.keys() & right.keys():
                    hour_pairs[hour].append((fmean(left[hour]), fmean(right[hour])))
            annual_pairs.extend(pairs)
        months.append({"month": month, "valid_month": valid,
                       "common_valid_days": len(available), **mean_pair(pairs)})
    return {
        "year": 2025,
        "valid_days": {"kr1": len(full_clearing), "kr2": len(full_field)},
        "annual": {"paired_days": len(annual_pairs), **mean_pair(annual_pairs)},
        "months": months,
        "hours": [{"hour": hour, "paired_date_hours": len(hour_pairs[hour]),
                   **mean_pair(hour_pairs[hour])} for hour in range(24)],
    }


def compare_archive(path):
    with ZipFile(path) as archive:
        sites = []
        for site in ("kr1", "kr2"):
            name = f"meteo_clgz_hackathon/data/sokol_m1/{site}_2025.csv"
            with archive.open(name) as raw, io.TextIOWrapper(raw, encoding="utf-8-sig",
                                                             newline="") as source:
                sites.append(read_site(csv.DictReader(source)))
    return summarize(*sites)


def plot_diurnal(result, path):
    """Write the report figure. Only this optional command requires matplotlib."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    data = [item for item in result["hours"] if item["paired_date_hours"]]
    if not data:
        raise ValueError("There are no paired hours to plot")
    values = [item["delta_c"] for item in data]
    hours = [item["hour"] for item in data]
    fig, ax = plt.subplots(figsize=(10.8, 5.9), dpi=160)
    fig.subplots_adjust(left=0.09, right=0.975, top=0.76, bottom=0.23)
    ink, muted = "#17372E", "#4F655F"
    fig.text(0.09, 0.92, "Разница температур поляны и поля по часам",
             fontsize=22, weight="bold", color=ink)
    fig.text(0.09, 0.853, "Урочище «Красное»   ·   Сокол М1   ·   2025 год",
             fontsize=13, color=muted)
    fig.text(0.09, 0.788, "Температура воздуха, Кр1 минус Кр2, °C", fontsize=11, color=ink)
    ax.bar(hours, values, width=0.74, color=["#267458" if v > 0 else "#42658B" for v in values])
    ax.axhline(0, color=ink, linewidth=1.25)
    limit = max(abs(v) for v in values) * 1.28
    ax.set_ylim(-limit, limit)
    ax.set_xlim(-0.65, 23.65)
    ax.set_xticks(range(24), labels=[f"{hour:02d}" for hour in range(24)])
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, pos: f"{v:g}".replace(".", ",").replace("-", "−")))
    ax.set_axisbelow(True)
    ax.grid(axis="y", color="#E2E9E6", linewidth=0.75)
    ax.tick_params(axis="both", colors=muted, length=0, labelsize=9)
    ax.tick_params(axis="x", pad=7)
    for spine in ax.spines.values():
        spine.set_visible(False)
    for item in [max(data, key=lambda r: r["delta_c"]), min(data, key=lambda r: r["delta_c"])]:
        value = item["delta_c"]
        label = f"{value:+.2f}".replace(".", ",").replace("-", "−")
        ax.annotate(label, (item["hour"], value),
                    xytext=(0, 5 if value >= 0 else -6), textcoords="offset points",
                    ha="center", va="bottom" if value >= 0 else "top",
                    weight="bold", color=ink, fontsize=10)
    count = sum(item["paired_date_hours"] for item in data)
    count_label = f"{count:,}".replace(",", " ")
    fig.text(0.09, 0.147, "Час по записям станций. Часовой пояс источника неизвестен.",
             fontsize=11, color=ink)
    fig.text(0.09, 0.073, f"В расчёте {count_label} пар «дата и час» на двух точках. "
             "Положительная разница означает, что на поляне теплее.",
             fontsize=10, color=muted)
    fig.savefig(path, facecolor="white")
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", help="Organizer's meteo_hack_dataset.zip")
    parser.add_argument("--figure", help="Optional path for a static figure (requires matplotlib)")
    args = parser.parse_args()
    report = compare_archive(args.archive)
    if args.figure:
        plot_diurnal(report, args.figure)
    print(json.dumps(report, indent=2, allow_nan=False))
