"""Reproducible blocked evaluation and separate hourly temperature research export."""

import argparse
import csv
import hashlib
import io
import json
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite
from pathlib import Path, PurePosixPath
from statistics import fmean, quantiles
from zipfile import ZipFile


VERSION = "air-hour-gaps-v1"
HOUR = timedelta(hours=1)
LENGTHS = (1, 3, 12, 24, 72)
METHODS = ("linear", "season_hour", "neighbor_bias", "forecast_last")
# Each pair gives the calibration year and first untouched evaluation year.
SPLITS = {"ame60": (2018, 2019), "kr1": (2024, 2025), "kr2": (2024, 2025)}
NEIGHBOR = {"kr1": "kr2", "kr2": "kr1"}
SEASONS = ("DJF", "MAM", "JJA", "SON")


def season(t):
    return SEASONS[t.month % 12 // 3]


def hourly_readings(rows):
    """Aggregate usable observations in their original station clock."""
    buckets = defaultdict(list)
    for row in rows:
        flags = row["qc_flag"].split(";")
        if "dup_conflict" in flags or any(flag.startswith("t_air:") for flag in flags):
            continue
        if not row["t_air"].strip():
            continue
        value = float(row["t_air"])
        if not isfinite(value):
            raise ValueError(f"Non-finite air temperature at {row['datetime']}")
        stamp = datetime.fromisoformat(row["datetime"])
        buckets[stamp.replace(minute=0, second=0, microsecond=0)].append(value)
    return {stamp: fmean(values) for stamp, values in buckets.items()}


def read_archive(path):
    series = {station: {} for station in SPLITS}
    members = {}
    with ZipFile(path) as archive:
        for member in sorted(archive.namelist()):
            if "/data/" not in member or not member.endswith(".csv"):
                continue
            station, year = PurePosixPath(member).stem.split("_")
            key = station, int(year)
            if key in members:
                raise ValueError(f"Two files for {key}")
            members[key] = member
            with archive.open(member) as raw, io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as stream:
                data = hourly_readings(csv.DictReader(stream))
            if any(stamp.year != key[1] for stamp in data):
                raise ValueError(f"Unexpected date in {member}")
            series[station].update(data)
    return series, members


@dataclass
class Background:
    profile: dict
    corrections: dict
    extremes: dict


def train(target, neighbor, cutoff):
    groups, differences, extremes = defaultdict(list), defaultdict(list), defaultdict(list)
    for stamp, value in target.items():
        if stamp.year >= cutoff:
            continue
        key = season(stamp), stamp.hour
        groups[key].append(value)
        extremes[key[0]].append(value)
        if stamp in neighbor:
            differences[key].append(value - neighbor[stamp])
    def supported_means(buckets):
        return {key: fmean(values) for key, values in buckets.items() if len(values) >= 10}
    limits = {}
    for label, values in extremes.items():
        if len(values) >= 20:
            cuts = quantiles(values, n=20, method="inclusive")
            limits[label] = cuts[0], cuts[-1]
    return Background(supported_means(groups), supported_means(differences), limits)


def masked_blocks(times, seed, max_per_length=8):
    """Within each calendar year and season, pick disjoint blocks without looking at values."""
    available = set(times)
    rng = random.Random(seed)
    periods = defaultdict(list)
    for stamp in sorted(available):
        periods[stamp.year, season(stamp)].append(stamp)
    blocks = []
    occupied = set()
    for (year, label), starts in sorted(periods.items()):
        candidates = {}
        for length in LENGTHS:
            candidates[length] = starts.copy()
            rng.shuffle(candidates[length])
        for _ in range(max_per_length):
            for length in reversed(LENGTHS):
                while candidates[length]:
                    start = candidates[length].pop()
                    end = start + (length - 1) * HOUR
                    if season(end) != label or end.year != year:
                        continue
                    block = [start + j * HOUR for j in range(length)]
                    if not all(t in available and t not in occupied for t in block):
                        continue
                    occupied.update(block)
                    occupied.update((start - HOUR, end + HOUR))
                    blocks.append((start, length))
                    break
    return sorted(blocks)


def predictions(target, neighbor, background, start, length):
    """Only the two boundaries and concurrent neighbor are exposed to the estimators."""
    left = target.get(start - HOUR)
    right = target.get(start + length * HOUR)
    for j in range(length):
        stamp = start + j * HOUR
        key = season(stamp), stamp.hour
        linear = None
        if length <= 3 and left is not None and right is not None:
            linear = left + (right - left) * (j + 1) / (length + 1)
        corrected = None
        if stamp in neighbor and key in background.corrections:
            corrected = neighbor[stamp] + background.corrections[key]
        yield stamp, {"linear": linear, "season_hour": background.profile.get(key),
                      "neighbor_bias": corrected, "forecast_last": left}


@dataclass
class Score:
    points: int = 0
    filled: int = 0
    total: float = 0
    absolute: float = 0
    tail_points: int = 0
    tail_filled: int = 0
    tail_absolute: float = 0

    def add(self, truth, estimate, extreme):
        self.points += 1
        self.tail_points += extreme
        if estimate is not None:
            error = estimate - truth
            self.filled += 1
            self.total += error
            self.absolute += abs(error)
            if extreme:
                self.tail_filled += 1
                self.tail_absolute += abs(error)

    def metrics(self):
        return {"points": self.points, "filled": self.filled, "coverage": self.filled / self.points,
                "mae_c": self.absolute / self.filled if self.filled else None,
                "bias_c": self.total / self.filled if self.filled else None,
                "extreme_points": self.tail_points, "extreme_filled": self.tail_filled,
                "extreme_mae_c": self.tail_absolute / self.tail_filled if self.tail_filled else None}


def evaluate(target, neighbor, background, blocks):
    scores = defaultdict(Score)
    forecast = defaultdict(Score)
    for start, length in blocks:
        for stamp, estimates in predictions(target, neighbor, background, start, length):
            label = season(stamp)
            truth = target[stamp]
            limits = background.extremes.get(label)
            extreme = limits is not None and (truth <= limits[0] or truth >= limits[1])
            for method, value in estimates.items():
                for scope in (label, "all"):
                    scores[scope, length, method].add(truth, value, extreme)
                if method in ("season_hour", "forecast_last"):
                    horizon = (stamp - start) // HOUR + 1
                    forecast[horizon, method].add(truth, value, extreme)
    return scores, forecast


def research_export(writer, station, target, neighbor, background, calibration, members, digest):
    """Publish separate observed and estimated fields for real gaps bounded by station data."""
    cutoff, test = SPLITS[station]
    times = sorted(t for t in target if t.year >= test)
    if not times:
        return
    def output(stamp, kind, original=None, estimate=None, method="", support="", uncertainty=None):
        writer.writerow([VERSION, digest, station, stamp.isoformat(timespec="hours"), "unknown", "°C", kind,
                         original, estimate, method, support, cutoff - 1, uncertainty])
    for i, left in enumerate(times):
        support = f"{members[station, left.year]}#{left:%Y-%m-%dT%H}"
        output(left, "observed_hour_mean", original=target[left], support=support)
        if i + 1 == len(times):
            continue
        right = times[i + 1]
        length = (right - left) // HOUR - 1
        if not length:
            continue
        if length > max(LENGTHS):
            for j in range(1, length + 1):
                output(left + j * HOUR, "missing")
            continue
        bucket = next(n for n in LENGTHS if n >= length)
        for stamp, estimates in predictions(target, neighbor, background, left + HOUR, length):
            # The seasonal baseline is evaluated, but withheld from the published layer.
            for method in ("linear", "neighbor_bias"):
                score = calibration.get((season(stamp), bucket, method))
                if estimates[method] is None or score is None or score.filled < 5:
                    continue
                support = f"{left.isoformat()}/{right.isoformat()}"
                if method == "neighbor_bias":
                    support = f"{members[NEIGHBOR[station], stamp.year]}#{stamp:%Y-%m-%dT%H}"
                output(stamp, "estimate", estimate=estimates[method], method=method,
                       support=support, uncertainty=score.absolute / score.filled)
                break
            else:
                output(stamp, "missing")


def experiment(archive, export=None):
    series, members = read_archive(archive)
    with open(archive, "rb") as file:
        digest = hashlib.sha256(file.read()).hexdigest()
    out = {"version": VERSION, "archive_sha256": digest, "splits": SPLITS, "unit": "°C",
           "sampling_seed": 8711, "stations": {}}
    if export:
        writer = csv.writer(export)
        writer.writerow(["method_version", "archive_sha256", "station", "station_hour", "timezone", "unit", "kind",
                         "observed_c", "estimated_c", "method", "support", "training_through_year", "calibration_mae_c"])
    for index, (station, (cal, test)) in enumerate(SPLITS.items()):
        target = series[station]
        neighbor = series.get(NEIGHBOR.get(station), {})
        model = train(target, neighbor, cal)
        results = {}
        for split_num, (label, stamps) in enumerate((
            ("calibration", (t for t in target if t.year == cal)),
            ("test", (t for t in target if t.year >= test)),
        )):
            blocks = masked_blocks(stamps, out["sampling_seed"] + 10 * index + split_num)
            scores, forecast = evaluate(target, neighbor, model, blocks)
            if label == "calibration" and export:
                research_export(writer, station, target, neighbor, model, scores, members, digest)
            results[label] = {
                "blocks": len(blocks),
                "scores": [{"season": scope, "gap_hours": length, "method": method, **score.metrics()}
                           for (scope, length, method), score in sorted(scores.items())],
                "forecast_by_horizon": [{"horizon_hours": horizon, "method": method, **score.metrics()}
                                        for (horizon, method), score in sorted(forecast.items())],
            }
        out["stations"][station] = results
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--metrics", type=Path, required=True)
    parser.add_argument("--series", type=Path, help="Optional observed/estimated hourly CSV for the test period")
    args = parser.parse_args()
    if len({p.resolve() for p in (args.archive, args.metrics, args.series) if p}) != 2 + bool(args.series):
        parser.error("Input and output paths must differ")
    if args.series:
        with args.series.open("w", encoding="utf-8-sig", newline="") as export:
            result = experiment(args.archive, export)
    else:
        result = experiment(args.archive)
    args.metrics.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    main()
