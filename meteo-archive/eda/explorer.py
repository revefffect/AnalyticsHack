"""Notebook views, source tables and the data exported for defense."""

import numpy as np
import pandas as pd

from .charts import aggregates_figure, coverage_figure, history_figure, measurements_figure
from .charts import temperature_comparison_figure, wind_figure
from .core import METRICS, MONTHS, analyze, compare_temperature, historical_context


def show_inventory(archive):
    for station, table in archive.observations.items():
        earliest = table.datetime.min().date()
        latest = table.datetime.max().date()
        print(f"{archive.labels[station]} · {earliest:%d.%m.%Y} – {latest:%d.%m.%Y} · {len(table):,} записей")


def show_selection(archive, station, metric, start, end, view, detail_day=""):
    from IPython.display import display

    rows, daily, monthly, ten_day = analyze(archive, station, metric, start, end)
    label, unit = METRICS[metric]
    title = f"{archive.labels[station]} · {label}"
    print(f"Пригодные сутки · станция {daily.station_ok.sum()} из {len(daily)} · показатель {daily.metric_ok.sum()} из {len(daily)}")
    print(f"Записи · {len(rows)} всего · {rows.valid.sum()} пригодных по показателю")
    if len(rows) and not rows.valid.all():
        for reason, count in rows.loc[~rows.valid, "status"].value_counts().items():
            print(f"{reason} · {count}")
    if metric == "wind_dir" and view not in ("Замеры и флаги", "Полнота по дням"):
        fig = wind_figure(rows, f"Частоты направлений · {archive.labels[station]}")
        print("Секторы показывают долю пригодных замеров направления без деления по скорости ветра.")
    elif view == "Замеры и флаги":
        fig = measurements_figure(rows, unit, title)
        if len(rows) > 7500:
            print("На длинных интервалах точки могут отображаться с прореживанием. CSV содержит все строки.")
    elif view == "Полнота по дням":
        fig = coverage_figure(daily, archive.profiles[station], title)
    elif view == "Направления по секторам":
        raise ValueError("Выбери направление ветра, чтобы увидеть секторы.")
    else:
        granularity, stat = {
            "Среднее за сутки": ("Сутки", "mean"),
            "Минимум за сутки": ("Сутки", "min"),
            "Максимум за сутки": ("Сутки", "max"),
            "Среднее за декаду": ("Декада", "mean"),
            "Среднее за месяц": ("Месяц", "mean"),
        }[view]
        fig = aggregates_figure(daily, monthly, ten_day, granularity, stat, unit, f"{title} · {view.lower()}")
    fig.show(config={"displaylogo": False, "responsive": True})

    monthly = monthly.assign(
        month=monthly["start"].dt.strftime("%Y-%m"),
        reason=np.select(
            [~monthly["full_selection"], monthly["eligible"], monthly["metric_days"].eq(0)],
            ["Месяц выбран частично", "Проходит", "Нет пригодных суток"],
            default="Месяц не проходит порог полноты",
        ),
    )
    display(monthly[["month", "days", "station_days", "metric_days", "gap", "reason"]].rename(columns={
        "month": "Месяц", "days": "Дней", "station_days": "По станции", "metric_days": "По показателю",
        "gap": "Неполных дней подряд", "reason": "Статус для показателя",
    }).head(36))
    if len(monthly) > 36:
        print(f"Выведено 36 из {len(monthly)} месяцев. Таблица целиком доступна для скачивания.")

    detail = rows.loc[rows["day"].eq(pd.Timestamp(detail_day).normalize())] if detail_day else rows.loc[~rows["valid"]]
    if not detail.empty:
        print("Исходные записи выбранной даты" if detail_day else "Первые исключённые строки")
        display(detail[["datetime", "value", "status", "qc_flag", "source", "csv_line"]].head(25))
    elif detail_day:
        print("На выбранную дату в этом окне записи отсутствуют.")
    return rows, daily, monthly, ten_day


def archive_comparison(archive, month):
    from IPython.display import display

    monthly, paired, hourly = compare_temperature(archive, 2025, month)
    if not paired.empty:
        print(f"{MONTHS[month - 1]} · {len(paired)} общих пригодных дней · средняя разница Кр2 − Кр1 {paired.delta.mean():+.2f} °C")
    temperature_comparison_figure(monthly, hourly, MONTHS, month).show(config={"displaylogo": False, "responsive": True})
    display(hourly.rename(columns={"clearing": "Кр1, °C", "field": "Кр2, °C", "delta": "Разница, °C", "pairs": "Общих дней"}).round(3))


def show_history(archive, station, metric, month, first, last):
    result, baseline, years, slope = historical_context(archive, station, metric, month, first, last)
    label, unit = METRICS[metric]
    if years:
        print(f"Среднее за выбранный период {baseline:.2f} {unit} · пригодных лет {years} из {len(result)}")
    else:
        print("В выбранном месяце и диапазоне нет пригодных лет.")
    if slope is not None:
        print(f"Линейное изменение {slope:+.2f} {unit} за десятилетие · описательная оценка")
    history_figure(result, baseline, unit, f"{archive.labels[station]} · {label} · {MONTHS[month - 1]}").show(
        config={"displaylogo": False, "responsive": True}
    )
    return result
