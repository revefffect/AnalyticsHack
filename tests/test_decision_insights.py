from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from meteo.analytics import slice_data
from meteo.decision_insights import decision_insights
from meteo.importer import import_upload
from meteo.store import Store


def daily(values, parameter='t_air', source='station'):
    start = date(2024, 1, 1)
    points = [{'start': str(start + timedelta(days=i)), 'value': value, 'eligible': value is not None}
              for i, value in enumerate(values)]
    valid = sum(point['eligible'] for point in points)
    return {'points': points, 'parameter': parameter, 'station': 'KR1', 'source': source,
            'start': str(start), 'end': points[-1]['start'], 'step': 'day', 'unit': '°C',
            'coverage': {'days': len(points), 'periods': len(points), 'valid_days': valid, 'valid_periods': valid}}


class DecisionInsightsTests(unittest.TestCase):
    def test_model_selection_and_independent_future_holdout(self):
        result = decision_insights(daily(list(range(15))))['automl']
        self.assertEqual(result['selected'], 'linear_3')
        self.assertEqual(result['forecast']['date'], '2024-01-16')
        self.assertEqual(result['forecast']['value'], 15)
        self.assertEqual({row['n'] for row in result['candidates']}, {4})
        self.assertEqual({row['selection_n'] for row in result['candidates']}, {8})
        self.assertLess(result['selection_end'], result['holdout_start'])
        changed_holdout = decision_insights(daily(list(range(11)) + [-20] * 4))['automl']
        self.assertEqual(changed_holdout['selected'], result['selected'])
        for a, b in zip(result['candidates'], changed_holdout['candidates']):
            self.assertEqual(a['selection_mae'], b['selection_mae'])
        winner = next(row for row in changed_holdout['candidates'] if row['method'] == 'linear_3')
        self.assertGreater(winner['mae'], 0)

    def test_abstains_across_calendar_gaps_and_for_external_forecasts(self):
        self.assertIsNone(decision_insights(daily(list(range(15)) + [None]))['automl']['forecast'])
        scattered = daily([8, 9, None] * 8)
        self.assertEqual(decision_insights(scattered)['automl']['candidates'], [])
        self.assertEqual(decision_insights(daily([5] * 15, source='forecast'))['automl']['status'], 'abstained')
        wind = decision_insights(daily([359, 1] * 8, parameter='wind_dir'))
        self.assertIsNone(wind['distribution']['median'])
        self.assertIsNone(wind['automl']['forecast'])

    def test_anomaly_requires_prior_variability_and_preserves_original_points(self):
        snapshot = daily([9, 10, 11, 9, 10, 11, 9, 35, None])
        snapshot['points'][-1].update(value=95, eligible=False)
        result = decision_insights(snapshot)
        self.assertEqual(result['distribution']['count'], 8)
        self.assertEqual(result['distribution']['max'], 35)
        self.assertEqual([point['date'] for point in result['anomalies']], ['2024-01-08'])
        self.assertIn('MAD', result['anomalies'][0]['explanation'])
        self.assertEqual(snapshot['points'][7]['value'], 35)

    def test_real_april_archive_monthly_qc_and_daily_forecast(self):
        archive = Path(__file__).resolve().parents[1] / 'data/raw/meteo_hack_dataset.zip'
        with TemporaryDirectory() as directory, ZipFile(archive) as zipped:
            store = Store(directory)
            versions = {}
            for station in ('KR1', 'KR2'):
                filename = f'{station.lower()}_2024.csv'
                rows = zipped.read('meteo_clgz_hackathon/data/sokol_m1/' + filename).splitlines()
                april = b'\n'.join([rows[0], *(r for r in rows[1:] if r.startswith(b'2024-04-'))])
                digest = store.save_blob('expert', filename, april)
                versions[station] = import_upload(store, 'expert', digest)['id']
            with store.connect() as db:
                sites = {key: db.execute('SELECT * FROM datasets WHERE id=?', (version,)).fetchone()
                         for key, version in versions.items()}
                period = (date(2024, 4, 1), date(2024, 4, 30))
                pressure = decision_insights(slice_data(db, sites['KR1'], 'KR1', 'pressure_hpa', *period, 'month'))
                air1 = decision_insights(slice_data(db, sites['KR1'], 'KR1', 't_air', *period, 'day'))
                air2 = decision_insights(slice_data(db, sites['KR2'], 'KR2', 't_air', *period, 'day'))
        self.assertEqual(pressure['coverage']['valid_days'], 2)
        self.assertEqual(pressure['distribution']['count'], 0)
        self.assertIsNone(pressure['automl']['forecast'])
        self.assertEqual(air1['coverage']['valid_days'], 28)
        self.assertIsNone(air1['automl']['forecast'])
        self.assertEqual(air2['automl']['forecast']['date'], '2024-05-01')
        self.assertEqual(air2['automl']['selected'], 'persistence')
        self.assertAlmostEqual(air2['automl']['forecast']['value'], 15.70, places=2)
        self.assertEqual(air2['automl']['candidates'][0]['n'], 9)
        self.assertAlmostEqual(air2['automl']['candidates'][0]['mae'], 2.84, places=2)
