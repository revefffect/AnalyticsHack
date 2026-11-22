from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from meteo.api import create_app
from meteo.importer import import_upload
from meteo.store import SCHEMA, SCHEMA_V2, Store


class StationAtlas(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = Store(temporary.name)
        accounts = {'owner': {'token': 'edit', 'role': 'research'},
                    'reader': {'token': 'view', 'role': 'business'}}
        self.client = TestClient(create_app(temporary.name, demo=False, accounts=accounts))
        self.addCleanup(self.client.close)
        self.owner = {'Authorization': 'Bearer edit'}
        self.reader = {'Authorization': 'Bearer view'}

    def dataset(self, name, body, source='station'):
        content = 'datetime,station,t_air\n' + body
        digest = self.store.save_blob('owner', name + '.csv', content.encode())
        return import_upload(self.store, 'owner', digest, name=name, source=source)['id']

    def test_station_ranges_and_permissions_follow_visible_versions(self):
        shared = self.dataset('Архив', '2024-04-01 05:15,KR1,7\n2024-04-02 06:15,KR1,8\n'
                                     '2024-05-01 08:30,KR2,11')
        private = self.dataset('Дополнение', '2025-03-01 00:00,KR1,2\n2025-03-01 01:00,EXTRA,1')
        route = '/api/datasets/' + shared + '/visibility'
        self.assertEqual(self.client.get('/api/stations').status_code, 401)
        self.assertEqual(self.client.get('/api/stations', headers=self.reader).json(), [])
        response = self.client.put(route, params={'shared': True}, headers=self.owner)
        self.assertEqual(response.status_code, 200, response.text)

        cards = self.client.get('/api/stations', headers=self.reader).json()
        self.assertEqual([card['id'] for card in cards], ['KR1', 'KR2'])
        clearing, field = cards
        self.assertEqual(clearing['datasets'], [{'id': shared, 'name': 'Архив', 'source': 'station',
                         'start': '2024-04-01 05:15:00', 'end': '2024-04-02 06:15:00', 'rows': 2,
                         'can_upload_photo': False, 'photo_upload_url': None, 'photo_url': None, 'photo_credit': None}])
        self.assertEqual(field['datasets'][0]['rows'], 1)
        self.assertEqual(clearing['location_kind'], 'area')
        for key in ('latitude', 'longitude', 'photo_url', 'photo_credit'):
            self.assertIsNone(clearing[key])

        own_cards = {card['id']: card for card in self.client.get('/api/stations', headers=self.owner).json()}
        self.assertEqual({dataset['id'] for dataset in own_cards['KR1']['datasets']}, {shared, private})
        self.assertTrue(all(dataset['can_upload_photo'] for dataset in own_cards['KR1']['datasets']))
        self.assertEqual(own_cards['EXTRA']['location_kind'], 'unknown')
        self.assertEqual(own_cards['EXTRA']['name'], 'EXTRA')
        self.assertIsNone(own_cards['EXTRA']['latitude'])
        self.assertEqual(self.client.put(route, params={'shared': False}, headers=self.owner).status_code, 200)
        self.assertEqual(self.client.get('/api/stations', headers=self.reader).json(), [])

    def test_groups_stream_with_observed_station_and_excludes_grids_and_snapshots(self):
        record = '2024-06-01 04:00,AME60,7'
        self.dataset('Архив', record)
        self.dataset('Поток', record, 'stream')
        self.dataset('Версия потока', record, 'stream_snapshot')
        self.dataset('Модель', '2024-06-01 04:00,GRID-001,8', 'forecast')
        self.dataset('Реанализ', record, 'reanalysis')
        cards = self.client.get('/api/stations', headers=self.owner).json()
        self.assertEqual([card['id'] for card in cards], ['AME60'])
        self.assertEqual({item['source'] for item in cards[0]['datasets']}, {'station', 'stream'})

    def test_reference_coordinates_and_publications_are_separate_from_imports(self):
        self.dataset('Импорт', '2024-06-01 04:00,RU-FYO,7')
        api = '/api/stations/references'
        self.assertEqual(self.client.get(api).status_code, 401)
        refs = {r['code']: r for r in self.client.get(api, headers=self.reader).json()}
        self.assertEqual(set(refs), {'RU-Fyo', 'RU-Fy2'})
        self.assertEqual((refs['RU-Fyo']['latitude'], refs['RU-Fyo']['longitude']), (56.4615, 32.9221))
        self.assertEqual((refs['RU-Fy2']['latitude'], refs['RU-Fy2']['longitude']), (56.4476, 32.9019))
        self.assertEqual([p['period'] for p in refs['RU-Fyo']['publications']], ['1998–2014', '1998–2018', '1998–2025'])
        self.assertEqual([p['period'] for p in refs['RU-Fy2']['publications']], ['2015–2018', '2015–2025'])
        for code, ref in refs.items():
            self.assertEqual(ref['id'], 'reference:' + code)
            self.assertEqual(ref['datasets'], [])
            self.assertEqual(ref['location_kind'], 'reference')
            self.assertEqual(ref['source_url'], 'https://fluxnet.org/sites/siteinfo/' + code)
        imported = self.client.get('/api/stations', headers=self.owner).json()
        self.assertEqual([card['id'] for card in imported], ['RU-FYO'])
        self.assertIsNone(imported[0]['latitude'])


class StationAtlasMigration(unittest.TestCase):
    def test_concurrent_upgrade_from_v1_preserves_data_and_future_version(self):
        with TemporaryDirectory() as root:
            database = Path(root) / 'meteo.sqlite3'
            db = sqlite3.connect(database)
            try:
                db.executescript(SCHEMA)
                db.execute('PRAGMA user_version=1')
                db.execute("INSERT INTO datasets(id,owner,name,source) VALUES('legacy','user','v1','station')")
                db.commit()
                self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='station_photos'").fetchone())
            finally:
                db.close()

            start = Barrier(4)

            def open_store(_):
                start.wait()
                return Store(root)

            with ThreadPoolExecutor(max_workers=4) as workers:
                copies = list(workers.map(open_store, range(4)))
            with copies[0].connect(write=True) as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 4)
                self.assertEqual(db.execute('SELECT name FROM datasets WHERE id=?', ('legacy',)).fetchone()[0], 'v1')
                db.execute("INSERT INTO station_photos VALUES ('legacy','KR1','','image/jpeg',X'01')")
                db.execute("INSERT INTO dataset_members VALUES ('legacy','reader1','reader')")
                db.execute('PRAGMA user_version=5')
            with Store(root).connect() as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 5)
                self.assertEqual(db.execute('SELECT role FROM dataset_members').fetchone()[0], 'reader')
                self.assertEqual(db.execute('SELECT image FROM station_photos').fetchone()[0], b'\x01')

    def test_upgrade_v2_keeps_photos_observations_and_visibility(self):
        with TemporaryDirectory() as root:
            db = sqlite3.connect(Path(root) / 'meteo.sqlite3')
            try:
                db.executescript(SCHEMA)
                db.execute(SCHEMA_V2)
                db.execute('PRAGMA user_version=2')
                db.execute("""INSERT INTO datasets (id,owner,name,source,visibility)
                              VALUES ('archive','owner','Архив','station','private')""")
                db.execute("""INSERT INTO observations (dataset,station,stamp,original_stamp,zone,
                                  source_file,source_row,values_json,flags,signature)
                              VALUES ('archive','KR1','2024-02-02','2024-02-02','unknown',
                                      'a.csv',2,'{}','[]','fixture')""")
                db.execute("""INSERT INTO station_photos VALUES
                              ('archive','KR1','Автор','image/png',X'0102')""")
                db.commit()
            finally:
                db.close()
            with Store(root).connect(write=True) as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 4)
                self.assertEqual(tuple(db.execute('SELECT owner,visibility FROM datasets').fetchone()), ('owner', 'private'))
                self.assertEqual(tuple(db.execute('SELECT station,source_row FROM observations').fetchone()), ('KR1', 2))
                self.assertEqual(tuple(db.execute('SELECT credit,hex(image) FROM station_photos').fetchone()), ('Автор', '0102'))
                self.assertEqual(db.execute('SELECT COUNT(*) FROM dataset_members').fetchone()[0], 0)
                db.execute("INSERT INTO dataset_members VALUES ('archive','collaborator','editor')")
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute("INSERT INTO dataset_members VALUES ('archive','collaborator','reader')")
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute("INSERT INTO dataset_members VALUES ('archive','other','owner')")
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute("INSERT INTO dataset_members VALUES ('absent','other','reader')")
                db.execute("DELETE FROM observations WHERE dataset='archive'")
                db.execute("DELETE FROM datasets WHERE id='archive'")
                self.assertEqual(db.execute('SELECT COUNT(*) FROM dataset_members').fetchone()[0], 0)

    def test_concurrent_first_start_creates_all_schema_versions(self):
        with TemporaryDirectory() as root:
            start = Barrier(6)

            def initialize():
                start.wait()
                with Store(root).connect() as db:
                    version = db.execute('PRAGMA user_version').fetchone()[0]
                    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    return version, tables

            with ThreadPoolExecutor(max_workers=6) as workers:
                results = [workers.submit(initialize) for _ in range(6)]
                for result in results:
                    version, tables = result.result()
                    self.assertEqual(version, 4)
                    self.assertTrue({'datasets', 'station_photos', 'dataset_members', 'expert_annotations'} <= tables)

    def test_failed_upgrade_rolls_back_earlier_schema_changes(self):
        with TemporaryDirectory() as root:
            database = Path(root) / 'meteo.sqlite3'
            with patch('meteo.store.SCHEMA_V3', 'INVALID SQL'):
                with self.assertRaises(sqlite3.OperationalError):
                    Store(root)
            db = sqlite3.connect(database)
            try:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 0)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0], 0)
            finally:
                db.close()
            with Store(root).connect() as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 4)
