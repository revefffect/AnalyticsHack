import csv
from datetime import timedelta
from functools import lru_cache
from io import StringIO

import numpy as np
import pandas as pd
from django.db.models import Q
from django.http import HttpResponse, JsonResponse

from eda.core import METRICS, analyze, historical_context

from .importing import QC_VERSION, SCHEMA, archive_for, dataset_frame
from .models import Dataset


COLUMNS = {
    "start": "Начало", "end": "Конец", "period_days": "Дней в периоде", "station_days": "Пригодно по станции",
    "metric_days": "Пригодно по показателю", "mean": "Среднее", "state": "Статус", "max_gap": "Неполных дней подряд",
}


def allowed_datasets(user):
    if not user.has_perm("catalog.view_dataset"):
        return Dataset.objects.none()
    if user.is_superuser:
        return Dataset.objects.all()
    return Dataset.objects.filter(Q(uploaded_by=user) | Q(published=True) | Q(readers=user)).distinct()


@lru_cache(maxsize=4)
def _saved_frame(version):
    return dataset_frame(Dataset.objects.get(version=version))


def version_context(dataset, metric, start, end):
    archive, code = archive_for(dataset, _saved_frame(dataset.version))
    rows, daily, months, decades = analyze(archive, code, metric, start, end)
    original = archive.observations[code].loc[rows.index]
    cols = [name for name in ("original_datetime", "source_timezone", f"original_{metric}") if name in original]
    present = original.get(f"present_{metric}")
    if present is not None:
        rows.loc[rows.status.eq("Нет значения") & present.isna(), "status"] = "Колонка отсутствует в исходной таблице"
    return rows.assign(**{name: original[name] for name in cols}), daily, months, decades


def period_records(daily, monthly, ten_day, step):
    if step == "day":
        return [{"start": day.date().isoformat(), "end": day.date().isoformat(), "period_days": 1,
                 "station_days": int(row.station_ok), "metric_days": int(row.metric_ok),
                 "mean": number(row.get("mean")), "state": "Пригодно" if row.metric_ok else "Недостаточно данных",
                 "max_gap": int(not row.metric_ok)} for day, row in daily.iterrows()]
    periods = monthly if step == "month" else ten_day
    return [{"start": row.start.date().isoformat(), "end": row.end.date().isoformat(), "period_days": row.days,
             "station_days": row.station_days, "metric_days": row.metric_days, "mean": number(row.mean),
             "state": "Частичный период" if not row.full_selection else "Пригодно" if row.eligible else "Недостаточно данных",
             "max_gap": row.gap} for row in periods.itertuples(index=False)]


def number(value):
    return None if pd.isna(value) else float(value)


def profile_summary(rows, daily, metric, threshold):
    intervals = rows.datetime.drop_duplicates().diff().dt.total_seconds().div(60).dropna()
    intervals = intervals.loc[intervals.gt(0)]
    usable = rows.loc[rows.valid, ["datetime", "value"]]
    difference = usable.value.diff().abs() if metric == "t_air" else pd.Series(dtype=float)
    jumps = difference.gt(8) & usable.datetime.diff().le(timedelta(hours=2)) if metric == "t_air" else None
    flags = rows.loc[rows.qc_flag.ne(""), "qc_flag"].str.split(";").explode()
    gaps = []
    for day, complete in daily.metric_ok.items():
        if complete:
            continue
        if gaps and day - gaps[-1]["end"] == timedelta(days=1):
            gaps[-1]["end"] = day
            gaps[-1]["length"] += 1
        else:
            gaps.append({"start": day, "end": day, "length": 1})
    gaps.sort(key=lambda item: item["length"], reverse=True)
    return {
        "station_days": int(daily.station_ok.sum()), "metric_days": int(daily.metric_ok.sum()), "days": len(daily),
        "rows": len(rows), "valid_rows": int(rows.valid.sum()), "gap": gaps[0]["length"] if gaps else 0, "expected": threshold,
        "actual": int(daily.metric_slots.sum()), "expected_total": len(daily) * threshold, "conflicts": int(rows.conflict.sum()),
        "missing_cells": int(rows.status.eq("Нет значения").sum()), "missing_columns": int(rows.status.eq("Колонка отсутствует в исходной таблице").sum()),
        "measured_zero": int((rows.valid & rows.value.eq(0)).sum()),
        "intervals": [(float(span), int(count)) for span, count in intervals.value_counts().head(5).items()],
        "flags": [(flag, int(count)) for flag, count in flags.value_counts().items()],
        "jumps": [] if jumps is None else [index for index in jumps.index[jumps][:20]],
        "gap_runs": gaps[:10],
    }


def history(dataset, metric, month):
    archive, code = archive_for(dataset, _saved_frame(dataset.version))
    return historical_context(archive, code, metric, month, dataset.first_observation.year, dataset.last_observation.year)


def metadata(dataset, options):
    return {
        "dataset_version": str(dataset.version), "station_id": dataset.station_id, "station": dataset.station.name,
        "provider": dataset.source_meta.get("provider", dataset.uploaded_by.get_username()), "source_file": dataset.original_name,
        "source_type": dataset.source_kind, "metric": options["metric"], "unit": METRICS[options["metric"]][1],
        "period_from": options["start"], "period_to": options["end"], "step": options["step"],
        "schema": dataset.source_meta.get("schema", SCHEMA), "qc_version": dataset.source_meta.get("qc", QC_VERSION),
        "method": "observed_day_means_v1", "attribution": dataset.source_meta.get("attribution", ""),
    }


def export_result(dataset, options, records, format, *, info=None):
    info = info or metadata(dataset, options)
    if format == "json":
        response = JsonResponse({"metadata": info, "periods": records}, json_dumps_params={"ensure_ascii": False})
    else:
        text = StringIO()
        writer = csv.DictWriter(text, fieldnames=[*info.keys(), *COLUMNS.keys()])
        writer.writeheader()
        for record in records:
            writer.writerow({key: csv_safe(value) for key, value in {**info, **record}.items()})
        response = HttpResponse("\ufeff" + text.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="weather-{dataset.version}.{format}"'
    response["Cache-Control"] = "private, no-store"
    return response


def csv_safe(value):
    return "'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")) else value


def source_records(rows, metric, inspected_day, flags_only=False):
    if inspected_day:
        rows = rows.loc[rows.day.eq(pd.Timestamp(inspected_day))]
    elif flags_only:
        rows = rows.loc[~rows.valid]
    return [{
        "datetime": row.get("original_datetime", row.datetime.isoformat()),
        "source_timezone": row.get("source_timezone") if row.get("source_timezone") and pd.notna(row.get("source_timezone")) else "неизвестен",
        "value": number(row.value), "original": row.get(f"original_{metric}", row.value),
        "status": row.status, "flag": row.qc_flag, "file": row.source, "line": int(row.csv_line),
    } for _, row in rows.head(80).iterrows()]


def direction_distribution(rows):
    valid = rows.loc[rows.valid & rows["value"].between(0, 360), "value"]
    if valid.empty:
        return []
    sectors = ((valid + 11.25) / 22.5).astype(int) % 16
    counts = sectors.value_counts().reindex(range(16), fill_value=0)
    return [(index * 22.5, round(100 * int(count) / len(valid), 2)) for index, count in counts.items()]
