from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from meteo.analytics import day_detail, slice_data
from meteo.importer import import_upload, preview
from meteo.store import Store


ARCHIVE = Path(__file__).resolve().parents[1] / 'data/raw/meteo_hack_dataset.zip'


class PlatformCore(unittest.TestCase):
    def setUp(self):
        root = TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.store = Store(root.name)

    def test_real_archive_reference_and_idempotence(self):
        digest = self.store.save_blob('science', ARCHIVE.name, ARCHIVE.read_bytes())
        first = import_upload(self.store, 'science', digest)
        self.assertEqual(first['row_count'], 874560)
        self.assertEqual(import_upload(self.store, 'science', digest), {**first, 'reused': True})
        with self.store.connect() as db:
            dataset = db.execute('SELECT * FROM datasets WHERE id=?', (first['id'],)).fetchone()
            air = slice_data(db, dataset, 'AME60', 't_air', date(1992, 1, 1), date(2021, 12, 31), 'month')
            jan10, = [r for r in air['points'] if r['start'] == '2010-01-01']
            self.assertAlmostEqual(jan10['value'], -15.20, places=2)
            self.assertAlmostEqual(air['monthly_baseline'][1]['value'], -7.57, places=2)
            self.assertEqual(air['monthly_baseline'][1]['year_count'], 13)
            pressure = slice_data(db, dataset, 'KR1', 'pressure_hpa',
                                  date(2024, 4, 1), date(2024, 4, 30), 'month')['points'][0]
            self.assertEqual((pressure['value'], pressure['valid_days'], pressure['longest_gap']), (None, 2, 28))
            self.assertEqual(pressure['flags']['pressure_hpa:range'], 1272)

    def test_file_formats_ambiguity_and_zero(self):
        ambiguous = b'datetime,timestamp,t_air\n2024-01-01 00:21,2024-01-01 00:01,0\n'
        digest = self.store.save_blob('science', 'kr1.csv', ambiguous)
        self.assertEqual(preview(self.store.upload('science', digest)[1], 'kr1.csv')[0]['ambiguous']['datetime'],
                         ['datetime', 'timestamp'])
        with self.assertRaisesRegex(ValueError, 'неоднозначные'):
            import_upload(self.store, 'science', digest)
        csv = import_upload(self.store, 'science', digest, options={
            'kr1.csv': {'mapping': {'datetime': 'datetime', 't_air': 't_air'}}
        })
        payload = b'[{"datetime":"2024-01-01 00:21","t_air":0,"station":"KR1"}]'
        digest = self.store.save_blob('science', 'measurements.json', payload)
        as_json = import_upload(self.store, 'science', digest)
        with self.store.connect() as db:
            for data in [csv, as_json]:
                dataset = db.execute('SELECT * FROM datasets WHERE id=?', (data['id'],)).fetchone()
                detail = day_detail(db, dataset, 'KR1', 't_air', date(2024, 1, 1))
                self.assertEqual(detail['rows'][0]['value'], 0)
                self.assertEqual(detail['rows'][0]['zone'], 'unknown')
                self.assertEqual(detail['day']['valid_slots'], 1)
