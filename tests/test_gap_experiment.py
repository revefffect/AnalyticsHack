import csv
import io
import unittest
from datetime import datetime
from pathlib import Path

from tools.gap_experiment import (
    Background, HOUR, Score, evaluate, experiment, hourly_readings, masked_blocks, predictions,
    research_export, train,
)


class GapExperimentTests(unittest.TestCase):
    def test_archived_benchmark(self):
        archive = Path(__file__).resolve().parents[1] / "data/raw/meteo_hack_dataset.zip"
        result = experiment(archive)
        self.assertEqual(result["archive_sha256"], "8c574c518617aea0f301997c62dfe75dc09b059abca1cdbd268645a3cd6cc33d")
        kr1 = result["stations"]["kr1"]["test"]
        self.assertEqual(kr1["blocks"], 160)
        day = next(row for row in kr1["scores"] if (row["season"], row["gap_hours"], row["method"]) ==
                   ("all", 24, "neighbor_bias"))
        self.assertEqual((day["filled"], day["points"]), (567, 768))
        self.assertAlmostEqual(day["mae_c"], 1.364, places=3)

    def test_hour_mean_uses_zero_and_excludes_only_relevant_flags(self):
        def reading(stamp, value, flag=""):
            return {"datetime": "2025-04-01 " + stamp, "t_air": value, "qc_flag": flag}
        hours = hourly_readings([reading("07:58", "0", "rh:range"), reading("07:59", "4"),
                                 reading("07:21", "-80", "dup_conflict"), reading("08:04", ""),
                                 reading("08:14", "20", "t_air:range")])
        self.assertEqual(hours, {datetime(2025, 4, 1, 7): 2})

    def test_hourly_profile_and_neighbor_bias_fit_only_earlier_years(self):
        target = {datetime(2023, 1, n, 8): 2.0 for n in range(1, 12)}
        neighbor = {t: 1.0 for t in target}
        target[datetime(2024, 1, 1, 8)] = 98
        neighbor[datetime(2024, 1, 1, 8)] = 100
        model = train(target, neighbor, 2024)
        self.assertEqual(model.profile["DJF", 8], 2)
        self.assertEqual(model.corrections["DJF", 8], 1)
        self.assertNotIn(("DJF", 9), model.profile)

    def test_masks_reproducible_disjoint_known_contiguous_hours(self):
        base = datetime(2025, 1, 1)
        available = {base + i * HOUR for i in range(1200) if i % 199}
        blocks = masked_blocks(available, 42, 2)
        self.assertEqual(blocks, masked_blocks(reversed(sorted(available)), 42, 2))
        self.assertEqual({length for _, length in blocks}, {1, 3, 12, 24, 72})
        covered = set()
        for start, length in blocks:
            stamps = {start + i * HOUR for i in range(length)}
            self.assertTrue(stamps <= available)
            self.assertTrue(stamps.isdisjoint(covered))
            covered.update(stamps)
        for start, length in blocks:
            self.assertNotIn(start - HOUR, covered)
            self.assertNotIn(start + length * HOUR, covered)

    def test_hidden_truth_never_used_and_forecast_has_no_right_boundary(self):
        start = datetime(2025, 1, 1, 8)
        target = {start - HOUR: 0, start: -50, start + HOUR: -100, start + 2 * HOUR: 9}
        background = Background({}, {("DJF", 8): 2}, {})
        def run():
            return [v for _, v in predictions(target, {start: 18}, background, start, 2)]
        initial = run()
        self.assertEqual(initial[0]["linear"], 3)
        self.assertEqual(initial[1]["linear"], 6)
        self.assertEqual(initial[0]["neighbor_bias"], 20)
        self.assertIsNone(initial[1]["neighbor_bias"])
        target[start] = 99
        self.assertEqual(initial, run())
        del target[start + 2 * HOUR]
        self.assertEqual(run()[1]["forecast_last"], 0)
        self.assertIsNone(run()[1]["linear"])
        scores, forecast = evaluate(target, {}, background, [(start, 2)])
        self.assertEqual(scores["all", 2, "linear"].metrics()["coverage"], 0)
        self.assertEqual(forecast[2, "forecast_last"].metrics()["mae_c"], 100)

    def test_research_export_is_separate_and_abstains_without_calibration(self):
        first = datetime(2025, 1, 1)
        target = {first: 0, first + 2 * HOUR: 4, first + 5 * HOUR: 10}
        cal = Score(points=7, filled=7, absolute=1.4)
        stream = io.StringIO()
        research_export(csv.writer(stream), "kr1", target, {}, Background({}, {}, {}),
                        {("DJF", 1, "linear"): cal}, {("kr1", 2025): "kr1.csv"}, "digest")
        rows = list(csv.reader(io.StringIO(stream.getvalue())))
        self.assertEqual([r[6] for r in rows], ["observed_hour_mean", "estimate", "observed_hour_mean",
                                                 "missing", "missing", "observed_hour_mean"])
        self.assertEqual(rows[1][7:10], ["", "2.0", "linear"])
        self.assertAlmostEqual(float(rows[1][-1]), 0.2)
        self.assertEqual(rows[0][7:10], ["0", "", ""])


if __name__ == "__main__":
    unittest.main()
