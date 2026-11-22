import sqlite3
from contextlib import closing
from datetime import date
import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from .models import AlertRule, Dataset, Location, Station


CSV = b"datetime,t_air,qc_flag\n2024-04-01 14:15,9.8,\n2024-04-02 14:15,11.1,\n"


@override_settings(
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}},
    WHITENOISE_USE_FINDERS=True,
)
class PortalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_catalog", stdout=StringIO())
        cls.territory = Location.objects.get(kind=Location.Kind.TERRITORY)
        cls.kr1 = Station.objects.get(location=cls.territory, name__contains="Кр1")
        cls.city = Location.objects.create(name="Тестовый город", kind=Location.Kind.CITY)
        cls.city_station = Station.objects.create(name="Городская станция", location=cls.city)
        user_model = get_user_model()
        cls.reader = user_model.objects.create_user("reader")
        cls.reader.groups.add(Group.objects.get(name="Читатели"))
        cls.researcher = user_model.objects.create_user("researcher")
        cls.researcher.groups.add(Group.objects.get(name="Исследователи"))

    def upload(self, payload=CSV, name="наблюдения.csv", *, follow=False):
        return self.client.post(reverse("upload", args=(self.kr1.pk,)), {
            "data_file": SimpleUploadedFile(name, payload, content_type="text/csv"),
        }, follow=follow)

    def confirm(self, url, *, follow=False, qc="qc_flag"):
        return self.client.post(url, {"member": "__all__", "map_datetime": "datetime",
                                      "map_t_air": "t_air", "map_qc_flag": qc},
                                follow=follow)

    def test_catalog_requires_login_and_filters_city_exactly(self):
        self.assertRedirects(self.client.get(reverse("catalog")), f"{reverse('login')}?next=/")
        self.client.force_login(self.reader)
        response = self.client.get(reverse("catalog"), {"place": self.city.pk})
        self.assertContains(response, self.city_station.name)
        self.assertNotContains(response, self.kr1.name)
        response = self.client.get(reverse("catalog"), {"place": self.territory.pk})
        self.assertContains(response, self.kr1.name)
        self.assertNotContains(response, self.city_station.name)

    def test_researcher_can_create_city_and_station(self):
        self.client.force_login(self.researcher)
        result = self.client.post(reverse("add-location"), {"kind": "city", "name": "Новый город", "region": "Регион"})
        self.assertRedirects(result, reverse("catalog"))
        city = Location.objects.get(name="Новый город")
        result = self.client.post(reverse("add-station"), {"location": city.pk, "name": "Станция 4", "profile": 18})
        station = Station.objects.get(name="Станция 4", location=city)
        self.assertRedirects(result, reverse("station", args=(station.pk,)))

    def test_station_map_uses_confirmed_coordinate_pairs_and_catalog_permission(self):
        url = reverse("station-geojson")
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.researcher)
        values = {"location": self.city.pk, "name": "Точка для теста", "profile": 18}
        response = self.client.post(reverse("add-station"), {**values, "latitude": "56.123456"})
        self.assertContains(response, "Укажите широту и долготу вместе")
        self.assertFalse(Station.objects.filter(name=values["name"]).exists())
        self.client.post(reverse("add-station"), {**values, "latitude": "56.123456", "longitude": "32.654321"})
        point = Station.objects.get(name=values["name"])
        self.client.force_login(self.reader)
        response = self.client.get(url, {"place": self.city.pk})
        self.assertEqual(response["Content-Type"], "application/geo+json")
        features = json.loads(response.content)["features"]
        self.assertEqual([feature["id"] for feature in features], [point.pk])
        self.assertEqual(features[0]["geometry"]["coordinates"], [32.654321, 56.123456])
        self.assertEqual(features[0]["properties"]["station_url"], reverse("station", args=(point.pk,)))
        self.assertEqual(json.loads(self.client.get(url, {"place": self.territory.pk}).content)["features"], [])

    def test_report_chart_and_table_use_one_slice_and_link_to_its_source(self):
        self.client.force_login(self.researcher)
        self.confirm(self.upload().url)
        version = Dataset.objects.get().version
        report = reverse("dataset-report", args=(version,))
        options = {"metric": "t_air", "start": "2024-04-01", "end": "2024-04-30", "step": "month"}
        response = self.client.get(report, options)
        self.assertContains(response, "weather-chart-data")
        self.assertContains(response, "Открыть сутки")
        self.assertEqual(response.context["chart_data"]["periods"],
                         self.client.get(report, {**options, "export": "json"}).json()["periods"])
        drilldown = response.context["records"][0]["detail_url"]
        self.assertIn("step=day", drilldown)
        day = self.client.get(drilldown)
        self.assertIn("detail=2024-04-01", day.context["records"][0]["detail_url"])

    def test_researcher_uploads_and_reader_downloads_exact_bytes(self):
        self.client.force_login(self.researcher)
        upload = self.upload()
        response = self.client.get(upload.url)
        self.assertContains(response, "наблюдения.csv")
        self.assertFalse(Dataset.objects.exists())
        self.assertEqual(self.confirm(upload.url).status_code, 302)
        saved = Dataset.objects.get(station=self.kr1)
        self.assertEqual(saved.row_count, 2)
        self.assertEqual(saved.first_observation, date(2024, 4, 1))
        self.assertEqual(saved.last_observation, date(2024, 4, 2))
        self.assertEqual(saved.metrics, ["t_air"])
        self.assertEqual(self.confirm(self.upload(name="копия.csv").url).status_code, 302)
        self.assertEqual(Dataset.objects.count(), 1)
        self.client.force_login(self.reader)
        self.assertEqual(self.client.get(reverse("download", args=(saved.pk,))).status_code, 404)
        saved.readers.add(self.reader)
        result = self.client.get(reverse("download", args=(saved.pk,)))
        self.assertEqual(result.content, CSV)
        self.assertEqual(result["Cache-Control"], "private, no-store")
        self.assertTrue(result["Content-Disposition"].startswith("attachment;"))
        self.client.logout()
        self.assertEqual(self.client.get(reverse("download", args=(saved.pk,))).status_code, 302)

    def test_invalid_csv_and_unauthorized_upload_do_not_write(self):
        self.client.force_login(self.reader)
        self.assertEqual(self.upload().status_code, 403)
        self.client.force_login(self.researcher)
        invalid = self.upload(b"datetime,t_air\nwrong,abc\n")
        self.assertContains(self.confirm(invalid.url, qc=""), "непрочитанные даты")
        self.assertContains(self.upload(name="bad.txt"), "Выбери CSV")
        self.assertFalse(Dataset.objects.exists())

    def test_upload_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.researcher)
        response = client.post(reverse("upload", args=(self.kr1.pk,)), {"data_file": SimpleUploadedFile("a.csv", CSV)})
        self.assertEqual(response.status_code, 403)

    def test_versions_report_permissions_and_exports(self):
        self.client.force_login(self.researcher)
        self.confirm(self.upload().url)
        dataset = Dataset.objects.get()
        report = reverse("dataset-report", args=(dataset.version,))
        self.assertContains(self.client.get(report, {"end": "2024-04-30"}), "Недостаточно данных")
        exported = self.client.get(report, {"end": "2024-04-30", "export": "json"}).json()
        self.assertEqual(exported["metadata"]["dataset_version"], str(dataset.version))
        self.assertEqual(exported["metadata"]["unit"], "°C")
        self.assertIsNone(exported["periods"][0]["mean"])
        self.client.force_login(self.reader)
        self.assertEqual(self.client.get(report).status_code, 404)
        self.assertEqual(self.client.get(reverse("grafana-open", args=(dataset.version,))).status_code, 404)
        dataset.readers.add(self.reader)
        saved = self.client.post(reverse("save-report", args=(dataset.version,)), {"column_order": "mean,start",
                                 "column_labels": "mean=Температура"})
        self.assertContains(self.client.get(saved.url), "Температура")
        dataset.readers.remove(self.reader)
        self.assertEqual(self.client.get(saved.url).status_code, 404)

    def test_report_equivalence_for_csv_xlsx_and_json(self):
        from io import BytesIO
        from openpyxl import Workbook
        from .reporting import version_context

        raw = [{"datetime": f"2024-04-01 {hour:02}:03", "t_air": hour, "qc_flag": ""} for hour in range(18)]
        book = Workbook()
        book.active.append(["datetime", "t_air", "qc_flag"])
        for row in raw:
            book.active.append(list(row.values()))
        xlsx = BytesIO()
        book.save(xlsx)
        payloads = {"data.csv": ("datetime,t_air,qc_flag\n" + "\n".join(f"{r['datetime']},{r['t_air']}," for r in raw)).encode(),
                    "data.xlsx": xlsx.getvalue(), "data.json": json.dumps(raw).encode()}
        self.client.force_login(self.researcher)
        for filename, payload in payloads.items():
            self.assertEqual(self.confirm(self.upload(payload, filename).url).status_code, 302)
        self.assertEqual(Dataset.objects.count(), 3)
        for dataset in Dataset.objects.all():
            rows, daily, _, _ = version_context(dataset, "t_air", "2024-04-01", "2024-04-01")
            self.assertEqual(len(rows), 18)
            self.assertEqual(daily["mean"].iloc[0], 8.5)

    def test_stream_duplicate_late_recovery_and_stale(self):
        from datetime import timedelta
        from .streaming import monitor_stale, register_stream

        stream, token = register_stream(self.kr1, self.researcher, "Replay", Dataset.Source.REPLAY)
        rule = AlertRule.objects.create(stream=stream, recipient=self.reader, metric="t_air", threshold=10,
                                       name="Warm", duration_minutes=15, interval_minutes=15, minimum_coverage=1, stale_minutes=1)
        url = reverse("stream-ingest", args=(stream.key,))

        def push(instant, value, key):
            return self.client.post(url, {"id": key, "datetime": f"2024-07-01T{instant}:00Z", "values": {"t_air": value}},
                                    content_type="application/json", headers={"Authorization": "Bearer " + token})

        self.assertEqual(self.client.post(url, {}, content_type="application/json").status_code, 401)
        self.assertEqual(push("10:00", 15, "1").status_code, 201)
        self.assertEqual(push("10:15", 14, "2").status_code, 201)
        self.assertEqual(push("10:15", 14, "2").json()["status"], "duplicate")
        self.assertEqual(push("10:15", 13, "2").status_code, 409)
        self.assertEqual(push("10:05", 12, "3").json()["status"], "late")
        self.assertEqual(push("10:30", 8, "4").status_code, 201)
        stream.refresh_from_db()
        monitor_stale(stream.latest_receipt + timedelta(minutes=2))
        monitor_stale(stream.latest_receipt + timedelta(minutes=2))
        self.assertEqual(push("10:45", 7, "5").status_code, 201)
        self.assertEqual(list(rule.events.values_list("category", "state")),
                         [("threshold", "triggered"), ("threshold", "recovered"), ("stale", "triggered"), ("stale", "recovered")])
        self.client.force_login(self.reader)
        self.assertContains(self.client.get(reverse("alerts")), "Восстановлено")

    def test_grafana_proxy_denies_forged_header_and_revoked_dataset(self):
        self.client.force_login(self.researcher)
        self.confirm(self.upload().url)
        dataset = Dataset.objects.get()
        dashboard = dataset.dashboard
        dashboard.state, dashboard.org_id = "ready", 42
        dashboard.save()
        dataset.readers.add(self.reader)
        self.client.force_login(self.reader)
        source_url = reverse("grafana-proxy-auth")
        redirect_url = reverse("grafana-open", args=(dataset.version,))
        with patch.dict("os.environ", {"METEO_GRAFANA_PROXY_KEY": "test-proxy"}):
            self.assertEqual(self.client.get(source_url, headers={"X-Webauth-User": "admin"}).status_code, 403)
            with patch("catalog.grafana.enabled", return_value=True), patch("catalog.grafana.reconcile_user",
                    return_value=f"meteo-{self.reader.pk}"):
                response = self.client.get(redirect_url, {"metric": "t_air", "step": "day"})
                self.assertEqual(response.status_code, 302)
                self.assertIn("var-step=day", response.url)
            headers = {"X-Meteo-Proxy-Key": "test-proxy", "X-Webauth-User": "admin"}
            response = self.client.get(source_url, headers=headers)
            self.assertEqual(response.status_code, 204)
            self.assertEqual(response["X-Webauth-User"], f"meteo-{self.reader.pk}")
            dataset.readers.remove(self.reader)
            self.assertEqual(self.client.get(source_url, headers=headers).status_code, 403)
            self.assertEqual(self.client.get(redirect_url).status_code, 404)

    def test_grafana_data_source_has_one_read_only_bucket(self):
        from .grafana import setup_workspace

        uid = "meteo-scope-example"
        bucket = {"id": "only-this-bucket", "orgID": "influx-org"}
        with patch("catalog.grafana.influx_bucket", return_value=bucket), patch.dict("os.environ", {
                "METEO_INFLUX_URL": "http://influx:8086"}), patch("catalog.grafana.influx",
                return_value={"id": "token-id", "token": "scoped"}) as influx, patch("catalog.grafana.grafana",
                side_effect=[{"id": 17}, [{"login": "admin"}], None, {}]) as grafana:
            self.assertEqual(setup_workspace(uid), (bucket, 17))
        self.assertEqual(influx.call_args.args[2]["permissions"], [
            {"action": "read", "resource": {"type": "buckets", "id": "only-this-bucket", "orgID": "influx-org"}}])
        self.assertEqual(grafana.call_args.args[2]["secureJsonData"], {"token": "scoped"})
        self.assertEqual(grafana.call_args.kwargs["org"], 17)

    def test_influx_creates_bucket_when_name_lookup_returns_404(self):
        from .grafana import influx_bucket

        new_bucket = {"id": "new-bucket", "orgID": "org"}
        with patch("catalog.grafana.influx", side_effect=[{"orgs": [{"id": "org"}]}, None, new_bucket]) as influx:
            self.assertEqual(influx_bucket("weather"), new_bucket)
        self.assertTrue(influx.call_args_list[1].kwargs["missing"])
        self.assertEqual(influx.call_args.args[1], "POST")

    def test_assistant_uses_authorized_slice_and_requires_confirmation(self):
        self.client.force_login(self.researcher)
        self.confirm(self.upload(name="private-raw.csv").url)
        dataset = Dataset.objects.get()
        url = reverse("dataset-assistant", args=(dataset.version,))
        query = {"metric": "t_air", "start": "2024-04-01", "end": "2024-04-30", "step": "month"}
        self.assertContains(self.client.get(reverse("dataset-report", args=(dataset.version,)), query),
                            "Открыть в Метеоагенте")
        with (
            patch("catalog.assistant.model_config", return_value=("key", "folder", "example")),
            patch("catalog.assistant.ask_model", return_value="<script>untrusted</script>") as ask,
        ):
            page = self.client.get(url, query)
            self.assertContains(page, "0 из 30")
            self.assertContains(page, "Проверить расчёт")
            self.assertNotContains(page, "private-raw.csv")
            self.client.post(url, {**query, "question": "Объясни"})
            ask.assert_not_called()
            self.client.force_login(self.reader)
            self.assertEqual(self.client.post(url, {**query, "confirm": "on"}).status_code, 404)
            ask.assert_not_called()
            dataset.readers.add(self.reader)
            response = self.client.post(url, {**query, "confirm": "on", "question": "Объясни"})
            self.assertContains(response, "&lt;script&gt;untrusted&lt;/script&gt;")
            args = ask.call_args.args
            self.assertEqual(args[2], "Объясни")
            self.assertEqual(args[1]["latest_periods"][0]["state"], "Недостаточно данных")
            self.assertIsNone(args[1]["latest_periods"][0]["mean"])
            self.assertNotIn("private-raw.csv", json.dumps(args[1]))
            self.assertNotIn(str(dataset.version), json.dumps(args[1]))
            self.assertIn("no-store", response["Cache-Control"])

    def test_assistant_supports_local_preview_and_limit_error(self):
        from .assistant import ModelError

        self.client.force_login(self.researcher)
        self.confirm(self.upload().url)
        dataset = Dataset.objects.get()
        url = reverse("dataset-assistant", args=(dataset.version,))
        with patch("catalog.assistant.model_config", return_value=None), patch("catalog.assistant.ask_model") as ask:
            self.assertContains(self.client.get(url), "Облачная модель ещё не настроена")
            ask.assert_not_called()
        with (
            patch("catalog.assistant.model_config", return_value=("key", "folder", "example")),
            patch("catalog.assistant.ask_model", side_effect=ModelError("Достигнут лимит")),
        ):
            response = self.client.post(url, {"confirm": "on"})
            self.assertContains(response, "Достигнут лимит")
            self.assertContains(response, "Сводка из Метеоархива")


class AssistantModelTests(SimpleTestCase):
    def test_model_request_is_bounded_and_uses_only_output_text(self):
        from io import BytesIO
        from .assistant import AI_ENDPOINT, DEFAULT_MODEL, ask_model

        payload = {"status": "completed", "output": [
            {"type": "reasoning", "content": [{"type": "reasoning_text", "text": "internal"}]},
            {"type": "message", "content": [{"type": "output_text", "text": "Готовый черновик."}]},
        ]}
        with patch("catalog.assistant.build_opener") as opener:
            opener.return_value.open.return_value = BytesIO(json.dumps(payload).encode())
            self.assertEqual(ask_model(("key", "folder", DEFAULT_MODEL), {"metric_days": 2}, "Объясни"),
                             "Готовый черновик.")
        request = opener.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, AI_ENDPOINT)
        self.assertEqual(request.get_header("Authorization"), "Api-Key key")
        self.assertEqual(request.get_header("X-data-logging-enabled"), "false")
        request_payload = json.loads(request.data)
        self.assertFalse(request_payload["store"])
        self.assertEqual(request_payload["model"], f"gpt://folder/{DEFAULT_MODEL}")
        self.assertEqual(json.loads(request_payload["input"][1]["content"])["report"], {"metric_days": 2})


class BackupTests(SimpleTestCase):
    def test_backup_copies_file_bytes_and_preserves_existing_destination(self):
        with TemporaryDirectory() as temp:
            original = Path(temp) / "catalog.sqlite3"
            destination = Path(temp) / "backups" / "copy.sqlite3"
            with closing(sqlite3.connect(original)) as source, source:
                source.execute("CREATE TABLE observations (content BLOB)")
                source.execute("INSERT INTO observations VALUES (?)", (CSV,))

            connection = SimpleNamespace(settings_dict={"NAME": original})
            with patch("catalog.management.commands.backup_catalog.connection", connection):
                call_command("backup_catalog", destination, stdout=StringIO())
                with self.assertRaises(CommandError):
                    call_command("backup_catalog", destination, stdout=StringIO())

            with closing(sqlite3.connect(destination)) as restored:
                self.assertEqual(restored.execute("PRAGMA integrity_check").fetchone(), ("ok",))
                self.assertEqual(restored.execute("SELECT content FROM observations").fetchone(), (CSV,))
