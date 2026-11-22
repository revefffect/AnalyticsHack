from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

import pandas as pd
from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse

from catalog.importing import QC_VERSION, SCHEMA, dataset_frame
from catalog.models import Dataset
from catalog.reporting import period_records, version_context
from eda.core import METRICS, MONTH_MAX_CONSECUTIVE_INCOMPLETE_DAYS, MONTH_MAX_INCOMPLETE_DAYS
from eda.core import MeteoArchive, compare_temperature, longest_gap


def station_periods(dataset):
    for metric in dataset.metrics:
        _, daily, monthly, decades = version_context(dataset, metric, dataset.first_observation, dataset.last_observation)
        for step in ("day", "decade", "month"):
            for record in period_records(daily, monthly, decades, step):
                yield {
                    "dataset_version": str(dataset.version), "station_id": dataset.station_id,
                    "station": dataset.station.name, "location": str(dataset.station.location), "metric": metric,
                    "unit": METRICS[metric][1], "step": step, "mean": record["mean"],
                    "period_start": date.fromisoformat(record["start"]), "period_end": date.fromisoformat(record["end"]),
                    "period_days": record["period_days"], "station_days": record["station_days"],
                    "metric_days": record["metric_days"], "max_gap": record["max_gap"], "state": record["state"],
                    "source_type": dataset.source_kind, "schema_version": dataset.source_meta.get("schema", SCHEMA),
                    "qc_version": dataset.source_meta.get("qc", QC_VERSION),
                    "report_path": reverse("dataset-report", args=(dataset.version,)),
                }


def comparison_frames(left, right, year):
    archive = MeteoArchive({"kr1": dataset_frame(left), "kr2": dataset_frame(right)}, None)
    archive.profiles.update(kr1=left.profile or left.station.profile, kr2=right.profile or right.station.profile)
    months, hours = [], []
    identity = {"left_version": str(left.version), "right_version": str(right.version),
                "left_station": left.station.name, "right_station": right.station.name, "year": year, "unit": "°C"}
    for month in range(1, 13):
        _, days, hourly = compare_temperature(archive, year, month)
        start = pd.Timestamp(year, month, 1)
        calendar = pd.date_range(start, start + pd.offsets.MonthEnd(), freq="D")
        coverage = calendar.isin(days.index)
        eligible = bool(len(days) and len(calendar) - len(days) <= MONTH_MAX_INCOMPLETE_DAYS
                        and longest_gap(coverage) <= MONTH_MAX_CONSECUTIVE_INCOMPLETE_DAYS)
        months.append({**identity, "month_start": start.date(), "eligible": eligible, "pair_count": len(days),
                       "left_c": days.clearing.mean() if eligible else None,
                       "right_c": days.field.mean() if eligible else None,
                       "delta_c": -days.delta.mean() if eligible else None})
        hours.extend({**identity, "month_start": start.date(), "hour": hour, "pair_count": int(row.pairs),
                      "left_c": row.clearing, "right_c": row.field, "delta_c": -row.delta}
                     for hour, row in hourly.dropna(subset=["delta"]).iterrows())
    hour_columns = [*identity, "month_start", "hour", "pair_count", "left_c", "right_c", "delta_c"]
    hourly_data = pd.DataFrame(hours, columns=hour_columns).astype({
        "hour": "int64", "pair_count": "int64", "left_c": "float64", "right_c": "float64", "delta_c": "float64",
    })
    return pd.DataFrame(months), hourly_data


class Command(BaseCommand):
    help = "Create a read-only analytics snapshot from explicitly selected portal datasets."

    def add_arguments(self, parser):
        parser.add_argument("output", type=Path)
        parser.add_argument("--dataset", type=UUID, action="append", required=True, metavar="VERSION")
        parser.add_argument("--compare", type=UUID, nargs=2, metavar=("LEFT_VERSION", "RIGHT_VERSION"))
        parser.add_argument("--year", type=int, default=2025)

    def handle(self, *args, **options):
        try:
            import duckdb
        except ImportError as error:
            raise CommandError("Установите DuckDB в окружение команды: python -m pip install 'duckdb>=1.4,<2'.") from error
        output = options["output"].resolve()
        if output.exists():
            raise CommandError("Файл витрины уже существует. Укажите новый путь.")
        versions = list(dict.fromkeys(options["dataset"]))
        datasets = Dataset.objects.filter(version__in=versions).select_related("station__location").defer("contents", "normalization").in_bulk(field_name="version")
        if datasets.keys() != set(versions):
            raise CommandError("Одной из указанных версий нет в каталоге.")
        compare = options["compare"]
        if compare:
            if len(set(compare)) != 2 or not set(compare).issubset(datasets) or not 1900 <= options["year"] <= 2200:
                raise CommandError("Укажите две разные выгружаемые версии и год от 1900 до 2200.")
            left, right = (datasets[version] for version in compare)
            if left.station_id == right.station_id or any("t_air" not in dataset.metrics for dataset in (left, right)):
                raise CommandError("Для сравнения нужны две разные станции с температурой воздуха.")
        output.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=output.parent) as folder:
            temporary = Path(folder) / "mart.duckdb"
            with duckdb.connect(str(temporary)) as database:
                frame = pd.DataFrame.from_records(
                    record for version in versions for record in station_periods(datasets[version])
                )
                database.register("selected_periods", frame)
                database.execute('CREATE TABLE weather_periods AS SELECT * REPLACE (CAST("mean" AS DOUBLE) AS "mean") FROM selected_periods')
                if compare:
                    monthly, hourly = comparison_frames(left, right, options["year"])
                    database.register("selected_months", monthly)
                    database.register("selected_hours", hourly)
                    database.execute("""CREATE TABLE weather_comparison_months AS SELECT * REPLACE (
                        CAST(left_c AS DOUBLE) AS left_c, CAST(right_c AS DOUBLE) AS right_c,
                        CAST(delta_c AS DOUBLE) AS delta_c) FROM selected_months""")
                    database.execute("CREATE TABLE weather_comparison_hours AS SELECT * FROM selected_hours")
                    database.execute("""
                        CREATE VIEW weather_comparison_year_hours AS
                        SELECT left_version, right_version, left_station, right_station, year, unit, hour,
                               SUM(pair_count)::BIGINT AS pair_count,
                               SUM(delta_c * pair_count) / SUM(pair_count) AS delta_c
                        FROM weather_comparison_hours GROUP BY ALL
                    """)
            try:
                output.hardlink_to(temporary)
            except FileExistsError as error:
                raise CommandError("Файл витрины уже существует. Укажите новый путь.") from error
        self.stdout.write(f"{output} · {len(frame)} агрегатов · {len(datasets)} версий")
