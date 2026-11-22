"""Block validation of Sokol temperature reconstruction and future baselines."""
import argparse
from collections import defaultdict
import csv
from datetime import date, datetime, timedelta
import io
import json
from math import isfinite
from pathlib import Path
from statistics import fmean, median, quantiles
from zipfile import ZipFile

DAY = timedelta(days=1)
STATIONS = ('KR1', 'KR2')
TRAIN_END = date(2024, 12, 31)


def read_daily(archive):
    site_days = {}
    with ZipFile(archive) as files:
        for station in STATIONS:
            slots, values = defaultdict(set), defaultdict(list)
            members = [name for name in files.namelist()
                       if Path(name).name in {f'{station.lower()}_2024.csv', f'{station.lower()}_2025.csv'}]
            for name in members:
                with files.open(name) as raw, io.TextIOWrapper(raw, encoding='utf-8-sig', newline='') as src:
                    for row in csv.DictReader(src):
                        tags = row['qc_flag'].split(';')
                        if ('dup_conflict' in tags or any(tag.startswith('t_air:') for tag in tags)
                                or not row['t_air'].strip()):
                            continue
                        stamp = datetime.fromisoformat(row['datetime'])
                        value = float(row['t_air'])
                        if isfinite(value) and -50 <= value <= 45:
                            slots[stamp.date()].add(stamp.hour)
                            values[stamp.date()].append(value)
            site_days[station] = {day: fmean(values[day]) for day, hours in slots.items() if len(hours) >= 18}
    return site_days


def season(day):
    return ('winter', 'spring', 'summer', 'autumn')[day.month % 12 // 3]


def block(start, length):
    return [start + delta * DAY for delta in range(length)]


def score(pairs, attempted, extremes):
    errors = [estimate - actual for actual, estimate in pairs if estimate is not None]
    extreme_errors = [abs(estimate - actual) for actual, estimate in pairs
                      if estimate is not None and (actual <= extremes[0] or actual >= extremes[1])]
    return {'targets': attempted, 'predicted': len(errors), 'coverage': len(errors) / attempted,
            'mae': fmean(map(abs, errors)) if errors else None, 'bias': fmean(errors) if errors else None,
            'extreme_mae': fmean(extreme_errors) if extreme_errors else None}


def evaluate(target, donor):
    training = {day: value for day, value in target.items() if day <= TRAIN_END}
    testing = {day: value for day, value in target.items() if day > TRAIN_END}
    if len(training) < 30 or not testing:
        raise ValueError('Требуется достаточно наблюдений Сокол за 2024–2025')
    offsets, monthly = defaultdict(list), defaultdict(list)
    for day, value in training.items():
        monthly[day.month].append(value)
        if day in donor:
            offsets[season(day)].append(value - donor[day])
    deciles = quantiles(training.values(), n=10)
    extremes = deciles[0], deciles[-1]
    gap_rows, forecast_rows = defaultdict(list), defaultdict(list)
    windows = defaultdict(int)
    for length in (1, 3, 7):
        current = TRAIN_END + 2 * DAY
        while current.year == 2025:
            dates = block(current, length)
            if all(day in testing for day in dates) and len({season(day) for day in dates}) == 1:
                before, after = current - DAY, dates[-1] + DAY
                left, right = target.get(before), target.get(after)
                label = length, season(current)
                windows[label] += 1
                for index, day in enumerate(dates, 1):
                    actual = testing[day]
                    values = monthly[day.month]
                    ref = donor.get(day)
                    differences = offsets[season(day)]
                    profile = median(values) if len(values) >= 10 else None
                    linear = (left + (right - left) * index / (length + 1)
                              if length <= 2 and left is not None and right is not None else None)
                    transfer = ref + median(differences) if ref is not None and len(differences) >= 30 else None
                    for method, estimate in [('linear', linear), ('seasonal_profile', profile),
                                             ('measured_neighbor', transfer)]:
                        gap_rows[(*label, method)].append((actual, estimate))
                    if index in (1, 3, 7) and length == 7:
                        # Only earlier observations may enter the future predictions.
                        earlier_donor = donor.get(before)
                        neighbor_issued = (earlier_donor + median(differences)
                                           if earlier_donor is not None and len(differences) >= 30 else None)
                        for method, estimate in [('persistence', left), ('seasonal_profile', profile),
                                                 ('neighbor_lag_persistence', neighbor_issued)]:
                            forecast_rows[(index, season(current), method)].append((actual, estimate))
                current += length * DAY
            else:
                current += DAY

    def metrics(groups):
        rows = []
        pooled = defaultdict(list)
        common_by_window = defaultdict(list)
        for (length, season_name, method), pairs in groups.items():
            rows.append({'days': length, 'season': season_name, 'method': method, **score(pairs, len(pairs), extremes)})
            pooled[length, method].extend(pairs)
            common_by_window[length, season_name].append((method, pairs))
        for (length, method), pairs in pooled.items():
            rows.append({'days': length, 'season': 'all', 'method': method, **score(pairs, len(pairs), extremes)})
        common = []
        for (length, season_name), methods in common_by_window.items():
            eligible = [(name, pairs) for name, pairs in methods if any(value is not None for _, value in pairs)]
            indexes = [i for i in range(len(eligible[0][1]))
                       if all(pairs[i][1] is not None for _, pairs in eligible)] if eligible else []
            if indexes:
                for name, pairs in eligible:
                    common.append({'days': length, 'season': season_name, 'method': name,
                                   **score([pairs[i] for i in indexes], len(indexes), extremes)})
        return rows, common

    gap_scores, common_gap = metrics(gap_rows)
    forecast_scores, common_forecast = metrics(forecast_rows)
    return {'training_days': len(training), 'test_days': len(testing), 'calibration_pairs': {
        key: len(values) for key, values in offsets.items()}, 'extreme_limits_c': extremes,
        'masked_windows': [{'days': length, 'season': name, 'count': count}
                           for (length, name), count in sorted(windows.items())],
        'imputation': gap_scores, 'forecast': forecast_scores,
        'imputation_common_dates': common_gap, 'forecast_common_dates': common_forecast}


def reconstruction(rows, target, donor):
    known, reference = rows[target], rows[donor]
    history = defaultdict(list)
    for day, value in known.items():
        if day <= TRAIN_END and day in reference:
            history[season(day)].append(value - reference[day])
    dates = sorted(day for day in reference if day > TRAIN_END and day not in known)
    for day in dates:
        offsets = history[season(day)]
        if len(offsets) < 30:
            continue
        yield {'station': target, 'parameter': 't_air', 'valid_at': str(day), 'issued_at': None,
               'value': reference[day] + median(offsets), 'unit': '°C', 'kind': 'station_reconstruction',
               'method': 'measured_neighbor_seasonal_median_offset', 'version': 'sokol-daily-1',
               'training_end': str(TRAIN_END), 'source_stations': [donor],
               'availability': {'reference_day': str(day), 'reference_value': reference[day],
                                'calibration_pairs': len(offsets)}, 'uncertainty': None}


def benchmark(path, *, estimates=False):
    rows = read_daily(path)
    if estimates:
        return {'version': 'sokol-daily-1', 'data_type': 'derived', 'estimates': [
            estimate for target, donor in (STATIONS, STATIONS[::-1])
            for estimate in reconstruction(rows, target, donor)
        ]}
    return {'version': 'sokol-daily-1', 'training_end': str(TRAIN_END), 'unit': '°C',
            'data_type': 'observed', 'results': {target: {
                'donor': donor, **evaluate(rows[target], rows[donor])
            } for target, donor in (STATIONS, STATIONS[::-1])}}


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('archive')
    cli.add_argument('--estimates', action='store_true', help='Return a separate layer of historic estimates.')
    args = cli.parse_args()
    print(json.dumps(benchmark(args.archive, estimates=args.estimates), ensure_ascii=False, indent=2, allow_nan=False))
