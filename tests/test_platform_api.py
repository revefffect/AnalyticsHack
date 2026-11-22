from datetime import timedelta
from io import BytesIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from meteo.api import create_app
from meteo.connectors import WeatherRequest, open_meteo
from meteo.store import Store
from meteo.stream import evaluate, utcnow


RESEARCH = {'Authorization': 'Bearer research-token'}
BUSINESS = {'Authorization': 'Bearer business-token'}
FEED = {'Authorization': 'Bearer feed-token'}
DASH = {'Authorization': 'Bearer dash-token'}
ACCOUNTS = {'research': {'token': 'research-token', 'role': 'research'},
            'business': {'token': 'business-token', 'role': 'business'}}
SLICE = {'station': 'KR1', 'parameter': 't_air', 'start': '2024-04-01', 'end': '2024-04-01', 'step': 'day'}


class PlatformApi(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {'METEO_GRAFANA_READ_TOKEN': 'dash-token', 'METEO_FEED_TOKEN': 'feed-token',
                                       'METEO_FEED_OWNER': 'research'})
        env.start()
        self.addCleanup(env.stop)
        self.client = TestClient(create_app(self.temp.name, accounts=ACCOUNTS, demo=False))
        self.addCleanup(self.client.close)

    def create_dataset(self):
        source = '\n'.join(['datetime,t_air'] + [f'2024-04-01 {h:02}:17,0' for h in range(18)])
        upload = self.client.post('/api/uploads', params={'filename': 'KR1.csv'}, content=source, headers=RESEARCH)
        self.assertEqual(upload.status_code, 200, upload.text)
        applied = self.client.post('/api/imports/' + upload.json()['hash'], json={}, headers=RESEARCH)
        self.assertEqual(applied.status_code, 200, applied.text)
        return applied.json()['id']

    def test_two_roles_and_the_same_value_in_dashboard_report_and_export(self):
        version = self.create_dataset()
        url = '/api/datasets/' + version
        dashboard = '/api/grafana/' + version
        self.assertEqual(self.client.get('/', headers=RESEARCH).status_code, 200)
        self.assertEqual(self.client.get(url + '/series', params=SLICE, headers=BUSINESS).status_code, 404)
        self.assertEqual(self.client.get(url + '/raw', headers=BUSINESS).status_code, 404)
        self.assertEqual(self.client.get(dashboard, params=SLICE, headers=DASH).status_code, 404)
        self.assertEqual(self.client.get(dashboard, params=SLICE, headers=BUSINESS).status_code, 401)
        self.assertEqual(self.client.put(url + '/visibility?shared=true', headers=RESEARCH).status_code, 200)
        series = self.client.get(url + '/series', params=SLICE, headers=BUSINESS).json()
        self.assertEqual(series['points'][0]['value'], 0)
        graph = self.client.get(dashboard, params=SLICE, headers=DASH).json()
        self.assertEqual(graph['points'][0]['value'], 0)
        export = self.client.get(url + '/export', params={**SLICE, 'format': 'json'}, headers=BUSINESS).json()
        self.assertEqual(export['points'], series['points'])
        report = self.client.post(url + '/reports', headers=BUSINESS, json=SLICE)
        self.assertEqual(report.status_code, 200, report.text)
        self.assertEqual(report.json()['points'], series['points'])
        old = '/api/reports/' + report.json()['id']
        self.assertEqual(self.client.get(old, headers=BUSINESS).json()['dataset'], version)
        self.assertEqual(self.client.put(url + '/visibility?shared=false', headers=RESEARCH).status_code, 200)
        self.assertEqual(self.client.get(old, headers=BUSINESS).status_code, 404)

    def test_stream_idempotence_late_recovery_and_stale(self):
        rule = self.client.post('/api/alerts/rules', headers=RESEARCH, json={
            'name': 'Температура', 'station': 'KR1', 'parameter': 't_air', 'threshold': 20, 'minutes': 0,
            'coverage': 1, 'recipient': 'business', 'stale_minutes': 1,
        })
        self.assertEqual(rule.status_code, 200, rule.text)
        self.assertEqual(self.client.get('/api/alerts', headers=BUSINESS).json()['rules'][0]['state'], 'insufficient')

        def post(ident, when, value):
            call = self.client.post('/api/stream/ingest', headers=FEED, json={
                'id': ident, 'station': 'KR1', 'datetime': '2024-04-01 ' + when, 'values': {'t_air': value},
            })
            self.assertEqual(call.status_code, 200, call.text)
            return call.json()

        first = post('first', '01:30', 21)
        self.assertEqual((first['accepted'], first['events']), (1, 1))
        self.assertEqual(post('first', '01:30', 21)['accepted'], 0)
        self.assertEqual(post('late', '01:00', 12)['events'], 0)
        stream = '/api/datasets/stream-reserve'
        report = self.client.post(stream + '/reports', headers=RESEARCH, json=SLICE)
        self.assertEqual(report.status_code, 200, report.text)
        snapshot = report.json()['dataset']
        self.assertNotEqual(snapshot, 'stream-reserve')
        self.assertEqual(post('recovery', '02:00', 13)['events'], 1)
        repeat = self.client.get('/api/reports/' + report.json()['id'], headers=RESEARCH)
        self.assertEqual(repeat.json()['freshness']['measurement'], '2024-04-01 01:30:00')
        with Store(self.temp.name).connect(write=True) as db:
            self.assertEqual(evaluate(db, utcnow() + timedelta(minutes=2)), 1)
        result = self.client.get('/api/alerts', headers=BUSINESS).json()
        self.assertEqual([entry['state'] for entry in result['events']], ['stale', 'recovered', 'active'])
        self.assertIsNone(result['feed'])

    def test_satellite_separates_extent_date_and_providers(self):
        url = '/api/satellite/imagery'
        params = {'area': 'west', 'layer': 'hotspots', 'day': '2024-07-08'}
        result = self.client.get(url, params=params, headers=BUSINESS)
        self.assertEqual(result.status_code, 200, result.text)
        data = result.json()
        wms = parse_qs(urlparse(data['image_url']).query)
        self.assertEqual(wms['BBOX'], ['56.25,32.15,56.58,32.7'])
        self.assertEqual(wms['TIME'], ['2024-07-08'])
        self.assertIn('VIIRS_NOAA20_Thermal_Anomalies_375m_All', wms['LAYERS'][0])
        soil = next(p for p in data['reference_products'] if p.get('product') == 'SPL3SMP_E')
        self.assertIn('bounding_box=32.15%2C56.25%2C32.7%2C56.58', soil['granules'])
        self.assertEqual(self.client.get(url, params={**params, 'area': 'unknown'}, headers=BUSINESS).status_code, 422)

    def test_open_meteo_response_keeps_origin(self):
        fixture = b'{"latitude":56.5,"longitude":33,"hourly":{"time":["2024-04-01T00:00"],"temperature_2m":[0],"relative_humidity_2m":[50],"surface_pressure":[980],"precipitation":[0]}}'
        weather = WeatherRequest(latitude=56.5, longitude=33, start='2024-04-01', end='2024-04-01')
        store = Store(self.temp.name)
        data = open_meteo(store, 'research', weather, fetch=lambda *args, **kwargs: BytesIO(fixture))
        with store.connect() as db:
            row = db.execute('SELECT source,metadata,raw_hash FROM datasets WHERE id=?', (data['id'],)).fetchone()
            self.assertEqual(row['source'], 'reanalysis')
            self.assertIn('ERA5'.lower(), row['metadata'])
            self.assertIn('provider_response', (Path(store.raw) / row['raw_hash']).read_text())
