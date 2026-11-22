import os
import unittest
from io import BytesIO

import pandas as pd

from eda.core import MeteoArchive, analyze, compare_temperature, parse_observations, reference_coverage
from scripts.sync_influx_demo import kr1_temperature_points


class CoverageRulesTests(unittest.TestCase):
    def test_hours_gate_but_observation_mean_uses_every_reading(self):
        rows = [f"2024-02-01 {hour:02}:00,10,1000," for hour in range(18)]
        rows.extend(["2024-02-01 00:30,100,900,pressure_hpa:range", "2024-02-01 18:00,30,950,dup_conflict"])
        csv = BytesIO(("datetime,t_air,pressure_hpa,qc_flag\n" + "\n".join(rows)).encode())
        station = parse_observations(csv, "kr1_test.csv")
        archive = MeteoArchive({"kr1": station}, None)
        _, days, _, _ = analyze(archive, "kr1", "t_air", "2024-02-01", "2024-02-01")
        day = days.iloc[0]
        self.assertEqual(day.metric_slots, 18)
        self.assertEqual(day.valid_readings, 19)
        self.assertAlmostEqual(day["mean"], 280 / 19)

    def test_new_station_marks_conflicting_times_and_collapses_exact_repeats(self):
        archive = MeteoArchive({}, None)
        data = b"datetime,t_air\n2024-02-01 01:00,1\n2024-02-01 01:00,1\n2024-02-01 01:00,2\n"
        self.assertEqual(archive.add_station("upload.csv", data, "Новая", 18), 2)
        rows, days, _, _ = analyze(archive, "extra", "t_air", "2024-02-01", "2024-02-01")
        self.assertTrue(rows["conflict"].all())
        self.assertEqual(days.iloc[0].metric_slots, 0)

    def test_short_date_filter_evaluates_full_month_on_leap_calendar(self):
        empty = parse_observations(BytesIO(b"datetime,t_air\n2024-02-01 01:00,1\n"), "kr1_test.csv")
        _, days, months, _ = analyze(MeteoArchive({"kr1": empty}, None), "kr1", "t_air", "2024-02-03", "2024-02-05")
        self.assertEqual(len(days), 3)
        self.assertEqual(months.iloc[0].days, 29)
        self.assertFalse(months.iloc[0].full_selection)
        self.assertTrue(pd.isna(months.iloc[0]["mean"]))


@unittest.skipUnless(os.getenv("METEO_ARCHIVE"), "Set METEO_ARCHIVE to test the full organizer ZIP")
class ArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = os.environ["METEO_ARCHIVE"]
        cls.archive = MeteoArchive.read(cls.path)

    def test_full_coverage_matches_reference(self):
        checked, differences = reference_coverage(self.archive)
        self.assertEqual(len(checked), 420)
        self.assertTrue(differences.empty)

    def test_april_pressure_rejects_month_and_keeps_good_source_rows(self):
        rows, daily, monthly, _ = analyze(self.archive, "kr1", "pressure_hpa", "2024-04-01", "2024-04-30")
        self.assertEqual((len(rows), rows.valid.sum()), (1389, 117))
        self.assertEqual((daily.station_ok.sum(), daily.metric_ok.sum()), (28, 2))
        self.assertFalse(monthly.iloc[0].eligible)
        self.assertTrue(pd.isna(monthly.iloc[0]["mean"]))

    def test_july_comparison_uses_paired_days_and_hours(self):
        monthly, paired, hourly = compare_temperature(self.archive, 2025, 7)
        self.assertEqual(len(paired), 30)
        self.assertAlmostEqual(monthly.loc["2025-07-01", "mean"], -0.01916265127987948)
        self.assertEqual(hourly.loc[13, "pairs"], 28)
        self.assertAlmostEqual(hourly.loc[13, "delta"], -2.006, places=3)
        self.assertAlmostEqual(hourly.loc[20, "delta"], 1.271, places=3)

    def test_influx_preview_generates_one_point_per_eligible_day(self):
        lines = list(kr1_temperature_points(self.path))
        self.assertEqual(len(lines), 363)
        self.assertEqual(len({line.rsplit(" ", 1)[-1] for line in lines}), 363)
        self.assertTrue(lines[0].startswith("meteo_daily,station=kr1 mean_c="))
        self.assertTrue(lines[0].endswith(" 1704067200"))
