"""One analytical contract for the API, reports, exports and Grafana."""
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
import calendar
import json
from math import atan2, cos, degrees, fsum, hypot, radians, sin
from statistics import mean

from meteo.importer import PARAMETERS, QC_VERSION, SCHEMA_VERSION
from tools.audit_meteo import valid_month

DAY = timedelta(days=1)
PERIODS = {'day': 'Сутки', 'decade': 'Декады', 'month': 'Месяцы', 'instant': 'Измерения потока'}
QC_REASONS = {
    'dup_conflict': 'Противоречащие записи одного срока',
    'dup_exact': 'Повтор идентичной записи',
    'range': 'Значение вне допустимого диапазона',
    'fault': 'Сбой датчика',
    'parse': 'Значение не распознано',
    'no_column': 'Показатель отсутствует в исходной таблице',
    'empty': 'Пустая ячейка',
    'no_rows': 'Наблюдений за дату нет',
    'incomplete': 'Недостаточно пригодных сроков',
    'coverage': 'Нарушено правило календарного покрытия',
    'undefined_direction': 'Направления взаимно компенсируются',
}
ROW_EXCLUSIONS = {'dup_conflict', 'dup_exact'}


def days_between(start, end):
    for offset in range((end - start).days + 1):
        yield start + offset * DAY


def month_end(day):
    length = calendar.monthrange(day.year, day.month)[1]
    return day.replace(day=length)


def period_start(day, step):
    if step == 'month':
        return day.replace(day=1)
    if step == 'decade':
        return day.replace(day=(day.day - 1) // 10 * 10 + 1)
    return day


def period_end(day, step):
    if step == 'month' or step == 'decade' and day.day == 21:
        return month_end(day)
    return day + 9 * DAY if step == 'decade' else day


def coverage_rule(station, source):
    if source in {'reanalysis', 'forecast'}:
        return 18, 24, 'часов'
    if station == 'AME60':
        return 72, 96, 'сроков'
    return 18, 24, 'часов'


def average(values, metric):
    if metric != 'wind_dir':
        return mean(values) if values else None
    vectors = [radians(v) for v in values]
    if not vectors:
        return None
    x, y = fsum(cos(v) for v in vectors), fsum(sin(v) for v in vectors)
    if hypot(x, y) / len(vectors) < 1e-8:
        return None
    direction = degrees(atan2(y, x)) % 360
    return 0.0 if direction == 360 else direction


def observations(db, version, station, start, end, metric):
    # Binding the JSON path keeps this query valid for all registered parameters.
    return db.execute("""
        SELECT id, stamp, original_stamp, zone, source_file, source_row, received_at, flags,
               json_type(values_json, ?) AS cell, json_extract(values_json, ?) AS value
        FROM observations
        WHERE dataset=? AND station=? AND stamp>=? AND stamp<?
        ORDER BY stamp, id
    """, ('$.' + metric, '$.' + metric, version, station, start.isoformat(), (end + DAY).isoformat()))


def observation_status(row, metric):
    tags = [tag for tag in row['flags'].split(';') if tag]
    reasons = [t for t in tags if t in ROW_EXCLUSIONS or t.startswith(metric + ':')]
    if reasons:
        return 'flagged', reasons
    if row['cell'] is None:
        return 'no_column', ['no_column']
    if row['value'] is None:
        return 'empty', ['empty']
    return 'usable', []


def ranges_of_missing(days, present):
    start = last = None
    for day in days:
        if day not in present:
            start = start or day
            last = day
        elif start:
            yield {'start': str(start), 'end': str(last), 'days': (last - start).days + 1}
            start = None
    if start:
        yield {'start': str(start), 'end': str(last), 'days': (last - start).days + 1}


def daily_series(db, version, station, start, end, metric, source):
    minimum, expected, unit = coverage_rule(station, source)
    series = {}
    observed_steps, jumps = Counter(), []
    previous_stamp = previous_value = None

    def finish(day, slots, values, counts, flags, zones):
        count = len(slots)
        valid = count >= minimum
        value = average(values, metric) if valid else None
        reason = None
        if not valid:
            if not counts:
                reason = 'no_rows'
            elif set(counts) == {'no_column'}:
                reason = 'no_column'
            else:
                reason = 'incomplete'
        elif value is None:
            reason = 'undefined_direction'
        series[day] = {
            'start': str(day), 'end': str(day), 'value': value, 'eligible': valid,
            'valid_slots': count, 'expected_slots': expected, 'minimum_slots': minimum,
            'slot_unit': unit, 'counts': dict(counts), 'flags': dict(flags),
            'zones': sorted(zones), 'reason': reason,
        }

    current = None
    slots, values, counts, flags, zones = set(), [], Counter(), Counter(), set()
    for row in observations(db, version, station, start, end, metric):
        stamp = row['stamp']
        day = stamp[:10]
        if day != current:
            if current is not None:
                finish(date.fromisoformat(current), slots, values, counts, flags, zones)
            current = day
            slots, values, counts, flags, zones = set(), [], Counter(), Counter(), set()
        zones.add(row['zone'])
        status, reasons = observation_status(row, metric)
        counts[status] += 1
        flags.update(reasons if status == 'flagged' else ())
        if status != 'usable':
            continue
        values.append(row['value'])
        slots.add(stamp[11:16] if unit == 'сроков' else stamp[11:13])
        when = datetime.fromisoformat(stamp)
        if previous_stamp is not None:
            delta = (when - previous_stamp).total_seconds() / 60
            if delta > 0:
                observed_steps[int(delta)] += 1
            if metric == 't_air' and 0 < delta <= 60 and abs(row['value'] - previous_value) >= 10:
                if len(jumps) < 100:
                    jumps.append({'stamp': row['original_stamp'], 'change': row['value'] - previous_value,
                                  'file': row['source_file'], 'row': row['source_row'],
                                  'rule': 'Изменение температуры ≥10 °C за 60 минут'})
        previous_stamp, previous_value = when, row['value']
    if current is not None:
        finish(date.fromisoformat(current), slots, values, counts, flags, zones)
    for day in days_between(start, end):
        if day not in series:
            finish(day, [], [], {}, {}, [])
    return series, {'intervals_minutes': observed_steps.most_common(12), 'temperature_jumps': jumps}


def period_rows(start, end, step, metric, daily):
    for day in days_between(start, end):
        first = period_start(day, step)
        if day != max(first, start):
            continue
        last = period_end(day, step)
        lo, hi = max(first, start), min(last, end)
        rows = [daily[d] for d in days_between(lo, hi)]
        usable = {date.fromisoformat(r['start']).day for r in rows if r['eligible']}
        longest = max((r['days'] for r in ranges_of_missing(days_between(lo, hi), {
            date.fromisoformat(r['start']) for r in rows if r['eligible']
        })), default=0)
        partial = (lo, hi) != (first, last)
        if step == 'month' and not partial:
            accepted, longest = valid_month(usable, first.year, first.month)
        elif step == 'day':
            accepted = rows[0]['eligible']
        elif step == 'decade':
            accepted = len(usable) >= ((hi - lo).days + 1) * 0.75
        else:
            accepted = False
        reasons, flags = Counter(), Counter()
        for row in rows:
            reasons.update(row['counts'])
            flags.update(row['flags'])
        yield {
            'start': str(lo), 'end': str(hi), 'eligible': accepted, 'partial': partial,
            'value': average([r['value'] for r in rows if r['value'] is not None], metric) if accepted else None,
            'valid_days': len(usable), 'expected_days': len(rows), 'calendar_days': (last - first).days + 1,
            'longest_gap': longest, 'counts': dict(reasons), 'flags': dict(flags),
            'reason': rows[0]['reason'] if step == 'day' else (None if accepted else 'coverage'),
        }


def instant_points(db, dataset, station, start, end, metric, daily):
    for index, row in enumerate(observations(db, dataset['id'], station, start, end, metric)):
        if index >= 15000:
            raise ValueError('Сократите период для показа измерений потока')
        state, flags = observation_status(row, metric)
        day = daily[date.fromisoformat(row['stamp'][:10])]
        yield {'start': row['stamp'], 'end': row['stamp'], 'value': row['value'] if state == 'usable' else None,
               'received_at': row['received_at'], 'original_stamp': row['original_stamp'], 'zone': row['zone'],
               'valid_days': int(day['eligible']), 'expected_days': 1, 'partial': False, 'longest_gap': 0,
               'counts': {state: 1}, 'flags': dict(Counter(flags)), 'reason': flags[0] if flags else None}


def slice_data(db, dataset, station, metric, start, end, step, baseline_month=None):
    if metric not in PARAMETERS or step not in PERIODS or end < start or start.year < 1800 or end.year > 2200:
        raise ValueError('Проверьте параметр, шаг и выбранный период')
    if step == 'instant' and dataset['source'] not in {'stream', 'stream_snapshot'}:
        raise ValueError('Показ по отдельным измерениям доступен для потока')
    if baseline_month is not None and not 1 <= baseline_month <= 12:
        raise ValueError('Укажите месяц от 1 до 12')
    daily, diagnostics = daily_series(db, dataset['id'], station, start, end, metric, dataset['source'])
    points = list(instant_points(db, dataset, station, start, end, metric, daily)) if step == 'instant' else list(
        period_rows(start, end, step, metric, daily)
    )
    months = list(period_rows(start, end, 'month', metric, daily)) if step != 'month' else points
    season = {}
    for month in range(1, 13):
        years = [row for row in months if row['value'] is not None and int(row['start'][5:7]) == month]
        season[month] = {
            'value': average([r['value'] for r in years], metric),
            'years': [int(r['start'][:4]) for r in years],
            'year_count': len(years),
        }
    for row in points:
        reference = season[baseline_month or int(row['start'][5:7])]['value']
        value = row['value']
        diff = value - reference if value is not None and reference is not None else None
        row['deviation'] = (diff + 180) % 360 - 180 if diff is not None and metric == 'wind_dir' else diff
    available = [r for r in points if r['value'] is not None]
    return {
        'dataset': dataset['id'], 'name': dataset['name'], 'source': dataset['source'],
        'schema': SCHEMA_VERSION, 'qc': QC_VERSION, 'station': station, 'parameter': metric,
        'unit': PARAMETERS[metric][1], 'start': str(start), 'end': str(end), 'step': step,
        'method': ('Отдельные пригодные измерения потока' if step == 'instant' else
                   'Сутки по пригодным наблюдениям, месяцы по пригодным суткам, каждому году равный вес'),
        'coverage': {'valid_days': sum(row['eligible'] for row in daily.values()), 'days': len(daily),
                     'valid_periods': len(available), 'periods': len(points)},
        'points': points,
        'summary': {'average': average([r['value'] for r in available], metric),
                    'min': min((r['value'] for r in available), default=None),
                    'max': max((r['value'] for r in available), default=None)},
        'monthly_baseline': season,
        'missing_runs': sorted(ranges_of_missing(days_between(start, end), {
            day for day, row in daily.items() if row['eligible']
        }), key=lambda gap: gap['days'], reverse=True)[:50],
        **diagnostics,
    }


def day_detail(db, dataset, station, metric, day):
    if metric not in PARAMETERS or not 1800 <= day.year <= 2200:
        raise ValueError('Некорректный параметр или дата')
    summary, _ = daily_series(db, dataset['id'], station, day, day, metric, dataset['source'])
    rows = []
    for row in observations(db, dataset['id'], station, day, day, metric):
        status, reasons = observation_status(row, metric)
        record = dict(row)
        record.pop('cell')
        record.update(status=status, reasons=[{'code': tag, 'label': QC_REASONS.get(tag.rsplit(':', 1)[-1], tag)}
                                              for tag in reasons])
        rows.append(record)
    return {'day': summary[day], 'rows': rows}


def describe_dataset(db, dataset):
    rows = db.execute("""
        SELECT station, COUNT(*) AS rows, MIN(stamp) AS start, MAX(stamp) AS end,
               MIN(received_at) AS first_received, MAX(received_at) AS last_received
        FROM observations WHERE dataset=? GROUP BY station ORDER BY station
    """, (dataset['id'],))
    metadata = json.loads(dataset['metadata'])
    params = set(metadata.get('parameters', [])) | {
        field for mapping, _, _ in metadata.get('mapping', {}).values() for field in mapping
    }
    stations = [dict(row) for row in rows]
    return {**{key: dataset[key] for key in ('id', 'name', 'owner', 'source', 'created_at', 'visibility', 'row_count')},
            'stations': stations, 'parameters': sorted(params & PARAMETERS.keys()), 'metadata': metadata}
