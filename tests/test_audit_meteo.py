import calendar
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from tools.audit_meteo import Day, audit, month_result, valid_month


class MeteoRulesTests(unittest.TestCase):
    def test_month_uses_both_limits_and_leap_year(self):
        days = set(range(1, 30))
        self.assertTrue(valid_month(days - set(range(1, 20, 2)), 2024, 2)[0])
        self.assertFalse(valid_month(days - set(range(1, 22, 2)), 2024, 2)[0])
        self.assertEqual(valid_month(days - set(range(25, 30)), 2024, 2), (False, 5))
        self.assertEqual(valid_month(days - set(range(26, 30)), 2024, 2), (True, 4))

    def test_ame60_requires_72_distinct_original_times_including_off_grid(self):
        day = Day()
        for index in range(71):
            day.record(f'{index // 4:02}:{12 + 15 * (index % 4):02}', 0)
        day.record('00:12', 18)
        self.assertEqual(month_result({(3, 1): day}, 'AME60', 2018, 3)[0]['days'], 0)
        day.record('17:57', 0)
        result, means = month_result({(3, 1): day}, 'AME60', 2018, 3)
        self.assertEqual(result['days'], 1)
        self.assertAlmostEqual(means[1], 18 / 73)

    def test_archive_hour_coverage_and_flags_are_applied_per_parameter(self):
        # An extra sample in hour 00 changes the daily mean but contributes no extra hour.
        rows = ['datetime,t_air,pressure_hpa,qc_flag']
        for d in range(1, 31):
            for h in range(18):
                flag = 'pressure_hpa:range' if d <= 28 else ''
                rows.append(f'2024-04-{d:02} {h:02}:04,0,999,{flag}')
            rows.append(f'2024-04-{d:02} 00:35,19,999,{flag}')
        rows.extend(['2024-04-30 20:00,1000,1000,dup_conflict', '2024-04-30 20:00,900,800,dup_conflict'])
        coverage = ['station,year,month,days_in_month,valid_days,month_valid']
        for m in range(1, 13):
            coverage.append(f'KR1,2024,{m},{calendar.monthrange(2024, m)[1]},{30 if m == 4 else 0},{m == 4}')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'audit.zip'
            with ZipFile(path, 'w') as z:
                z.writestr('archive/data/sokol_m1/kr1_2024.csv', '\n'.join(rows))
                z.writestr('archive/coverage_by_month.csv', '\n'.join(coverage))
            result = audit(path)
        self.assertEqual(result['reference_mismatches'], [])
        self.assertEqual(result['conflicting_timestamps'], 1)
        self.assertEqual(result['flag_counts']['dup_conflict'], 2)
        self.assertEqual(result['kr1_2024']['t_air']['april']['mean'], 1.0)
        pressure = result['kr1_2024']['pressure_hpa']['april']
        self.assertEqual((pressure['days'], pressure['longest_gap'], pressure['mean']), (2, 28, None))


if __name__ == '__main__':
    unittest.main()
