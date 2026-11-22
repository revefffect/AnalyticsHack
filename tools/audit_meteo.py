"""Reproduce the archive checks in docs/data-audit.md using the Python standard library."""

import argparse
import calendar
from collections import Counter, defaultdict
import csv
from hashlib import sha256
from io import TextIOWrapper
import json
from pathlib import Path, PurePosixPath
from statistics import mean
from zipfile import ZipFile


class Day:
    def __init__(self):
        self.slots = set()
        self.total = 0.0
        self.n = 0

    def record(self, slot, value=None):
        self.slots.add(slot)
        if value is not None:
            self.total += value
            self.n += 1


def valid_month(day_numbers, year, month):
    length = calendar.monthrange(year, month)[1]
    longest = streak = 0
    for n in range(1, length + 1):
        streak = 0 if n in day_numbers else streak + 1
        longest = max(longest, streak)
    return length - len(day_numbers) <= 10 and longest < 5, longest


def month_result(days, station, year, month):
    threshold = 72 if station == 'AME60' else 18
    valid = {d: obs for (m, d), obs in days.items() if m == month and len(obs.slots) >= threshold}
    accepted, longest = valid_month(valid, year, month)
    daily_means = {d: obs.total / obs.n for d, obs in valid.items() if obs.n}
    avg = mean(daily_means.values()) if accepted and daily_means else None
    return {'days': len(valid), 'valid': accepted, 'longest_gap': longest, 'mean': avg}, daily_means


def audit(archive_path):
    digest = sha256()
    with open(archive_path, 'rb') as inp:
        for chunk in iter(lambda: inp.read(1024 * 1024), b''):
            digest.update(chunk)
    station_rows = Counter()
    flags = Counter()
    excluded_stamps = set()
    off_grid = Counter()
    sample_counts = Counter()
    kr1_year = Counter()
    mismatches = []
    station_months = Counter()
    jan = {}
    kr1 = defaultdict(dict)
    air_2025 = defaultdict(dict)
    checked = set()
    with ZipFile(archive_path) as archive:
        coverage_file, = [p for p in archive.namelist() if p.endswith('/coverage_by_month.csv')]
        with archive.open(coverage_file) as raw:
            reference = {(r['station'], int(r['year']), int(r['month'])):
                         (int(r['valid_days']), r['month_valid'] == 'True')
                         for r in csv.DictReader(TextIOWrapper(raw, encoding='utf-8-sig'))}
        files = sorted(p for p in archive.namelist() if '/data/' in p and p.endswith('.csv'))
        for path in files:
            name, year = PurePosixPath(path).stem.split('_')
            station, year = name.upper(), int(year)
            if station not in {'AME60', 'KR1', 'KR2'}:
                raise ValueError(f'Unknown station {station} in {path}')
            metrics = ['station', 't_air']
            if station == 'KR1' and year == 2024:
                metrics.append('pressure_hpa')
            days = {metric: defaultdict(Day) for metric in metrics}
            with archive.open(path) as raw:
                reader = csv.DictReader(TextIOWrapper(raw, encoding='utf-8-sig', newline=''))
                for r in reader:
                    station_rows[station] += 1
                    stamp = r['datetime']
                    if int(stamp[:4]) != year:
                        raise ValueError(f'Timestamp outside filename year in {path}: {stamp}')
                    month, day = int(stamp[5:7]), int(stamp[8:10])
                    tags = r['qc_flag'].split(';') if r['qc_flag'] else []
                    flags.update(tags)
                    if station == 'KR1' and year == 2024:
                        kr1_year['records'] += 1
                        kr1_year['pressure_range_flags'] += int('pressure_hpa:range' in tags)
                    if 'dup_conflict' in tags:
                        excluded_stamps.add((station, stamp))
                        continue
                    slot = stamp[11:16] if station == 'AME60' else stamp[11:13]
                    if station == 'AME60' and int(stamp[14:16]) % 15:
                        off_grid[year] += 1
                    days['station'][month, day].record(slot)
                    for metric in metrics[1:]:
                        value = r[metric].strip()
                        flagged = any(t.startswith(metric + ':') for t in tags)
                        if station == 'KR1' and year == 2024 and month == 4:
                            sample_counts[metric + '_rows'] += 1
                            sample_counts[metric + '_flagged'] += int(flagged)
                        if value and not flagged:
                            days[metric][month, day].record(slot, float(value))
            for month in range(1, 13):
                key = (station, year, month)
                result, _ = month_result(days['station'], *key)
                checked.add(key)
                if (result['days'], result['valid']) != reference.get(key):
                    mismatches.append(list(key))
                station_months[station] += int(result['valid'])
                air, daily = month_result(days['t_air'], *key)
                if station == 'AME60' and month == 1 and air['valid']:
                    jan[year] = air['mean']
                if year == 2025 and station in {'KR1', 'KR2'} and air['valid']:
                    air_2025[station][month] = daily
                if station == 'KR1' and year == 2024:
                    pressure, _ = month_result(days['pressure_hpa'], *key)
                    kr1['station'][month] = result
                    kr1['t_air'][month] = air
                    kr1['pressure_hpa'][month] = pressure
    mismatches.extend(sorted(set(reference) - checked))
    paired = {}
    for m in sorted(air_2025['KR1'].keys() & air_2025['KR2'].keys()):
        a, b = air_2025['KR1'][m], air_2025['KR2'][m]
        common = sorted(a.keys() & b.keys())
        if common:
            paired[m] = {'common_days': len(common), 'delta_kr1_minus_kr2': mean(a[d] - b[d] for d in common)}
    return {
        'sha256': digest.hexdigest(), 'observation_csv': len(files), 'rows': dict(station_rows),
        'total_rows': sum(station_rows.values()), 'flag_counts': dict(sorted(flags.items())),
        'conflicting_timestamps': len(excluded_stamps), 'ame60_off_grid_nonconflict': dict(sorted(off_grid.items())),
        'reference_months': len(reference), 'station_months_checked': len(checked),
        'reference_mismatches': mismatches, 'valid_station_months': dict(station_months),
        'ame60_january_t_air': {'annual_means': jan, 'mean_across_years': mean(jan.values()) if jan else None},
        'kr1_2024': {metric: {'valid_days': sum(r['days'] for r in months.values()),
                             'valid_months': sum(r['valid'] for r in months.values()), 'april': months[4]}
                     for metric, months in kr1.items()},
        'kr1_april_2024_row_counts': dict(sample_counts), 'sokol_2025_paired_t_air': paired,
        'kr1_2024_row_counts': dict(kr1_year),
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.archive), ensure_ascii=False, indent=2))
