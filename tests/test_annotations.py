from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient

from meteo.api import create_app
from meteo.importer import import_upload
from meteo.store import Store
from tools.build_station_demo import archive_slice


ACCOUNTS = {name: {'role': role, 'token': 'fixture-' + name} for name, role in (
    ('owner', 'research'), ('expert', 'research'), ('reader', 'research'), ('manager', 'business'),
)}
SLICE = {'station': 'KR1', 'parameter': 't_air', 'start': '2024-04-01', 'end': '2024-04-02'}


class ExpertAnnotations(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = directory.name
        self.store = Store(self.directory)
        archive = archive_slice(['sokol_m1/kr1_2024.csv'], SLICE['start'], SLICE['end'])
        digest = self.store.save_blob('owner', 'archive.zip', archive)
        self.version = import_upload(self.store, 'owner', digest)['id']
        self.dataset = '/api/datasets/' + self.version
        self.endpoint = self.dataset + '/annotations'
        self.marker = {**SLICE, 'label': 'weather_event', 'note': 'Интервал проверен по журналу поляны.'}
        self.client = TestClient(create_app(self.directory, accounts=ACCOUNTS, demo=False))
        self.addCleanup(self.client.close)

    def request(self, name, method, path=None, **kwargs):
        return self.client.request(method, path or self.endpoint,
                                   headers={'Authorization': 'Bearer ' + ACCOUNTS[name]['token']}, **kwargs)

    def grant(self, user, role):
        response = self.request('owner', 'PUT', self.dataset + '/members/' + user, json={'role': role})
        self.assertEqual(response.status_code, 200, response.text)

    def test_archive_marks_are_attributable_shared_and_do_not_change_observations(self):
        before = self.request('owner', 'GET', self.dataset + '/series', params={**SLICE, 'step': 'day'}).json()
        self.assertTrue(before['points'])
        for user, role in [('expert', 'editor'), ('reader', 'reader'), ('manager', 'editor')]:
            self.grant(user, role)
        written = self.request('owner', 'POST', json=self.marker)
        self.assertEqual(written.status_code, 201, written.text)
        self.assertEqual(written.json(), self.request('owner', 'POST', json=self.marker).json())
        self.assertTrue(written.json()['created_at'].endswith('Z'))
        second = self.request('expert', 'POST', json={**self.marker, 'label': 'normal'})
        self.assertEqual(second.status_code, 201, second.text)
        for user in ['reader', 'manager']:
            self.assertEqual(self.request(user, 'POST', json=self.marker).status_code, 403)
            result = self.request(user, 'GET', params={**SLICE, 'start': SLICE['end']}).json()
            self.assertFalse(result['can_annotate'])
            self.assertCountEqual([row['author'] for row in result['items']], ['owner', 'expert'])
        other_slice = self.request('owner', 'GET', params={**SLICE, 'start': '2024-04-03', 'end': '2024-04-04'})
        self.assertEqual(other_slice.json()['items'], [])
        self.assertEqual(before, self.request('owner', 'GET', self.dataset + '/series',
                                              params={**SLICE, 'step': 'day'}).json())
        self.request('owner', 'DELETE', self.dataset + '/members/expert')
        self.assertEqual(self.request('expert', 'GET', params=SLICE).status_code, 404)
        self.assertEqual(self.request('expert', 'POST', json=self.marker).status_code, 404)
        with TestClient(create_app(self.directory, accounts=ACCOUNTS, demo=False)) as restarted:
            response = restarted.get(self.endpoint, params=SLICE, headers={'Authorization': 'Bearer fixture-owner'})
            self.assertEqual(len(response.json()['items']), 2)

    def test_invalid_source_window_and_spoofed_authorship(self):
        for change in [{'station': 'NO-STATION'}, {'parameter': 'pressure_hpa'}, {'start': '2023-01-01'},
                       {'end': '2023-01-01'}, {'note': '  '}, {'author': 'expert'}, {'label': 'unregistered'}]:
            with self.subTest(change=change):
                self.assertEqual(self.request('owner', 'POST', json={**self.marker, **change}).status_code, 422)
        self.assertEqual(self.request('owner', 'GET', params=SLICE).json()['items'], [])

    def test_gap_can_be_marked_between_observations(self):
        raw = b'datetime,t_air\n2024-04-01 12:00,1\n2024-04-03 12:00,2'
        digest = self.store.save_blob('owner', 'KR1.csv', raw)
        version = import_upload(self.store, 'owner', digest)['id']
        path = '/api/datasets/' + version + '/annotations'
        missing = {**self.marker, 'start': '2024-04-02', 'end': '2024-04-02'}
        self.assertEqual(self.request('owner', 'POST', path, json=missing).status_code, 422)
        self.assertEqual(self.request('owner', 'POST', path, json={**missing, 'label': 'gap'}).status_code, 201)
        self.assertEqual(self.request('owner', 'GET', params=SLICE).json()['items'], [])

    def test_v3_upgrade_preserves_source(self):
        with self.store.connect(write=True) as db:
            db.execute('DROP TABLE expert_annotations')
            db.execute('PRAGMA user_version=3')
        with Store(self.directory).connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 4)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM expert_annotations').fetchone()[0], 0)
            actual = db.execute('SELECT COUNT(*) FROM observations WHERE dataset=?', (self.version,)).fetchone()[0]
            self.assertGreater(actual, 0)
