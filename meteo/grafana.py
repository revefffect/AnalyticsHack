"""Provision shared dashboards with the same series as the workspace."""
from base64 import b64encode
from hashlib import sha256
import json
import os
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from meteo.analytics import PERIODS
from meteo.importer import PARAMETERS, STATIONS

DATASOURCE = {'type': 'yesoreyeram-infinity-datasource', 'uid': 'meteo-shared'}


def shared_dataset(db, version):
    return db.execute("""
        SELECT * FROM datasets WHERE id=? AND visibility='shared' AND source!='stream_snapshot' AND owner=?
    """, (version, os.getenv('METEO_GRAFANA_OWNER', 'research'))).fetchone()


def uid_for(version):
    return 'meteo-' + sha256(version.encode()).hexdigest()[:24]


def selector(name, label, values):
    options = [{'text': l, 'value': value, 'selected': i == 0} for i, (value, l) in enumerate(values)]
    return {'name': name, 'label': label, 'type': 'custom', 'query': ','.join(
        f'{option["text"]} : {option["value"]}' for option in options), 'options': options, 'current': options[0]}


def dashboard(dataset):
    uid = uid_for(dataset['id'])
    version = dataset['id']
    stations = [(row['station'], STATIONS.get(row['station'], row['station'])) for row in dataset['stations']]
    variables = [
        selector('station', 'Станция', stations),
        selector('parameter', 'Параметр', [(p, PARAMETERS[p][0]) for p in dataset['parameters']]),
        selector('step', 'Шаг', [('instant', PERIODS['instant']), *[(key, name) for key, name in PERIODS.items() if key != 'instant']]
                 if dataset['source'] == 'stream' else [(key, name) for key, name in PERIODS.items() if key != 'instant']),
    ]
    internal = os.getenv('METEO_GRAFANA_APP_INTERNAL', 'http://app:8000')
    web = os.getenv('METEO_GRAFANA_APP_PUBLIC', 'http://localhost:8000')
    selectors = 'station=${station:percentencode}&parameter=${parameter:percentencode}&step=${step:percentencode}'
    dates = '&start=${__from:date:YYYY-MM-DD}&end=${__to:date:YYYY-MM-DD}'
    base_url = internal + '/api/grafana/' + version + '?' + selectors + dates
    target = {
        'datasource': DATASOURCE, 'refId': 'A', 'type': 'json', 'source': 'url', 'format': 'table',
        'parser': 'backend', 'url': base_url, 'root_selector': 'points',
        'url_options': {'method': 'GET'},
        'columns': [{'selector': 'time', 'text': 'Дата', 'type': 'timestamp_epoch_ms'},
                    {'selector': 'value', 'text': 'Значение', 'type': 'number'},
                    {'selector': 'coverage_pct', 'text': 'Покрытие, %', 'type': 'number'}],
    }
    links = [{'title': 'Разобрать пропуски в приложении', 'type': 'link',
              'url': web + '/?dataset=' + version + '&' + selectors + dates, 'targetBlank': True}]
    data_link = {'title': 'Исходные наблюдения', 'targetBlank': True, 'url': links[0]['url'] +
                 '&day=${__value.time:date:YYYY-MM-DD}'}
    panel = {'id': 1, 'title': 'Наблюдения и покрытие', 'type': 'timeseries', 'datasource': DATASOURCE,
             'gridPos': {'h': 15, 'w': 24, 'x': 0, 'y': 0}, 'targets': [target],
             'fieldConfig': {'defaults': {'custom': {'spanNulls': False}, 'links': [data_link]}, 'overrides': [
                 {'matcher': {'id': 'byName', 'options': 'Покрытие, %'}, 'properties': [
                     {'id': 'unit', 'value': 'percent'}, {'id': 'custom.axisPlacement', 'value': 'right'}]}
             ]}}
    latest = dataset['stations'][0]['end'][:10]
    first = dataset['stations'][0]['start'][:10]
    return {'uid': uid, 'title': dataset['name'], 'tags': ['AnalyticsHack'], 'schemaVersion': 39,
            'timezone': 'utc', 'editable': False, 'refresh': '30s', 'links': links,
            'time': {'from': first + 'T00:00:00Z', 'to': latest + 'T23:59:59Z'},
            'templating': {'list': variables}, 'panels': [panel]}


def provision(dataset):
    target = os.getenv('METEO_GRAFANA_INTERNAL')
    public = os.getenv('METEO_GRAFANA_PUBLIC')
    token = os.getenv('METEO_GRAFANA_WRITE_TOKEN')
    username = os.getenv('METEO_GRAFANA_WRITE_USER')
    password = os.getenv('METEO_GRAFANA_WRITE_PASSWORD')
    if not target or not public or not (token or username and password):
        return None
    if not dataset['stations'] or not dataset['parameters']:
        return None
    auth = 'Bearer ' + token if token else 'Basic ' + b64encode(f'{username}:{password}'.encode()).decode()
    data = json.dumps({'dashboard': dashboard(dataset), 'overwrite': True}).encode()
    request = Request(target.rstrip('/') + '/api/dashboards/db', data=data,
                      headers={'Authorization': auth, 'Content-Type': 'application/json'}, method='POST')
    try:
        with urlopen(request, timeout=4) as response:
            result = json.load(response)
    except (ValueError, OSError):
        return None
    route = result.get('url')
    return public.rstrip('/') + route if isinstance(route, str) and route.startswith('/d/') else None


def dashboard_url(url, station, parameter, start, end, step):
    return url + '?' + urlencode({'var-station': station, 'var-parameter': parameter, 'var-step': step,
                                  'from': start.isoformat() + 'T00:00:00Z', 'to': end.isoformat() + 'T23:59:59Z'})
