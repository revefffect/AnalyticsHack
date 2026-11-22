"""Product-level regression tests for the analytics console."""

import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

os.environ.setdefault("RESERVE_OFFLINE", "1")

from fastapi.testclient import TestClient

from app.ai import AIClient, local_compose, validate_spec
from app.analytics import analyse, estimate_gap
from app.annotations import AnnotationStore
from app.data import catalog, load_dataset
from app.report import build_pdf
from app.server import app


class ArchiveFacts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = load_dataset()
        cls.cat = catalog(cls.ds)

    def test_archive_control_numbers(self):
        self.assertEqual(self.ds.meta["rows"], 874_560)
        self.assertEqual(self.ds.meta["sha256"], "8c574c518617aea0f301997c62dfe75dc09b059abca1cdbd268645a3cd6cc33d")
        january = self.ds.monthly[
            (self.ds.monthly["station"] == "AME60")
            & (self.ds.monthly["parameter"] == "t_air")
            & (self.ds.monthly["month"] == 1)
            & self.ds.monthly["month_valid"]
        ]
        self.assertEqual(len(january), 13)
        self.assertEqual(round(january["mean"].mean(), 2), -7.57)
        self.assertEqual(round(float(january.loc[january["year"] == 2010, "mean"].iloc[0]), 2), -15.20)

        april = self.ds.monthly[
            (self.ds.monthly["station"] == "KR1")
            & (self.ds.monthly["parameter"] == "t_air")
            & (self.ds.monthly["year"] == 2024)
            & (self.ds.monthly["month"] == 4)
        ].iloc[0]
        self.assertEqual(round(float(april["mean"]), 2), 7.61)
        self.assertEqual(int(april["valid_days"]), 28)

        pressure = self.ds.monthly[
            (self.ds.monthly["station"] == "KR1")
            & (self.ds.monthly["parameter"] == "pressure_hpa")
            & (self.ds.monthly["year"] == 2024)
            & (self.ds.monthly["month"] == 4)
        ].iloc[0]
        self.assertEqual(int(pressure["valid_days"]), 2)
        self.assertEqual(int(pressure["longest_gap"]), 28)
        self.assertFalse(bool(pressure["month_valid"]))

    def test_series_keeps_gaps_and_period_quality(self):
        series = self.ds.series("KR1", "pressure_hpa", date(2024, 1, 1), date(2024, 12, 31), "month")
        april = next(point for point in series["points"] if point["label"] == "04.2024")
        self.assertFalse(april["valid"])
        self.assertIsNone(april["value"])
        analysis = analyse(self.ds, "KR1", "pressure_hpa", date(2024, 1, 1), date(2024, 12, 31))
        self.assertEqual(len(analysis["period_quality"]["excluded_months"]), 1)
        self.assertEqual(analysis["period_quality"]["excluded_months"][0]["month"], 4)

    def test_local_ai_parser_and_validation(self):
        current = {
            "station": "KR1",
            "parameter": "t_air",
            "start": "2024-01-01",
            "end": "2025-12-31",
            "granularity": "month",
            "baseline": True,
            "annotations": True,
            "risk_focus": None,
        }
        parsed = local_compose("Давление Кр1 в апреле 2024 и причины пропуска", current, self.cat)
        self.assertEqual(parsed["spec"]["station"], "KR1")
        self.assertEqual(parsed["spec"]["parameter"], "pressure_hpa")
        self.assertEqual((parsed["spec"]["start"], parsed["spec"]["end"]), ("2024-04-01", "2024-04-30"))

        fire = local_compose("Пожарная опасность на поляне летом 2025", current, self.cat)
        self.assertEqual(fire["spec"]["risk_focus"], "fire")
        self.assertEqual((fire["spec"]["start"], fire["spec"]["end"]), ("2025-06-01", "2025-08-31"))

        fixed = validate_spec(
            {"station": "AME60", "parameter": "pressure_hpa", "start": "1900-01-01", "end": "2099-01-01"},
            current,
            self.cat,
        )
        self.assertEqual(fixed["parameter"], "t_air")
        self.assertEqual(fixed["start"], "1992-07-01")
        self.assertEqual(fixed["end"], "2021-11-06")

    def test_annotation_store_seeds_and_crud(self):
        with tempfile.TemporaryDirectory() as folder:
            store = AnnotationStore(Path(folder) / "annotations.db")
            self.assertEqual(len(store.list()), 3)
            created = store.create(
                {
                    "station": "KR1",
                    "parameter": "t_air",
                    "start_date": "2025-07-01",
                    "end_date": "2025-07-02",
                    "kind": "event",
                    "severity": "warning",
                    "title": "Проверка API",
                    "note": "Тест",
                    "author": "QA",
                }
            )
            self.assertEqual(created["kind_label"], "Событие")
            self.assertTrue(store.delete(created["id"]))
            self.assertFalse(store.delete(created["id"]))

    def test_deepseek_response_is_validated(self):
        client = AIClient()
        client.configure("test-key")
        client._chat = lambda messages, json_mode=True, timeout=35: (
            '{"station":"KR2","parameter":"pressure_hpa","start":"2024-04-01","end":"2024-04-30",'
            '"granularity":"day","baseline":true,"annotations":true,"risk_focus":"flood",'
            '"title":"Кр2 давление","explanation":"Проверка ответа модели."}'
        )
        result = client.compose(
            "покажи давление",
            {
                "station": "KR1",
                "parameter": "t_air",
                "start": "2024-01-01",
                "end": "2025-12-31",
                "granularity": "month",
                "baseline": True,
                "annotations": True,
                "risk_focus": None,
            },
            self.cat,
        )
        self.assertEqual(result["source"], "deepseek")
        self.assertEqual(result["spec"]["station"], "KR2")
        self.assertEqual(result["spec"]["parameter"], "pressure_hpa")
        self.assertEqual(result["spec"]["risk_focus"], "flood")

    def test_gap_estimate_never_masquerades_as_observation(self):
        gap = estimate_gap(self.ds, "KR1", "pressure_hpa", date(2024, 4, 15))
        self.assertIsNone(gap["observed"])
        self.assertIsNotNone(gap["climatology"])
        self.assertIn("оценку", gap["recommendation"])
        observed = estimate_gap(self.ds, "KR1", "t_air", date(2024, 4, 15))
        self.assertIsNotNone(observed["observed"])

    def test_pdf_report_builds(self):
        pdf = build_pdf(
            self.ds,
            "KR1",
            "pressure_hpa",
            date(2024, 1, 1),
            date(2024, 12, 31),
            annotations=[],
            narrative="Проверка отчёта.",
        )
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 20_000)


class ApiSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def test_health_and_series(self):
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["rows"], 874_560)

        response = self.client.get(
            "/api/series",
            params={"station": "KR1", "parameter": "t_air", "start": "2024-01-01", "end": "2024-12-31", "granularity": "month"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(round(body["summary"]["mean"], 2), 6.34)
        self.assertEqual(body["points"][3]["label"], "04.2024")

    def test_local_dashboard_composition(self):
        response = self.client.post(
            "/api/ai/dashboard",
            json={
                "prompt": "Покажи давление Кр1 в апреле 2024",
                "current": {
                    "station": "KR1",
                    "parameter": "t_air",
                    "start": "2024-01-01",
                    "end": "2025-12-31",
                    "granularity": "month",
                    "baseline": True,
                    "annotations": True,
                    "risk_focus": None,
                },
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["source"], "local")
        self.assertEqual(body["spec"]["parameter"], "pressure_hpa")

    def test_annotation_crud_endpoint(self):
        created = self.client.post(
            "/api/annotations",
            json={
                "station": "KR2",
                "parameter": "t_air",
                "start_date": "2025-05-01",
                "kind": "note",
                "severity": "info",
                "title": "API smoke",
                "author": "QA",
            },
        )
        self.assertEqual(created.status_code, 200)
        identifier = created.json()["item"]["id"]
        deleted = self.client.delete(f"/api/annotations/{identifier}")
        self.assertEqual(deleted.status_code, 200)


if __name__ == "__main__":
    unittest.main()
