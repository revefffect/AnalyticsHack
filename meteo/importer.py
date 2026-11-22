"""Read supported transports and retain provenance before the shared QC step."""
from collections import Counter
from contextlib import closing
import csv
from datetime import date, datetime
from hashlib import sha256
from io import StringIO, TextIOWrapper
import json
import math
from pathlib import Path, PurePosixPath
import re
from zipfile import BadZipFile, ZipFile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

SCHEMA_VERSION = 'meteo-1'
QC_VERSION = 'case02-1'
STATIONS = {'AME60': 'АМЕ-60', 'KR1': 'Сокол М1 · Кр1, поляна', 'KR2': 'Сокол М1 · Кр2, поле'}
PARAMETERS = {
    't_air': ('Температура воздуха', '°C', -50, 45),
    'rh': ('Влажность', '%', 0, 100),
    'pressure_hpa': ('Давление', 'гПа', 940, 1050),
    'wind_speed': ('Скорость ветра', 'м/с', 0, 60),
    'wind_gust': ('Порывы ветра', 'м/с', 0, 75),
    'wind_dir': ('Направление ветра', '°', 0, 360),
    'soil_05': ('Температура почвы · 5 см', '°C', -40, 45),
    'soil_10': ('Температура почвы · 10 см', '°C', -40, 45),
    'soil_20': ('Температура почвы · 20 см', '°C', -40, 45),
    'precipitation': ('Осадки за интервал', 'мм', 0, None),
}
FIELDS = {'datetime', 'station', 'timezone', 'qc_flag', *PARAMETERS}
ALIASES = {
    'datetime': ('datetime', 'timestamp', 'date_time', 'время', 'дата и время', 'time'),
    'station': ('station', 'station_id', 'станция'),
    'timezone': ('timezone', 'tz', 'часовой пояс'),
    'qc_flag': ('qc_flag', 'flags', 'флаги'),
    't_air': ('t_air', 'temperature', 'temperature_2m', 'температура'),
    'rh': ('rh', 'relative_humidity', 'relative_humidity_2m', 'влажность'),
    'pressure_hpa': ('pressure_hpa', 'surface_pressure', 'давление'),
}
MAX_EXPANDED = 250 * 1024 * 1024
INSERT = """
INSERT INTO observations(dataset, station, stamp, original_stamp, zone, received_at,
 source_file, source_row, values_json, flags, signature) VALUES (?,?,?,?,?,?,?,?,?,?,?)
"""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def encoding_of(sample):
    for codec in ('utf-8-sig', 'cp1251'):
        try:
            # The prefix can end within a multi-byte character.
            import codecs
            codecs.getincrementaldecoder(codec)().decode(sample, final=False)
            return codec
        except UnicodeDecodeError:
            continue
    raise ValueError('Кодировка не определена')


def csv_rows(raw):
    sample = raw.read(4096)
    raw.seek(0)
    encoding = encoding_of(sample)
    first = sample.splitlines()[0].decode(encoding) if sample else ''
    delimiter = max((',', ';', '\t'), key=first.count)
    with TextIOWrapper(raw, encoding=encoding, newline='') as inp:
        reader = csv.DictReader(inp, delimiter=delimiter)
        headers = check_headers(reader.fieldnames or [])
        yield headers, {'encoding': encoding, 'delimiter': delimiter}
        for row in reader:
            if None in row:
                raise ValueError(f'Лишние значения в строке {reader.line_num}')
            yield reader.line_num, row


def check_headers(headers):
    if any(not h for h in headers) or len(headers) != len(set(headers)):
        raise ValueError('Заголовки должны быть заполнены и уникальны')
    return headers


def objects_rows(data):
    if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
        raise ValueError('JSON должен содержать массив объектов или поле observations')
    headers = list(dict.fromkeys(key for row in data for key in row))
    yield check_headers(headers), {}
    yield from enumerate(data, 1)


def xlsx_rows(sheet):
    values = sheet.values
    headers = check_headers([str(value) if value is not None else '' for value in next(values, ())])
    yield headers, {'sheet': sheet.title}
    for number, row in enumerate(values, 2):
        if any(value is not None for value in row):
            yield number, dict(zip(headers, row))


def tables(path, filename):
    suffix = Path(filename).suffix.lower()
    if suffix in ('.zip', '.xlsx'):
        with ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 2000 or sum(file.file_size for file in entries) > MAX_EXPANDED:
                raise ValueError('Архив превышает допустимый объём')
            if len({file.filename for file in entries}) != len(entries):
                raise ValueError('В архиве повторяется имя файла')
            if suffix == '.zip':
                files = [f for f in entries if not f.is_dir() and
                         PurePosixPath(f.filename).suffix.lower() == '.csv' and
                         PurePosixPath(f.filename).name.lower() != 'coverage_by_month.csv']
                for file in files:
                    with archive.open(file) as raw:
                        yield file.filename, csv_rows(raw)
                return
        with path.open('rb') as inp, closing(load_workbook(inp, read_only=True, data_only=True)) as book:
            for sheet in book.worksheets:
                yield sheet.title, xlsx_rows(sheet)
    elif suffix == '.csv':
        with path.open('rb') as raw:
            yield filename, csv_rows(raw)
    elif suffix in ('.json', '.jsonl'):
        content = path.read_text(encoding='utf-8-sig')
        if suffix == '.jsonl':
            data = [json.loads(line) for line in StringIO(content) if line.strip()]
        else:
            data = json.loads(content)
            if isinstance(data, dict):
                data = data.get('observations')
        yield filename, objects_rows(data)
    else:
        raise ValueError('Поддерживаются ZIP, CSV, XLSX, JSON и JSONL')


def suggest(headers):
    mapped, ambiguous = {}, {}
    for field in sorted(FIELDS):
        aliases = ALIASES.get(field, (field,))
        candidates = [h for h in headers if h.strip().lower() in aliases]
        if len(candidates) == 1:
            mapped[field] = candidates[0]
        elif candidates:
            ambiguous[field] = candidates
    return mapped, ambiguous


def station_from_name(filename):
    match = re.match(r'^(ame60|kr1|kr2)(?:[^a-z0-9]|$)', Path(filename).stem.lower())
    return match[1].upper() if match else None


def preview(path, filename):
    result = []
    try:
        for name, rows in tables(path, filename):
            headers, info = next(rows)
            mapping, ambiguous = suggest(headers)
            sample = [row for _, row in zip(range(4), rows)]
            result.append({
                'name': name, 'columns': headers, 'mapping': mapping, 'ambiguous': ambiguous,
                'station': station_from_name(name), 'sample': [row for _, row in sample],
                'types': {key: sorted({type(row.get(key)).__name__ for _, row in sample
                                       if row.get(key) not in (None, '')}) for key in headers},
                'units': {field: PARAMETERS[field][1] for field in mapping if field in PARAMETERS},
                'conversions': ['Числа: десятичная запятая или точка', 'Дата и время: исходное локальное время'],
                **info,
            })
    except (BadZipFile, InvalidFileException, UnicodeError, csv.Error) as exc:
        raise ValueError(f'Невозможно прочитать файл: {exc}') from exc
    if not result:
        raise ValueError('Файл не содержит таблиц наблюдений')
    return result


def timestamp(value, zone=None):
    if not isinstance(value, (str, datetime)):
        raise ValueError('Требуется дата и время ISO 8601')
    original = str(value).strip()
    if not re.search(r'[T ]\d{2}:\d{2}', original):
        raise ValueError('Требуется дата и время ISO 8601')
    stamp = datetime.fromisoformat(original)
    if stamp.tzinfo is not None:
        offset = stamp.strftime('%z')
        zone = offset[:3] + ':' + offset[3:]
    elif zone:
        try:
            ZoneInfo(zone)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError('Укажите часовой пояс IANA или смещение в отметке времени') from exc
    return stamp.replace(tzinfo=None).isoformat(sep=' '), original, zone or 'unknown'


def normalize(row, mapping, fallback_station, fallback_zone=None):
    stamp, original, zone = timestamp(row.get(mapping['datetime']), row.get(mapping.get('timezone')) or fallback_zone)
    station = str(row.get(mapping.get('station')) or fallback_station or '').strip().upper()
    if not re.fullmatch(r'[A-Z0-9][A-Z0-9_-]{0,49}', station):
        raise ValueError('Укажите идентификатор станции латиницей')
    tags = row.get(mapping.get('qc_flag')) or ''
    if isinstance(tags, str):
        tags = tags.split(';')
    if not isinstance(tags, list) or any(not isinstance(tag, str) or ';' in tag for tag in tags):
        raise ValueError('Некорректный формат флагов QC')
    flags = {tag.strip() for tag in tags if tag.strip()}
    values = {}
    for parameter in PARAMETERS.keys() & mapping.keys():
        if mapping[parameter] not in row:
            continue
        value = row[mapping[parameter]]
        if value is None or isinstance(value, str) and not value.strip():
            values[parameter] = None
            continue
        try:
            number = float(str(value).strip().replace(',', '.'))
            if not math.isfinite(number):
                raise ValueError
        except ValueError:
            values[parameter] = None
            flags.add(parameter + ':parse')
            continue
        values[parameter] = number
        _, _, minimum, maximum = PARAMETERS[parameter]
        if number < minimum or (maximum is not None and number > maximum):
            flags.add(parameter + ':range')
    values_json, flags_text = canonical(values), ';'.join(sorted(flags))
    return station, stamp, original, zone, values_json, flags_text, values_json + '|' + flags_text


def import_upload(store, owner, digest, *, name=None, options=None, metadata=None, source='station'):
    filename, path = store.upload(owner, digest)
    options = options or {}
    details = preview(path, filename)
    available = {table['name'] for table in details}
    if options.keys() - available:
        raise ValueError('Таблица для маппинга не найдена')
    config = {}
    for table in details:
        tab = table['name']
        choice = options.get(tab, {})
        if choice.get('skip'):
            continue
        mapping = choice.get('mapping', table['mapping'])
        if table['ambiguous'] and 'mapping' not in choice:
            raise ValueError(f'{tab}: требуется подтвердить неоднозначные колонки')
        if set(mapping) - FIELDS or set(mapping.values()) - set(table['columns']):
            raise ValueError(f'{tab}: маппинг содержит неизвестные поля')
        if len(mapping) != len(set(mapping.values())):
            raise ValueError(f'{tab}: каждой колонке соответствует одно поле')
        station = choice.get('station') or table['station']
        if ('datetime' not in mapping or not PARAMETERS.keys() & mapping.keys()
                or not (station or 'station' in mapping)):
            raise ValueError(f'{tab}: укажите время, станцию и хотя бы один показатель')
        config[tab] = (mapping, station, choice.get('timezone'))
    if not config:
        raise ValueError('Выберите хотя бы одну таблицу')
    identity = [owner, digest, source, metadata, config, SCHEMA_VERSION, QC_VERSION]
    version = sha256(canonical(identity).encode()).hexdigest()[:32]
    with store.connect(write=True) as db:
        prior = db.execute('SELECT row_count FROM datasets WHERE id=?', (version,)).fetchone()
        if prior:
            return {'id': version, 'row_count': prior['row_count'], 'reused': True}
        db.execute('INSERT INTO datasets(id,owner,name,raw_hash,source,metadata) VALUES(?,?,?,?,?,?)',
                   (version, owner, name or filename, digest, source,
                    canonical({**(metadata or {}), 'schema': SCHEMA_VERSION, 'qc': QC_VERSION, 'mapping': config})))
        count = 0
        for tab, rows in tables(path, filename):
            if tab not in config:
                continue
            next(rows)
            mapping, station, zone = config[tab]
            batch = []
            for number, row in rows:
                try:
                    site, stamp, original, tz, values, flags, signature = normalize(row, mapping, station, zone)
                except ValueError as exc:
                    raise ValueError(f'{tab}, строка {number}: {exc}') from exc
                batch.append((version, site, stamp, original, tz, None, tab, number, values, flags, signature))
                if len(batch) == 3000:
                    db.executemany(INSERT, batch)
                    count += len(batch)
                    batch.clear()
            db.executemany(INSERT, batch)
            count += len(batch)
        if not count:
            raise ValueError('В выбранных таблицах нет наблюдений')
        flag_duplicate_rows(db, version)
        db.execute('UPDATE datasets SET row_count=? WHERE id=?', (count, version))
    return {'id': version, 'row_count': count, 'reused': False}


def flag_duplicate_rows(db, version):
    groups = db.execute("""
        SELECT station, stamp, zone, COUNT(DISTINCT signature) AS variants
        FROM observations WHERE dataset=? GROUP BY station, stamp, zone HAVING COUNT(*) > 1
    """, (version,)).fetchall()
    for group in groups:
        key = (version, group['station'], group['stamp'], group['zone'])
        records = db.execute("""
            SELECT id, flags FROM observations WHERE dataset=? AND station=? AND stamp=? AND zone=? ORDER BY id
        """, key).fetchall()
        for index, row in enumerate(records):
            tags = set(filter(None, row['flags'].split(';')))
            if group['variants'] > 1 or any('dup_conflict' in r['flags'].split(';') for r in records):
                tags.add('dup_conflict')
            elif index:
                tags.add('dup_exact')
            db.execute('UPDATE observations SET flags=? WHERE id=?', (';'.join(sorted(tags)), row['id']))
