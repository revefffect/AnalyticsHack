"""Assemble a portable defense report with Plotly embedded."""

from html import escape

import pandas as pd
from plotly.io import to_html

from .charts import coverage_figure, measurements_figure, station_coverage_figure, temperature_comparison_figure
from .core import MONTHS, MeteoArchive, analyze, compare_temperature, reference_coverage


def write_report(archive_path, target):
    archive = MeteoArchive.read(archive_path)
    rows, daily, _, _ = analyze(archive, "kr1", "pressure_hpa", "2024-04-01", "2024-04-30")
    monthly, paired, hourly = compare_temperature(archive, 2025, 7)
    checked, mismatches = reference_coverage(archive)
    record = rows.loc[rows.day.eq(pd.Timestamp("2024-04-02")) & rows.metric_flag].iloc[0]

    def chart(fig, chart_id, first=False):
        return to_html(
            fig, full_html=False, include_plotlyjs=first, div_id=chart_id,
            config={"displaylogo": False, "responsive": True, "toImageButtonOptions": {"filename": chart_id}},
        )
    pressure_chart = chart(measurements_figure(rows, "гПа", "Давление на Кр1 · апрель 2024"), "pressure", True)
    daily_chart = chart(coverage_figure(daily, 18, "Сколько часов пригодно для расчёта"), "coverage-hours")
    comparison = chart(temperature_comparison_figure(monthly, hourly, MONTHS, 7), "comparison")
    coverage = chart(station_coverage_figure(checked, archive.labels, MONTHS), "archive-coverage")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Метеоданные Центрально-Лесного заповедника</title>
<style>
:root {{ color-scheme: light; --ink: #15322a; --soft: #4e655c; --line: #d9e7e0; --accent: #0e6655; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: #f3f7f3; color: var(--ink); font: 16px/1.6 Arial, sans-serif; }}
header, main, footer {{ width: min(1080px, calc(100% - 36px)); margin-inline: auto; }}
header {{ padding: 44px 0 30px; }}
h1 {{ max-width: 800px; margin: 0 0 12px; font: 700 clamp(2rem, 5vw, 3rem)/1.12 Georgia, serif; }}
h2 {{ margin: 0; font: 700 clamp(1.35rem, 3vw, 1.75rem)/1.25 Georgia, serif; }}
p {{ max-width: 76ch; margin: 9px 0 15px; color: var(--soft); }}
nav {{ display: flex; gap: 10px 20px; flex-wrap: wrap; margin-top: 20px; }}
a {{ color: var(--accent); text-underline-offset: 0.3em; }}
section {{ background: white; border: 1px solid var(--line); border-radius: 14px; padding: clamp(14px, 3vw, 28px); margin-bottom: 20px; overflow-wrap: anywhere; }}
.plotly-graph-div {{ max-width: 100%; }}
.checks {{ display: flex; flex-wrap: wrap; gap: 12px 28px; padding: 0; margin: 16px 0 8px; }}
.checks div {{ min-width: 150px; }}
dt {{ font-weight: bold; font-size: 1.3rem; font-variant-numeric: tabular-nums; }}
dd {{ margin: 0; color: var(--soft); }}
code {{ color: #713229; }}
footer {{ padding-bottom: 24px; }}
</style>
</head>
<body>
<header>
  <h1>Метеоданные Центрально-Лесного заповедника</h1>
  <p>Данные урочища «Красное». Наведи курсор на точку, чтобы увидеть замер, флаг и исходную строку. На графиках можно менять масштаб и сохранять изображение.</p>
  <nav aria-label="Разделы отчёта"><a href="#kr1">Давление на Кр1</a><a href="#stations">Поле и поляна</a><a href="#archive">Покрытие архива</a></nav>
</header>
<main>
  <section id="kr1">
    <h2>Почему апрельское давление исключено</h2>
    <dl class="checks"><div><dt>{daily.station_ok.sum()} из {len(daily)}</dt><dd>Пригодные дни по станции</dd></div>
    <div><dt>{daily.metric_ok.sum()} из {len(daily)}</dt><dd>Пригодные дни по давлению</dd></div>
    <div><dt>{rows.valid.sum()} из {len(rows)}</dt><dd>Замеры давления прошли контроль флагов</dd></div></dl>
    <p>Для суточного среднего нужны данные минимум за 18 разных часов. По давлению порог прошли два дня. Среднее за апрель исключено из сводки.</p>
    {pressure_chart}
    <p>Пример исключённой записи · {record.datetime:%d.%m.%Y %H:%M} · {record.value:.2f} гПа · <code>{escape(record.qc_flag)}</code> · <code>{escape(record.source)}</code>, строка {record.csv_line}</p>
    {daily_chart}
  </section>
  <section id="stations">
    <h2>Температура на поле и поляне</h2>
    <dl class="checks"><div><dt>{paired.delta.mean():+.2f} °C</dt><dd>Средняя разница за июль, {len(paired)} общих дней</dd></div>
    <div><dt>{hourly.loc[13, 'delta']:+.2f} °C</dt><dd>Разница в 13-м часу</dd></div>
    <div><dt>{hourly.loc[20, 'delta']:+.2f} °C</dt><dd>Разница в 20-м часу</dd></div></dl>
    <p>Разница считается как Кр2 − Кр1 по общим дням и часам. Часы приведены как в архиве. На наведение график показывает число пар за выбранный час.</p>
    {comparison}
  </section>
  <section id="archive">
    <h2>Где архив полон</h2>
    <p>Совпали {len(checked) - len(mismatches)} из {len(checked)} строк <code>coverage_by_month.csv</code>. Пустая ячейка означает, что месяц отсутствует в эталонной таблице.</p>
    {coverage}
  </section>
</main>
<footer><p>Источник — архив организаторов <code>meteo_hack_dataset.zip</code>. Порог полноты применяется к пригодным измерениям выбранного показателя.</p></footer>
</body>
</html>""", encoding="utf-8")
    return target
