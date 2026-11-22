"""Authenticated stream ingestion, threshold state and persisted webhook outbox."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
import os
from time import perf_counter
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

from pydantic import BaseModel, Field

from meteo.importer import FIELDS, INSERT, PARAMETERS, QC_VERSION, SCHEMA_VERSION, canonical, normalize

FEED = 'stream-reserve'


class Message(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    station: str
    datetime: str
    values: dict
    timezone: str | None = None
    qc_flag: str | None = None


class Rule(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    station: str
    parameter: str
    threshold: float = Field(allow_inf_nan=False)
    minutes: int = Field(ge=0, le=1440)
    coverage: float = Field(gt=0, le=1)
    recipient: str = Field(min_length=1, max_length=120)
    stale_minutes: int = Field(default=30, ge=1, le=10080)
    cadence_minutes: int = Field(default=30, ge=1, le=180)


def utcnow():
    return datetime.now(timezone.utc)


def event_link(rule, stamp):
    day = stamp[:10]
    return '/?' + urlencode({'dataset': FEED, 'station': rule.station, 'parameter': rule.parameter,
                             'start': day, 'end': day, 'step': 'instant', 'day': day})


def ingest(store, owner, message, *, replay=False, origin=None):
    start = perf_counter()
    spec = message.model_dump()
    row = {**message.values, **{key: spec[key] for key in ('station', 'datetime', 'timezone', 'qc_flag')}}
    if set(message.values) - PARAMETERS.keys() or not message.values:
        raise ValueError('Укажите зарегистрированные параметры потока')
    site, stamp, original, zone, values, flags, signature = normalize(
        row, {k: k for k in FIELDS if k in row}, None
    )
    now = utcnow()
    body = canonical(spec)
    with store.connect(write=True) as db:
        previous = db.execute('SELECT body, received_at FROM stream_messages WHERE feed=? AND message_id=?',
                              (FEED, message.id)).fetchone()
        if previous:
            if previous['body'] != body:
                raise ValueError('Идентификатор уже использован с другим содержимым')
            return {'accepted': 0, 'events': 0, 'received_at': previous['received_at'],
                    'dataset': FEED, 'elapsed_ms': round((perf_counter() - start) * 1000, 2)}
        db.execute('INSERT OR IGNORE INTO datasets(id,owner,name,source,visibility) VALUES(?,?,?,?,?)',
                   (FEED, owner, 'Поток заповедника', 'stream', 'shared' if replay else 'private'))
        dataset = db.execute('SELECT owner,metadata FROM datasets WHERE id=?', (FEED,)).fetchone()
        metadata = json.loads(dataset['metadata'])
        mode = 'Воспроизведение архива' if replay else 'Вход HTTPS'
        if dataset['owner'] != owner or metadata.get('mode', mode) != mode:
            raise ValueError('Режим источника уже зафиксирован в базе')
        params = sorted(set(metadata.get('parameters', [])) | message.values.keys())
        metadata.update(mode=mode, parameters=params, schema=SCHEMA_VERSION, qc=QC_VERSION, provider=mode)
        matches = db.execute('SELECT id, flags,signature FROM observations WHERE dataset=? AND station=? AND stamp=? AND zone=?',
                             (FEED, site, stamp, zone)).fetchall()
        if matches:
            incoming = set(filter(None, flags.split(';')))
            conflict = any(r['signature'] != signature or 'dup_conflict' in r['flags'].split(';') for r in matches)
            if conflict:
                incoming.add('dup_conflict')
                for r in matches:
                    tags = set(filter(None, r['flags'].split(';'))) | {'dup_conflict'}
                    db.execute('UPDATE observations SET flags=? WHERE id=?', (';'.join(sorted(tags)), r['id']))
            else:
                incoming.add('dup_exact')
            flags = ';'.join(sorted(incoming))
        source_file, source_row = origin or ('message/' + message.id, 1)
        db.execute(INSERT, (FEED, site, stamp, original, zone, now.isoformat(), source_file,
                            source_row, values, flags, signature))
        db.execute('INSERT INTO stream_messages VALUES (?,?,?,?,?)', (FEED, message.id, body, FEED, now.isoformat()))
        db.execute('UPDATE datasets SET row_count=row_count+1, metadata=? WHERE id=?', (canonical(metadata), FEED))
        events = evaluate(db, now)
    return {'accepted': 1, 'events': events, 'received_at': now.isoformat(),
            'dataset': FEED, 'elapsed_ms': round((perf_counter() - start) * 1000, 2)}


def rule_result(db, rule, now, changed_at):
    latest = db.execute("""
        SELECT MAX(received_at) AS received, MAX(stamp) AS stamp
        FROM observations WHERE dataset=? AND station=?
    """, (FEED, rule.station)).fetchone()
    if not latest['stamp']:
        if (now - datetime.fromisoformat(changed_at)).total_seconds() >= rule.stale_minutes * 60:
            return 'stale', None, now.date().isoformat(), 0
        return 'insufficient', None, now.date().isoformat(), 0
    if (now - datetime.fromisoformat(latest['received'])).total_seconds() >= rule.stale_minutes * 60:
        return 'stale', None, latest['stamp'], 0
    end = datetime.fromisoformat(latest['stamp'])
    start = end - timedelta(minutes=rule.minutes)
    interval = rule.cadence_minutes * 60
    expected = math.ceil(rule.minutes / rule.cadence_minutes) + 1
    points = db.execute("""
        SELECT stamp, json_extract(values_json, ?) AS value FROM observations
        WHERE dataset=? AND station=? AND stamp BETWEEN ? AND ?
        AND instr(';' || flags || ';',';dup_conflict;')=0
        AND instr(';' || flags || ';',';dup_exact;')=0
        AND instr(';' || flags || ';',?)=0
        ORDER BY stamp
    """, ('$.' + rule.parameter, FEED, rule.station, start.isoformat(sep=' '), end.isoformat(sep=' '),
          ';' + rule.parameter + ':')).fetchall()
    slots = {}
    for row in points:
        if row['value'] is not None:
            seconds = (datetime.fromisoformat(row['stamp']) - start).total_seconds()
            slots[min(expected - 1, round(seconds / interval))] = row['value']
    coverage = len(slots) / expected
    current = slots.get(expected - 1)
    if current is None or coverage < rule.coverage or 0 not in slots:
        return 'insufficient', current, latest['stamp'], coverage
    return ('active' if all(value >= rule.threshold for value in slots.values()) else 'normal',
            current, latest['stamp'], coverage)


def evaluate(db, now=None):
    now = now or utcnow()
    events = 0
    for row in db.execute('SELECT * FROM alert_rules').fetchall():
        rule = Rule.model_validate_json(row['body'])
        new, value, stamp, coverage = rule_result(db, rule, now, row['changed_at'])
        old = row['state']
        if old == new:
            continue
        event = new if new in {'active', 'stale'} else 'recovered' if new == 'normal' and old in {'active', 'stale'} else None
        if old in {'active', 'stale'} and new == 'insufficient':
            continue
        db.execute('UPDATE alert_rules SET state=?, changed_at=? WHERE id=?', (new, now.isoformat(), row['id']))
        if event:
            payload = {
                'name': rule.name, 'station': rule.station, 'parameter': rule.parameter, 'value': value,
                'unit': PARAMETERS[rule.parameter][1], 'rule': rule.model_dump(), 'coverage': coverage,
                'measurement': stamp, 'version': FEED + ':' + str(db.execute(
                    'SELECT COUNT(*) FROM stream_messages WHERE feed=?', (FEED,)).fetchone()[0]),
                'recipient': rule.recipient, 'url': event_link(rule, stamp),
            }
            db.execute('INSERT INTO alert_events(id,rule,state,happened_at,body) VALUES (?,?,?,?,?)',
                       (uuid4().hex, row['id'], event, now.isoformat(), canonical(payload)))
            events += 1
    return events


def save_rule(store, owner, rule):
    rule.station = rule.station.strip().upper()
    if rule.parameter not in PARAMETERS or not rule.station:
        raise ValueError('Укажите известный параметр и станцию')
    identifier = sha256(canonical([owner, rule.model_dump()]).encode()).hexdigest()[:24]
    with store.connect(write=True) as db:
        db.execute('INSERT OR IGNORE INTO alert_rules(id,owner,body,changed_at) VALUES (?,?,?,?)',
                   (identifier, owner, rule.model_dump_json(), utcnow().isoformat()))
        evaluate(db)
    return {'id': identifier}


def notifications(store, user, demo):
    with store.connect() as db:
        rules = [dict(row, body=json.loads(row['body'])) for row in db.execute(
            'SELECT id, owner, body, state, changed_at FROM alert_rules ORDER BY changed_at DESC'
        )]
        rules = [r for r in rules if user == r['owner'] or user == r['body']['recipient']]
        allowed = {r['id'] for r in rules}
        events = []
        for row in db.execute('SELECT * FROM alert_events ORDER BY happened_at DESC LIMIT 1000'):
            if row['rule'] in allowed and len(events) < 50:
                events.append(dict(row, body=json.loads(row['body'])))
        dataset = db.execute('SELECT * FROM datasets WHERE id=?', (FEED,)).fetchone()
        feed = None
        if dataset and (dataset['visibility'] == 'shared' or dataset['owner'] == user):
            newest = db.execute('SELECT MAX(received_at) FROM stream_messages WHERE feed=?', (FEED,)).fetchone()[0]
            feed = {'id': FEED, 'mode': json.loads(dataset['metadata'])['mode'], 'last_received': newest}
    return {'rules': rules, 'events': events, 'feed': feed, 'demo': demo}


def dispatch(store):
    destination = os.getenv('METEO_WEBHOOK_URL')
    if not destination:
        return
    with store.connect() as db:
        waiting = db.execute("SELECT * FROM alert_events WHERE delivery='pending' ORDER BY happened_at LIMIT 10").fetchall()
    for row in waiting:
        payload = canonical(dict(row, body=json.loads(row['body']))).encode()
        request = Request(destination, data=payload, method='POST', headers={
            'Content-Type': 'application/json', 'Idempotency-Key': row['id'],
        })
        try:
            with urlopen(request, timeout=3) as response:
                if not 200 <= response.status < 300:
                    continue
        except OSError:
            continue
        with store.connect(write=True) as db:
            db.execute("UPDATE alert_events SET delivery='sent' WHERE id=?", (row['id'],))
