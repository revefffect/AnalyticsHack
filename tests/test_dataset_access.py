from io import BytesIO
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from meteo.api import create_app


ACCOUNTS = {
    'owner': {'role': 'research', 'token': 'test-owner-secret'},
    'colleague': {'role': 'business', 'token': 'test-colleague-secret'},
    'visitor': {'role': 'business', 'token': 'test-visitor-secret'},
    'researcher': {'role': 'research', 'token': 'test-researcher-secret'},
}
SERIES = {'station': 'KR1', 'parameter': 't_air', 'start': '2024-04-01', 'end': '2024-04-01'}


class DatasetAccess(unittest.TestCase):
    def setUp(self):
        root = TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.client = TestClient(create_app(root.name, accounts=ACCOUNTS, demo=False))
        self.addCleanup(self.client.close)
        self.version = self.import_dataset('KR1.csv', b'datetime,t_air\n2024-04-01 01:12,2')
        self.dataset = '/api/datasets/' + self.version
        self.members = self.dataset + '/members'
        self.colleague = self.members + '/colleague'
        self.photo = self.dataset + '/stations/KR1/photo'
        image = BytesIO()
        Image.new('RGB', (8, 6), 'green').save(image, format='PNG')
        self.photo_bytes = image.getvalue()

    def import_dataset(self, filename, content):
        upload = self.request('owner', 'POST', '/api/uploads', params={'filename': filename}, content=content)
        self.assertEqual(upload.status_code, 200, upload.text)
        imported = self.request('owner', 'POST', '/api/imports/' + upload.json()['hash'], json={})
        self.assertEqual(imported.status_code, 200, imported.text)
        return imported.json()['id']

    def request(self, who, method, url, **kwargs):
        headers = kwargs.pop('headers', {})
        if who is not None:
            headers['Authorization'] = 'Bearer ' + ACCOUNTS[who]['token']
        return self.client.request(method, url, headers=headers, **kwargs)

    def set_role(self, role):
        result = self.request('owner', 'PUT', self.colleague, json={'role': role})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json(), {'username': 'colleague', 'role': role})

    def put_photo(self, who):
        return self.request(who, 'PUT', self.photo, content=self.photo_bytes,
                            headers={'Content-Type': 'image/png'})

    def check_access(self, who, *, readable, editable):
        for route in ['/api/catalog', '/api/datasets']:
            result = self.request(who, 'GET', route)
            self.assertEqual(result.status_code, 200, result.text)
            cards = [item for item in result.json() if item['id'] == self.version]
            self.assertEqual(bool(cards), readable, route)
            if cards:
                self.assertEqual(cards[0]['can_edit'], editable)
                self.assertEqual(cards[0]['can_manage_access'], who == 'owner')
        series = self.request(who, 'GET', self.dataset + '/series', params=SERIES)
        self.assertEqual(series.status_code, 200 if readable else 404, series.text)
        if readable:
            self.assertEqual(series.json()['dataset'], self.version)
        stations = self.request(who, 'GET', '/api/stations')
        self.assertEqual(stations.status_code, 200, stations.text)
        linked = [item for station in stations.json() for item in station['datasets']
                  if item['id'] == self.version]
        self.assertEqual(bool(linked), readable)
        if linked:
            self.assertEqual(linked[0]['can_upload_photo'], editable)

    def test_private_grants_downgrade_and_revocation(self):
        self.check_access('owner', readable=True, editable=True)
        self.check_access('colleague', readable=False, editable=False)
        self.set_role('editor')
        self.check_access('colleague', readable=True, editable=True)
        self.check_access('visitor', readable=False, editable=False)
        updated = self.request('colleague', 'PATCH', self.dataset, json={'name': 'Апрель · Кр1'})
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()['name'], 'Апрель · Кр1')
        self.assertEqual(self.put_photo('colleague').status_code, 200)

        self.set_role('reader')
        self.check_access('colleague', readable=True, editable=False)
        self.assertEqual(self.request('colleague', 'GET', self.photo).status_code, 200)
        denied = self.request('colleague', 'PATCH', self.dataset, json={'name': 'Другое имя'})
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(self.put_photo('colleague').status_code, 403)

        revoked = self.request('owner', 'DELETE', self.colleague)
        self.assertEqual(revoked.status_code, 204, revoked.text)
        self.check_access('colleague', readable=False, editable=False)
        self.assertEqual(self.request('colleague', 'GET', self.photo).status_code, 404)
        self.assertEqual(self.put_photo('colleague').status_code, 404)
        self.assertEqual(self.request('colleague', 'PATCH', self.dataset,
                                      json={'name': 'После отзыва'}).status_code, 404)
        catalog = self.request('owner', 'GET', '/api/catalog', params={'q': 'Апрель · Кр1'})
        self.assertEqual([card['id'] for card in catalog.json()], [self.version])

    def test_only_owner_manages_members_and_candidates_expose_only_names(self):
        self.set_role('reader')
        roster = self.request('owner', 'GET', self.members)
        self.assertEqual(roster.status_code, 200, roster.text)
        self.assertEqual(roster.json()['owner'], 'owner')
        self.assertEqual(roster.json()['members'], [{'username': 'colleague', 'role': 'reader'}])
        self.assertCountEqual(roster.json()['candidates'], [name for name in ACCOUNTS if name != 'owner'])
        for account in ACCOUNTS.values():
            self.assertNotIn(account['token'], roster.text)

        def assert_roster_denied(who, status):
            for method, url, options in [('GET', self.members, {}),
                                         ('PUT', self.members + '/visitor', {'json': {'role': 'editor'}}),
                                         ('DELETE', self.colleague, {})]:
                with self.subTest(actor=who, method=method, status=status):
                    result = self.request(who, method, url, **options)
                    self.assertEqual(result.status_code, status, result.text)

        assert_roster_denied(None, 401)
        assert_roster_denied('researcher', 404)
        assert_roster_denied('colleague', 403)
        self.set_role('editor')
        assert_roster_denied('colleague', 403)
        members = self.request('owner', 'GET', self.members).json()['members']
        self.assertEqual(members, [{'username': 'colleague', 'role': 'editor'}])

    def test_editor_grant_applies_only_to_selected_dataset(self):
        other_id = self.import_dataset('april.csv', b'datetime,station,t_air\n2024-04-01 01:12,KR1,5')
        other_dataset = '/api/datasets/' + other_id
        self.assertNotEqual(self.version, other_id)
        self.set_role('editor')
        listed = self.request('colleague', 'GET', '/api/datasets')
        self.assertEqual([item['id'] for item in listed.json()], [self.version])
        series = self.request('colleague', 'GET', other_dataset + '/series', params=SERIES)
        self.assertEqual(series.status_code, 404, series.text)
        renamed = self.request('colleague', 'PATCH', other_dataset, json={'name': 'Второй архив'})
        self.assertEqual(renamed.status_code, 404, renamed.text)

    def test_revocation_removes_private_stream_snapshot_and_personal_report(self):
        with patch.dict('os.environ', METEO_FEED_TOKEN='test-feed', METEO_FEED_OWNER='owner'):
            ingest = self.client.post('/api/stream/ingest', headers={'Authorization': 'Bearer test-feed'},
                                      json={'id': 'first', 'station': 'KR1', 'datetime': '2024-04-01 01:12',
                                            'values': {'t_air': 2}})
        self.assertEqual(ingest.status_code, 200, ingest.text)
        stream = '/api/datasets/' + ingest.json()['dataset']
        member = stream + '/members/colleague'
        granted = self.request('owner', 'PUT', member, json={'role': 'editor'})
        self.assertEqual(granted.status_code, 200, granted.text)
        created = self.request('colleague', 'POST', stream + '/reports', json=SERIES)
        self.assertEqual(created.status_code, 200, created.text)
        report_id = created.json()['id']
        report = '/api/reports/' + report_id
        snapshot = '/api/datasets/' + created.json()['dataset']
        self.assertNotEqual(stream, snapshot)
        self.assertEqual(self.request('colleague', 'GET', report).status_code, 200)
        self.assertEqual([r['id'] for r in self.request('colleague', 'GET', '/api/reports').json()], [report_id])
        self.assertEqual(self.request('colleague', 'GET', snapshot + '/series', params=SERIES).status_code, 200)
        self.assertEqual(self.request('colleague', 'PATCH', snapshot, json={'name': 'Изменение'}).status_code, 403)
        self.assertEqual(self.request('visitor', 'GET', report).status_code, 404)

        self.assertEqual(self.request('owner', 'DELETE', member).status_code, 204)
        self.assertEqual(self.request('colleague', 'GET', report).status_code, 404)
        self.assertEqual(self.request('colleague', 'GET', '/api/reports').json(), [])
        self.assertEqual(self.request('colleague', 'GET', snapshot + '/series', params=SERIES).status_code, 404)

    def test_shared_dataset_is_read_only_for_unassigned_accounts(self):
        shared = self.request('owner', 'PUT', self.dataset + '/visibility', params={'shared': True})
        self.assertEqual(shared.status_code, 200, shared.text)
        for who in ('visitor', 'researcher'):
            with self.subTest(who=who):
                self.check_access(who, readable=True, editable=False)
                self.assertEqual(self.put_photo(who).status_code, 403)
                self.assertEqual(self.request(who, 'PATCH', self.dataset,
                                              json={'name': 'Попытка записи'}).status_code, 403)
                self.assertEqual(self.request(who, 'GET', self.members).status_code, 403)
        self.assertEqual(self.request(None, 'GET', self.dataset + '/series', params=SERIES).status_code, 401)
        self.set_role('editor')
        self.assertEqual(self.put_photo('colleague').status_code, 200)
        self.assertEqual(self.request('owner', 'DELETE', self.colleague).status_code, 204)
        self.check_access('colleague', readable=True, editable=False)
        renamed = self.request('owner', 'PATCH', self.dataset, json={'name': 'Общие наблюдения'})
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertEqual(renamed.json()['name'], 'Общие наблюдения')
