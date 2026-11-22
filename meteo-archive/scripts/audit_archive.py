"""Reproduce the project brief's reference checks from the distributed ZIP."""

import argparse
import json
from calendar import monthrange
from collections import Counter, defaultdict
from csv import DictReader
from datetime import date, datetime
from io import TextIOWrapper
from itertools import groupby
from pathlib import PurePosixPath
from statistics import fmean
from zipfile import ZipFile


def read_csv(archive, path):
    with archive.open(str(path)) as stream:
        yield from DictReader(TextIOWrapper(stream, encoding="utf-8-sig", newline=""))


def calendar_days(year, month):
    return [date(year, month, day) for day in range(1, monthrange(year, month)[1] + 1)]


def month_coverage(dates, slots_for_day, minimum):
    eligible = [len(slots_for_day(day)) >= minimum for day in dates]
    missing_run = max(
        (sum(1 for _ in run) for valid, run in groupby(eligible) if not valid),
        default=0,
    )
    return sum(eligible), eligible.count(False) <= 10 and missing_run < 5


def paired_temperatures(july_temperatures):
    daily_deltas = []
    hourly_deltas = defaultdict(list)
    for day in calendar_days(2025, 7):
        clearing = july_temperatures["kr1", day]
        field = july_temperatures["kr2", day]
        if min(len(clearing), len(field)) < 18:
            continue
        clearing_day = fmean(value for values in clearing.values() for value in values)
        field_day = fmean(value for values in field.values() for value in values)
        daily_deltas.append(field_day - clearing_day)
        for hour in clearing.keys() & field.keys():
            hourly_deltas[hour].append(fmean(field[hour]) - fmean(clearing[hour]))

    return {
        "paired_days": len(daily_deltas),
        "mean_daily_delta_c": fmean(daily_deltas),
        "source_hours": {
            hour: {"paired_days": len(values), "mean_delta_c": fmean(values)}
            for hour, values in sorted(hourly_deltas.items())
        },
    }


def audit(archive):
    paths = [PurePosixPath(name) for name in archive.namelist()]
    reference_path, = (path for path in paths if path.name == "coverage_by_month.csv")
    data_dir = reference_path.parent / "data"
    observations = [
        path for path in paths if path.suffix == ".csv" and path.is_relative_to(data_dir)
    ]
    rows_by_file = Counter()
    flags = Counter()
    station_slots = defaultdict(set)
    pressure_hours = defaultdict(set)
    kr2_temperature_hours = defaultdict(set)
    ame60_temperatures = []
    july_temperatures = defaultdict(lambda: defaultdict(list))

    for path in observations:
        station, year = path.stem.rsplit("_", 1)
        for row in read_csv(archive, path):
            rows_by_file[path.name] += 1
            timestamp = datetime.fromisoformat(row["datetime"])
            day = timestamp.date()
            day_flags = set(row["qc_flag"].split(";")) - {""}
            flags.update(day_flags)
            if "dup_conflict" in day_flags:
                continue

            slot = timestamp.time() if station == "ame60" else timestamp.hour
            station_slots[station, day].add(slot)
            invalid_fields = {flag.split(":", 1)[0] for flag in day_flags}

            if station == "kr1" and year == "2024":
                if row["pressure_hpa"] and "pressure_hpa" not in invalid_fields:
                    pressure_hours[day].add(timestamp.hour)

            if not row.get("t_air") or "t_air" in invalid_fields:
                continue
            if station == "ame60" and day == date(1993, 7, 1):
                ame60_temperatures.append(float(row["t_air"]))
            if station == "kr2" and year == "2024":
                kr2_temperature_hours[day].add(timestamp.hour)
            if station in {"kr1", "kr2"} and year == "2025" and day.month == 7:
                july_temperatures[station, day][timestamp.hour].append(float(row["t_air"]))

    references = list(read_csv(archive, reference_path))
    mismatches = []
    for row in references:
        station = row["station"].lower()
        dates = calendar_days(int(row["year"]), int(row["month"]))
        minimum = 72 if station == "ame60" else 18
        calculated = month_coverage(dates, lambda day: station_slots[station, day], minimum)
        expected = (int(row["valid_days"]), row["month_valid"] == "True")
        if calculated != expected:
            mismatches.append({"reference": row, "calculated": calculated})

    pressure_months = [
        month_coverage(calendar_days(2024, month), pressure_hours.__getitem__, 18)
        for month in range(1, 13)
    ]
    kr2_temperature_months = [
        month_coverage(calendar_days(2024, month), kr2_temperature_hours.__getitem__, 18)
        for month in range(1, 13)
    ]
    april = calendar_days(2024, 4)
    april_station, _ = month_coverage(april, lambda day: station_slots["kr1", day], 18)
    return {
        "csv_files": len(observations),
        "observation_rows": rows_by_file.total(),
        "duplicate_conflict_rows": flags["dup_conflict"],
        "coverage_reference_rows": len(references),
        "coverage_mismatches": mismatches,
        "ame60_1993_07_01_temperature": {
            "readings": len(ame60_temperatures),
            "mean_c": fmean(ame60_temperatures),
        },
        "kr1_april_2024": {
            "station_valid_days": april_station,
            "pressure_valid_days": pressure_months[3][0],
            "pressure_month_valid": pressure_months[3][1],
        },
        "kr1_pressure_2024": {
            "valid_days": sum(days for days, _ in pressure_months),
            "valid_months": sum(valid for _, valid in pressure_months),
        },
        "kr2_import": {
            "rows_2024": rows_by_file["kr2_2024.csv"],
            "rows_2025": rows_by_file["kr2_2025.csv"],
            "temperature_valid_days_2024": sum(days for days, _ in kr2_temperature_months),
            "temperature_valid_months_2024": sum(valid for _, valid in kr2_temperature_months),
        },
        "kr2_minus_kr1_july_2025": paired_temperatures(july_temperatures),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", help="Path to the organizer's meteo_hack_dataset ZIP")
    args = parser.parse_args()
    with ZipFile(args.archive) as archive:
        report = audit(archive)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(bool(report["coverage_mismatches"]))


if __name__ == "__main__":
    raise SystemExit(main())
