"""Send verified daily means for Kr1 in 2024 to a local InfluxDB 2 instance."""

import argparse
import os
from getpass import getpass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from eda.core import MeteoArchive, analyze


def kr1_temperature_points(archive_path):
    archive = MeteoArchive.read(archive_path)
    _, daily, _, _ = analyze(archive, "kr1", "t_air", "2024-01-01", "2024-12-31")
    valid = daily.loc[daily["metric_ok"], ["mean", "metric_slots", "valid_readings"]]
    for day, mean, hours, readings in valid.itertuples(name=None):
        # A UTC midnight labels the source calendar day of the aggregate.
        day_label = int(day.tz_localize("UTC").timestamp())
        yield f"meteo_daily,station=kr1 mean_c={mean!r},valid_hours={hours}i,readings={readings}i {day_label}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="Path to the organizer's ZIP")
    parser.add_argument("--dry-run", action="store_true", help="Compute and preview without sending data")
    args = parser.parse_args()

    lines = list(kr1_temperature_points(args.archive))
    if not lines:
        parser.exit(1, "Пригодных суточных средних для Кр1 за 2024 год не найдено.\n")
    if args.dry_run:
        print(f"Готово {len(lines)} суточных средних для Кр1. Пример записи")
        print(lines[0])
        return

    token = os.getenv("INFLUX_TOKEN") or getpass("Токен InfluxDB для записи в meteo: ")
    query = urlencode({"org": "zapovednik", "bucket": "meteo", "precision": "s"})
    request = Request(
        f"http://127.0.0.1:8086/api/v2/write?{query}",
        data="\n".join(lines).encode("utf-8"),
        headers={"Authorization": f"Token {token}", "Content-Type": "text/plain; charset=utf-8"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            if response.status != 204:
                parser.exit(1, f"Неожиданный ответ InfluxDB HTTP {response.status}.\n")
    except HTTPError as error:
        parser.exit(1, f"InfluxDB вернул HTTP {error.code}. Проверь токен записи, организацию zapovednik и bucket meteo.\n")
    except URLError:
        parser.exit(1, "InfluxDB недоступен на 127.0.0.1:8086. Проверь Docker Desktop и состояние контейнера.\n")
    print(f"Записано {len(lines)} суточных средних для Кр1 в InfluxDB.")


if __name__ == "__main__":
    main()
