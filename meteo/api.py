"""Application HTTP contracts shared by the two workspaces and the demo client."""
import asyncio
from contextlib import asynccontextmanager, suppress
import csv
from datetime import date, datetime, timezone
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
import secrets

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from meteo.analytics import PERIODS, QC_REASONS, day_detail, describe_dataset, slice_data
from meteo.connectors import WeatherRequest, open_meteo
from meteo.grafana import dashboard_url, provision, shared_dataset
from meteo.satellite import imagery, options as satellite_options
from meteo.stream import FEED, Message, Rule, dispatch, evaluate, ingest, notifications, save_rule
from meteo.importer import FIELDS, PARAMETERS, STATIONS, canonical, import_upload, preview
from meteo.store import Store

MAX_UPLOAD = 50 * 1024 * 1024
WEB = Path(__file__).parent / 'web'
ARCHIVE = Path(__file__).resolve().parents[1] / 'data/raw/meteo_hack_dataset.zip'
ARCHIVE_NAME = 'Центрально-Лесной заповедник · урочище Красное'
ARCHIVE_META = {
    'provider': 'Центрально-Лесной государственный природный биосферный заповедник',
    'territory': 'Урочище Красное', 'type': 'Наблюдения станций',
    'license': 'Материалы кейса 02 для участников хакатона',
    'cadence': 'АМЕ-60 15 мин, Сокол М1 30–60 мин', 'format': 'ZIP / CSV',
}
CATALOG = [
    {'id': 'open-meteo', 'name': 'Open-Meteo · ERA5 и прогноз', 'provider': 'Open-Meteo / ECMWF',
     'type': 'Реанализ / модельный прогноз', 'territory': 'По координатам',
     'cadence': '1 час', 'format': 'JSON', 'coverage': 'ERA5 с 1940 года',
     'parameters': 'Температура, влажность, давление и осадки',
     'status': 'Подключение через API; требуется проверить доступность',
     'license': 'Данные CC BY 4.0 с атрибуцией. Для коммерческого hosted API нужен клиентский тариф.',
     'url': 'https://open-meteo.com/en/docs/historical-weather-api'},
    {'id': 'meteo-ru', 'name': 'ВНИИГМИ-МЦД · метеорологические архивы',
     'provider': 'ВНИИГМИ-МЦД / Росгидромет', 'type': 'Наблюдения станций',
     'status': 'Ручной доступ через карточку массива', 'territory': 'Россия',
     'license': 'Проверить условия конкретного массива перед повторной публикацией',
     'instructions': 'Выберите массив в официальном каталоге. Скачайте доступный файл или свяжитесь с поставщиком, затем импортируйте CSV, XLSX, JSON или ZIP.',
     'url': 'https://meteo.ru/data'},
]


class Selection(BaseModel):
    station: str
    parameter: str
    start: date
    end: date
    step: str = 'month'


class ImportOptions(BaseModel):
    options: dict[str, dict] = Field(default_factory=dict)
    name: str | None = None
    template: str | None = None


class ReportOptions(Selection):
    title: str = Field(default='Метеорологическая сводка', max_length=150)
    template: str = Field(default='Обзор показателя', max_length=80)


class ReplayOptions(BaseModel):
    cursor: int = Field(ge=0, le=100000, default=0)


class ViewOptions(BaseModel):
    columns: list[tuple[str, str]] = Field(max_length=12)


def seed(store):
    digest = store.save_blob('research', ARCHIVE.name, ARCHIVE.read_bytes())
    info = import_upload(store, 'research', digest, name=ARCHIVE_NAME, metadata=ARCHIVE_META)
    with store.connect(write=True) as db:
        db.execute("UPDATE datasets SET visibility='shared' WHERE id=?", (info['id'],))
    return info


def can_read(dataset, user):
    return dataset['owner'] == user['name'] or dataset['visibility'] == 'shared'


def pick_dataset(db, version, user):
    dataset = db.execute('SELECT * FROM datasets WHERE id=?', (version,)).fetchone()
    if dataset is None:
        raise HTTPException(404, 'Набор недоступен')
    permissions = dataset
    if dataset['source'] == 'stream_snapshot':
        source = json.loads(dataset['metadata'])['source_dataset']
        permissions = db.execute('SELECT * FROM datasets WHERE id=?', (source,)).fetchone()
    if permissions is None or not can_read(permissions, user):
        raise HTTPException(404, 'Набор недоступен')
    return dataset


def get_accounts(config, demo):
    if config is not None:
        return config
    if demo:
        return {'research': {'token': 'research-demo', 'role': 'research'},
                'business': {'token': 'business-demo', 'role': 'business'}}
    return json.loads(os.getenv('METEO_ACCOUNTS') or '{}')


def create_app(root=None, *, demo=None, accounts=None):
    demo = os.getenv('METEO_DEMO', '') == '1' if demo is None else demo
    accounts = get_accounts(accounts, demo)

    def feed_tick():
        store = current_store()
        with store.connect(write=True) as db:
            evaluate(db)
        dispatch(store)

    async def schedule():
        while True:
            await asyncio.sleep(15)
            await run_in_threadpool(feed_tick)

    @asynccontextmanager
    async def lifespan(_app):
        if demo:
            await run_in_threadpool(seed, current_store())
        task = asyncio.create_task(schedule())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title='AnalyticsHack · метеоданные', lifespan=lifespan)
    store = None

    def current_store():
        nonlocal store
        if store is None:
            store = Store(root or os.getenv('METEO_STORAGE', 'runtime'))
        return store

    def actor(authorization: str = Header(default='')):
        token = authorization.removeprefix('Bearer ') if authorization.startswith('Bearer ') else ''
        for name, account in accounts.items():
            if token and secrets.compare_digest(token.encode(), account['token'].encode()):
                return {'name': name, 'role': account['role']}
        raise HTTPException(401, 'Войдите в кабинет')

    def researcher(user=Depends(actor)):
        if user['role'] != 'research':
            raise HTTPException(403, 'Действие доступно исследователю')
        return user

    @app.exception_handler(ValueError)
    async def bad_input(_request, exc):
        return Response(json.dumps({'detail': str(exc)}, ensure_ascii=False),
                        status_code=422, media_type='application/json')

    @app.exception_handler(LookupError)
    async def missing(_request, exc):
        return Response(json.dumps({'detail': str(exc)}, ensure_ascii=False),
                        status_code=404, media_type='application/json')

    @app.get('/health')
    def health():
        return {'status': 'ok', 'demo': demo}

    @app.get('/api/session')
    def session(user=Depends(actor)):
        return user

    @app.get('/api/dictionary')
    def dictionary(user=Depends(actor)):
        return {'stations': STATIONS, 'parameters': {
            key: {'name': item[0], 'unit': item[1]} for key, item in PARAMETERS.items()
        }, 'periods': PERIODS, 'flags': QC_REASONS, 'fields': sorted(FIELDS)}

    @app.get('/api/datasets')
    def datasets(user=Depends(actor)):
        with current_store().connect() as db:
            rows = db.execute("""
                SELECT * FROM datasets WHERE (owner=? OR visibility='shared')
                AND source!='stream_snapshot' ORDER BY created_at DESC, name
            """, (user['name'],)).fetchall()
            return [describe_dataset(db, row) for row in rows]

    @app.get('/api/catalog')
    def catalog(q: str = '', user=Depends(actor)):
        with current_store().connect() as db:
            connected = {r['dataset'] for r in db.execute('SELECT dataset FROM subscriptions WHERE owner=?',
                                                          (user['name'],))}
        cards = [*CATALOG, *(dict(data, connected=data['id'] in connected) for data in datasets(user))]
        return [card for card in cards if q.casefold() in canonical(card).casefold()]

    @app.post('/api/datasets/{version}/connect')
    def connect_dataset(version: str, user=Depends(actor)):
        with current_store().connect(write=True) as db:
            pick_dataset(db, version, user)
            db.execute('INSERT OR IGNORE INTO subscriptions VALUES (?,?)', (user['name'], version))
        return {'connected': True}

    @app.put('/api/datasets/{version}/visibility')
    def visibility(version: str, shared: bool, jobs: BackgroundTasks, user=Depends(researcher)):
        with current_store().connect(write=True) as db:
            dataset = pick_dataset(db, version, user)
            if dataset['owner'] != user['name'] or dataset['source'] == 'stream_snapshot':
                raise HTTPException(404, 'Набор недоступен')
            state = 'shared' if shared else 'private'
            db.execute('UPDATE datasets SET visibility=? WHERE id=?', (state, version))
            if shared and shared_dataset(db, version) is not None:
                jobs.add_task(provision, describe_dataset(db, dataset))
        return {'visibility': state}

    @app.post('/api/uploads')
    async def upload(request: Request, filename: str, user=Depends(researcher)):
        filename = Path(filename).name.strip()
        if not filename or len(filename) > 200:
            raise ValueError('Укажите имя файла')
        payload = bytearray()
        async for chunk in request.stream():
            if len(chunk) + len(payload) > MAX_UPLOAD:
                raise HTTPException(413, 'Файл превышает 50 МБ')
            payload.extend(chunk)
        store = current_store()
        digest = await run_in_threadpool(store.save_blob, user['name'], filename, payload)
        stored_name, path = store.upload(user['name'], digest)
        return {'hash': digest, 'tables': await run_in_threadpool(preview, path, stored_name)}

    @app.post('/api/imports/{digest}')
    def apply_import(digest: str, config: ImportOptions, user=Depends(researcher)):
        store = current_store()
        validated = import_upload(store, user['name'], digest, name=config.name, options=config.options)
        if config.template:
            with store.connect(write=True) as db:
                db.execute('INSERT OR REPLACE INTO settings VALUES (?, ?, ?, ?)',
                           (user['name'], 'mapping', config.template[:100], canonical(config.options)))
        return validated

    @app.get('/api/templates')
    def templates(user=Depends(researcher)):
        with current_store().connect() as db:
            rows = db.execute("SELECT name, body FROM settings WHERE owner=? AND kind='mapping'", (user['name'],))
            return {row['name']: json.loads(row['body']) for row in rows}

    @app.get('/api/views')
    def views(user=Depends(actor)):
        with current_store().connect() as db:
            rows = db.execute("SELECT name, body FROM settings WHERE owner=? AND kind='view'", (user['name'],))
            return {row['name']: json.loads(row['body']) for row in rows}

    @app.put('/api/views/{name}')
    def save_view(name: str, config: ViewOptions, user=Depends(actor)):
        with current_store().connect(write=True) as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?, ?, ?, ?)',
                       (user['name'], 'view', name[:100], canonical(config.columns)))
        return {'saved': True}

    @app.get('/api/datasets/{version}/raw')
    def raw_download(version: str, user=Depends(actor)):
        store = current_store()
        with store.connect() as db:
            dataset = pick_dataset(db, version, user)
        if not dataset['raw_hash']:
            raise HTTPException(404, 'Исходный файл отсутствует')
        filename, path = store.upload(dataset['owner'], dataset['raw_hash'])
        return FileResponse(path, filename=filename)

    def analyze(version, db, user, station, parameter, start, end, step):
        dataset = pick_dataset(db, version, user)
        return slice_data(db, dataset, station, parameter, start, end, step)

    @app.get('/api/datasets/{version}/series')
    def series(version: str, station: str, parameter: str, start: date, end: date,
               step: str = 'month', user=Depends(actor)):
        with current_store().connect() as db:
            return analyze(version, db, user, station, parameter, start, end, step)

    @app.get('/api/datasets/{version}/quality')
    def quality(version: str, station: str, parameter: str, day: date, user=Depends(actor)):
        with current_store().connect() as db:
            return day_detail(db, pick_dataset(db, version, user), station, parameter, day)

    @app.get('/api/datasets/{version}/export')
    def export(version: str, station: str, parameter: str, start: date, end: date,
               step: str = 'month', format: str = 'csv', user=Depends(actor)):
        data = series(version, station, parameter, start, end, step, user)
        if format == 'json':
            return data
        if format != 'csv':
            raise ValueError('Поддерживаются CSV и JSON')
        output = StringIO()
        fields = ['dataset', 'station', 'parameter', 'unit', 'qc', 'source', 'start', 'end', 'value',
                  'valid_days', 'expected_days', 'partial', 'longest_gap', 'flags', 'deviation', 'original_stamp', 'zone', 'received_at']
        writer = csv.DictWriter(output, fields, extrasaction='ignore')
        writer.writeheader()
        for point in data['points']:
            writer.writerow({**data, **point, 'flags': canonical(point['flags'])})
        return Response('\ufeff' + output.getvalue(), media_type='text/csv',
                        headers={'Content-Disposition': 'attachment; filename="meteo.csv"'})

    @app.get('/api/reports')
    def report_list(user=Depends(actor)):
        with current_store().connect() as db:
            ids = db.execute("""
                SELECT r.id, r.created_at, json_extract(r.body, '$.title') AS title, r.dataset
                FROM reports r JOIN datasets d ON r.dataset=d.id
                LEFT JOIN datasets base ON base.id=json_extract(d.metadata,'$.source_dataset')
                WHERE r.owner=? AND (COALESCE(base.owner,d.owner)=?
                OR COALESCE(base.visibility,d.visibility)='shared') ORDER BY r.created_at DESC
            """, (user['name'], user['name']))
            return [dict(row) for row in ids]

    @app.post('/api/datasets/{version}/reports')
    def make_report(version: str, request: ReportOptions, user=Depends(actor)):
        with current_store().connect(write=True) as db:
            dataset = pick_dataset(db, version, user)
            if dataset['source'] == 'stream':
                snapshot = sha256((version + ':' + str(dataset['row_count'])).encode()).hexdigest()[:32]
                metadata = {**json.loads(dataset['metadata']), 'source_dataset': version}
                inserted = db.execute('''
                    INSERT OR IGNORE INTO datasets(id,owner,name,source,metadata,row_count,visibility)
                    VALUES (?,?,?,?,?,?,?)
                ''', (snapshot, dataset['owner'], dataset['name'], 'stream_snapshot', canonical(metadata),
                      dataset['row_count'], dataset['visibility'])).rowcount
                if inserted:
                    db.execute('''
                        INSERT INTO observations(dataset,station,stamp,original_stamp,zone,received_at,
                         source_file,source_row,values_json,flags,signature)
                        SELECT ?,station,stamp,original_stamp,zone,received_at,
                         source_file,source_row,values_json,flags,signature FROM observations WHERE dataset=?
                    ''', (snapshot, version))
                version = snapshot
                dataset = pick_dataset(db, version, user)
            metrics = request.model_dump()
            data = analyze(version, db, user, **{key: metrics[key] for key in Selection.model_fields})
            report_id = secrets.token_hex(12)
            fresh = db.execute('SELECT MAX(stamp),MAX(received_at) FROM observations WHERE dataset=? AND station=?',
                               (version, request.station)).fetchone()
            events = []
            for event in notifications(current_store(), user['name'], demo)['events']:
                body = event['body']
                if dataset['source'] == 'stream_snapshot' and (body['station'], body['parameter']) == (
                    request.station, request.parameter
                ) and str(request.start) <= body['measurement'][:10] <= str(request.end):
                    events.append({'state': event['state'], 'value': body['value'], 'unit': body['unit'],
                                   'measurement': body['measurement'], 'received_at': event['happened_at']})
            data.update(id=report_id, title=request.title, template=request.template,
                        metadata=json.loads(dataset['metadata']), freshness={'measurement': fresh[0], 'received': fresh[1]},
                        events=events)
            db.execute('INSERT INTO reports(id,owner,dataset,body) VALUES (?,?,?,?)',
                       (report_id, user['name'], version, canonical(data)))
            return data

    @app.get('/api/reports/{report_id}')
    def read_report(report_id: str, user=Depends(actor)):
        with current_store().connect() as db:
            row = db.execute('SELECT dataset,body FROM reports WHERE id=? AND owner=?',
                             (report_id, user['name'])).fetchone()
            if row is None:
                raise HTTPException(404, 'Отчёт не найден')
            pick_dataset(db, row['dataset'], user)
            return json.loads(row['body'])

    @app.get('/api/satellite')
    def satellite_config(user=Depends(actor)):
        return satellite_options()

    @app.get('/api/satellite/imagery')
    def satellite_image(area: str, layer: str, day: date, user=Depends(actor)):
        return imagery(area, layer, day)

    @app.post('/api/connectors/open-meteo')
    def connect_weather(request: WeatherRequest, user=Depends(researcher)):
        try:
            return open_meteo(current_store(), user['name'], request)
        except ConnectionError as exc:
            raise HTTPException(502, str(exc)) from exc

    @app.get('/api/datasets/{version}/dashboard')
    def dashboard(version: str, station: str, parameter: str, start: date, end: date,
                  step: str = 'month', user=Depends(actor)):
        with current_store().connect() as db:
            dataset = pick_dataset(db, version, user)
            if shared_dataset(db, dataset['id']) is None:
                return {'url': None, 'detail': 'Сервис Grafana подключён к общему рабочему пространству.'}
            details = describe_dataset(db, dataset)
        link = provision(details)
        return {'url': dashboard_url(link, station, parameter, start, end, step) if link else None,
                'detail': 'Grafana ещё не подключена. Те же расчёты доступны в кабинете.'}

    @app.get('/api/grafana/{version}')
    def dashboard_data(version: str, station: str, parameter: str, start: date, end: date,
                       step: str = 'month', authorization: str = Header(default='')):
        key = os.getenv('METEO_GRAFANA_READ_TOKEN')
        if not key or not secrets.compare_digest(authorization.encode(), ('Bearer ' + key).encode()):
            raise HTTPException(401, 'Для источника Grafana нужен сервисный ключ')
        with current_store().connect() as db:
            dataset = shared_dataset(db, version)
            if dataset is None:
                raise HTTPException(404, 'Набор недоступен источнику Grafana')
            data = slice_data(db, dataset, station, parameter, start, end, step)
        return {'dataset': version, 'unit': data['unit'], 'points': [{
            'time': int(datetime.fromisoformat(row['start']).replace(tzinfo=timezone.utc).timestamp() * 1000),
            'value': row['value'], 'coverage_pct': 100 * row['valid_days'] / row['expected_days'],
        } for row in data['points']]}

    @app.post('/api/stream/ingest')
    def incoming(message: Message, authorization: str = Header(default='')):
        key = os.getenv('METEO_FEED_TOKEN')
        if not key or not secrets.compare_digest(authorization.encode(), ('Bearer ' + key).encode()):
            raise HTTPException(401, 'Недействительный ключ потока')
        owner = os.getenv('METEO_FEED_OWNER', 'research')
        return ingest(current_store(), owner, message)

    @app.get('/api/alerts')
    def alerts(user=Depends(actor)):
        return notifications(current_store(), user['name'], demo)

    @app.post('/api/alerts/rules')
    def put_rule(rule: Rule, user=Depends(researcher)):
        if user['name'] != os.getenv('METEO_FEED_OWNER', 'research'):
            raise HTTPException(403, 'Правила меняет владелец потока')
        if rule.recipient not in accounts:
            raise ValueError('Получатель должен иметь кабинет')
        return save_rule(current_store(), user['name'], rule)

    @app.post('/api/demo/replay')
    def replay(request: ReplayOptions, user=Depends(researcher)):
        if not demo or user['name'] != 'research':
            raise HTTPException(404, 'Демо недоступно')
        store = current_store()
        save_rule(store, 'research', Rule(name='Тёплый период · архив Кр1', station='KR1', parameter='t_air',
                  threshold=20, minutes=0, coverage=1, recipient='business'))
        with store.connect() as db:
            dataset = db.execute("SELECT id FROM datasets WHERE raw_hash IS NOT NULL AND name=? ORDER BY created_at LIMIT 1",
                                 (ARCHIVE_NAME,)).fetchone()
            rising = db.execute("""
                SELECT stamp, original_stamp, source_file, source_row, json_extract(values_json,'$.t_air') AS value
                FROM observations WHERE dataset=? AND station='KR1' AND stamp LIKE '2024-07-%'
                AND flags NOT LIKE '%t_air:%' AND flags NOT LIKE '%dup_conflict%'
                AND value >=20 ORDER BY stamp LIMIT 1
            """, (dataset['id'],)).fetchone()
            falling = db.execute("""
                SELECT original_stamp, source_file, source_row, json_extract(values_json,'$.t_air') AS value
                FROM observations WHERE dataset=? AND station='KR1' AND stamp>? AND stamp LIKE '2024-07-%'
                AND flags NOT LIKE '%t_air:%' AND flags NOT LIKE '%dup_conflict%'
                AND value <20 ORDER BY stamp LIMIT 1
            """, (dataset['id'], rising['stamp'])).fetchone()
        rows = [rising, falling]
        cursor = request.cursor % 2
        row = rows[cursor]
        result = ingest(store, 'research', Message(id='archive-demo-' + str(cursor), station='KR1',
                        datetime=row['original_stamp'], values={'t_air': row['value']}), replay=True,
                        origin=(row['source_file'], row['source_row']))
        return {**result, 'next_cursor': (cursor + 1) % 2,
                'archive_row': {'file': row['source_file'], 'row': row['source_row'],
                                'measurement': row['original_stamp'], 'value': row['value']}}

    @app.get('/')
    def home():
        return FileResponse(WEB / 'index.html')

    app.mount('/assets', StaticFiles(directory=WEB), name='assets')
    return app


app = create_app()
