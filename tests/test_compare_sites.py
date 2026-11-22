import csv
import io
import sqlite3
import unittest
from datetime import date
from pathlib import Path
from zipfile import ZipFile

from tools.compare_sites import compare_archive, read_site, summarize, valid_month


ARCHIVE = Path(__file__).resolve().parents[1] / "data/raw/meteo_hack_dataset.zip"


class SiteComparisonTests(unittest.TestCase):
    def test_parameter_flags_and_original_hour(self):
        rows = [
            {"datetime": "2025-01-01 07:58", "t_air": "0", "qc_flag": "rh:range"},
            {"datetime": "2025-01-01 08:02", "t_air": "4", "qc_flag": ""},
            {"datetime": "2025-01-01 08:08", "t_air": "4", "qc_flag": "rh:range;dup_conflict"},
            {"datetime": "2025-01-01 08:10", "t_air": "4", "qc_flag": "wind_dir:fault;t_air:range"},
            {"datetime": "2025-01-01 10:10", "t_air": "", "qc_flag": ""},
        ]
        self.assertEqual(read_site(rows)[date(2025, 1, 1)], {7: [0.0], 8: [4.0]})

    def test_pair_weights_and_incomplete_days(self):
        left = {date(2025, 1, i): {h: [0.0] for h in range(18)} for i in range(1, 32)}
        right = {date(2025, 1, i): {h: [0.0] for h in range(1, 19)} for i in range(1, 32)}
        left[date(2025, 1, 1)][1] = [1.0, 19.0]
        right[date(2025, 1, 2)].pop(18)
        result = summarize(left, right)
        self.assertEqual(result["annual"]["paired_days"], 30)
        self.assertEqual(result["hours"][0]["paired_date_hours"], 0)
        self.assertIsNone(result["hours"][0]["delta_c"])
        self.assertEqual(result["hours"][1]["paired_date_hours"], 30)
        self.assertAlmostEqual(result["months"][0]["delta_c"], 20 / 19 / 30)
        self.assertAlmostEqual(result["hours"][1]["delta_c"], 10 / 30)
        self.assertIsNone(result["months"][1]["delta_c"])

    def test_month_missing_day_boundaries(self):
        self.assertTrue(valid_month([False, True] * 10 + [True] * 10))
        self.assertFalse(valid_month([False, True] * 11 + [True] * 8))
        self.assertTrue(valid_month([True] * 26 + [False] * 4))
        self.assertFalse(valid_month([False] * 5 + [True] * 25))

    @unittest.skipUnless(ARCHIVE.exists(), "Requires the organizer's archive")
    def test_archive_against_independent_sql(self):
        result = compare_archive(ARCHIVE)
        with ZipFile(ARCHIVE) as archive, sqlite3.connect(":memory:") as db:
            db.execute("CREATE TABLE raw(site TEXT, ts TEXT, t REAL, flags TEXT)")
            for site in ("kr1", "kr2"):
                member = f"meteo_clgz_hackathon/data/sokol_m1/{site}_2025.csv"
                with archive.open(member) as raw, io.TextIOWrapper(raw, encoding="utf-8-sig") as file:
                    db.executemany("INSERT INTO raw VALUES (?, ?, ?, ?)", (
                        (site, row["datetime"], row["t_air"] or None, row["qc_flag"])
                        for row in csv.DictReader(file)
                    ))
            db.executescript("""
                CREATE VIEW clean AS SELECT site, substr(ts, 1, 10) AS d,
                    substr(ts, 12, 2) AS h, t FROM raw WHERE t IS NOT NULL
                    AND (';' || flags || ';') NOT LIKE '%;dup_conflict;%'
                    AND (';' || flags || ';') NOT LIKE '%;t_air:%';
                CREATE VIEW days AS SELECT site, d, avg(t) AS t FROM clean
                    GROUP BY site, d HAVING count(DISTINCT h) >= 18;
                CREATE VIEW paired_days AS SELECT l.d, l.t AS a, r.t AS b FROM days l
                    JOIN days r ON l.d = r.d WHERE l.site = 'kr1' AND r.site = 'kr2';
                CREATE VIEW hours AS SELECT site, d, h, avg(t) AS t FROM clean GROUP BY site, d, h;
                CREATE VIEW paired_hours AS SELECT l.d, l.h, l.t AS a, r.t AS b FROM hours l
                    JOIN hours r ON l.d = r.d AND l.h = r.h JOIN paired_days p ON p.d = l.d
                    WHERE l.site = 'kr1' AND r.site = 'kr2';
            """)
            # In this archive all months meet the paired-day coverage rule.
            self.assertTrue(all(row["valid_month"] for row in result["months"]))
            self.assertEqual(result["valid_days"], {"kr1": 365, "kr2": 361})
            self.assertEqual(result["annual"]["paired_days"], 361)
            self.assertAlmostEqual(result["annual"]["delta_c"], -0.190774759873784)
            self.assertEqual(sum(row["paired_date_hours"] for row in result["hours"]), 8498)
            for table, key, col, size in [
                ("paired_days", "substr(d, 6, 2)", "months", "common_valid_days"),
                ("paired_hours", "h", "hours", "paired_date_hours"),
            ]:
                sql = f"SELECT {key}, count(*), avg(a), avg(b), avg(a-b) FROM {table} GROUP BY 1 ORDER BY 1"
                rows = list(db.execute(sql))
                self.assertEqual(len(rows), len(result[col]))
                for expected, actual in zip(rows, result[col]):
                    self.assertEqual(int(expected[0]), actual["month" if col == "months" else "hour"])
                    self.assertEqual(expected[1], actual[size])
                    for value, metric in zip(expected[2:], ["clearing_c", "field_c", "delta_c"]):
                        self.assertAlmostEqual(value, actual[metric], places=10)


if __name__ == "__main__":
    unittest.main()
