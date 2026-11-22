import json
from pathlib import Path
from time import monotonic
from urllib.request import Request, urlopen
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.conf import settings
from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand, CommandError
from django.test import Client
from django.urls import reverse

from eda.core import MeteoArchive, observation_rows
from catalog.importing import STATION_NAMES
from catalog.models import AlertRule, Dataset, Station
from catalog.streaming import register_stream


class Command(BaseCommand):
    help = "Send a real cooling sequence through the HTTPS API and measure the local alert write."

    def add_arguments(self, parser):
        parser.add_argument("archive", type=Path)
        parser.add_argument("--owner", required=True)
        parser.add_argument("--recipient", required=True)
        parser.add_argument("--url", required=True)
        parser.add_argument("--webhook", action="store_true")
        parser.add_argument("--measure-panel", action="store_true")

    def handle(self, *args, **options):
        users = get_user_model().objects
        owner, recipient = (users.get(username=options[key]) for key in ("owner", "recipient"))
        station = Station.objects.get(name=STATION_NAMES["kr1"], location__name='Урочище «Красное»')
        archive = MeteoArchive.read(options["archive"])
        rows = observation_rows(archive, "kr1", "t_air", "2024-07-01", "2024-07-31")
        clean = rows.loc[rows.valid].drop_duplicates("datetime").reset_index(drop=True)
        selected = None
        for index in range(len(clean) - 2):
            sample = clean.iloc[index:index + 3]
            cadence = sample.datetime.diff().dt.total_seconds().div(60).iloc[1:]
            temps = sample.value.tolist()
            if cadence.nunique() == 1 and 1 <= cadence.iloc[0] <= 30 and min(temps[:2]) - temps[2] >= 0.1:
                selected = sample
                break
        if selected is None:
            raise CommandError("В архиве отсутствует ожидаемая последовательность Кр1.")
        replay_id = uuid4().hex[:6]
        stream, token = register_stream(station, owner, f"Кр1 · воспроизведение {replay_id}", Dataset.Source.REPLAY)
        interval = int(cadence.iloc[0])
        rule = AlertRule.objects.create(stream=stream, recipient=recipient, name="Температура Кр1",
                                       metric="t_air", threshold=round((min(temps[:2]) + temps[2]) / 2, 3),
                                       duration_minutes=interval, interval_minutes=interval, minimum_coverage=1,
                                       stale_minutes=1, webhook=options["webhook"])
        url = options["url"].rstrip("/") + reverse("stream-ingest", args=(stream.key,))
        self.stdout.write(f"{url} · порог {rule.threshold} °C")
        self.stdout.write("Измерения ниже взяты из архива. Неизвестное исходное время показано с тестовым допущением UTC.")
        panel = options["url"].rstrip("/") + reverse("stream-detail", args=(stream.key,))
        cookie = None
        if options["measure_panel"]:
            client = Client()
            client.force_login(recipient)
            cookie = client.cookies[settings.SESSION_COOKIE_NAME]
        try:
            for row in selected.itertuples():
                raw = {"id": str(row.csv_line), "datetime": row.datetime.isoformat(), "assumed_timezone": "UTC",
                       "values": {"t_air": row.value}, "archive": f"{row.source}:{row.csv_line}", "qc_flag": row.qc_flag}
                request = Request(url, json.dumps(raw).encode(), {"Content-Type": "application/json",
                                  "Authorization": f"Bearer {token}"}, method="POST")
                start = monotonic()
                with urlopen(request, timeout=15) as response:
                    result = json.load(response)
                elapsed = (monotonic() - start) * 1000
                self.stdout.write(f"{raw['archive']} · {raw['datetime']} · {row.value} °C · {result['status']} · {elapsed:.1f} мс")
                if cookie:
                    request = Request(panel, headers={"Cookie": f"{cookie.key}={cookie.value}"})
                    with urlopen(request, timeout=15) as response:
                        if b"Plotly.newPlot" not in response.read():
                            raise CommandError("График для получателя отсутствует на странице потока.")
                    self.stdout.write(f"До HTML страницы графика · {(monotonic() - start) * 1000:.1f} мс")
        finally:
            if cookie:
                Session.objects.filter(session_key=cookie.value).delete()
        events = list(rule.events.filter(category="threshold").order_by("pk"))
        if [event.state for event in events] != ["triggered", "recovered"]:
            raise CommandError("Ожидались ровно одно срабатывание и одно восстановление.")
        for event in events:
            related = stream.messages.get(observation=event.observation)
            self.stdout.write(f"{event.state} · до записи события {(event.created - related.received).total_seconds() * 1000:.1f} мс")
        self.stdout.write(reverse("stream-detail", args=(stream.key,)))
