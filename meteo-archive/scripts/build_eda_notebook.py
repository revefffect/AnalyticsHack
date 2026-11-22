"""Build the self-contained Colab notebook and, optionally, an offline HTML demo."""

import argparse
import json
from pathlib import Path
from textwrap import dedent


ROOT = Path(__file__).resolve().parent.parent


def sources():
    for name in ("core", "charts", "explorer"):
        source = (ROOT / "eda" / f"{name}.py").read_text(encoding="utf-8")
        yield "\n".join(line for line in source.splitlines() if not line.startswith("from ."))


def notebook():
    cells = []

    def add(key, kind, source, *, form=False):
        cell = {
            "cell_type": kind, "id": key, "source": dedent(source).strip().splitlines(keepends=True),
            "metadata": {"cellView": "form"} if form else {},
        }
        if kind == "code":
            cell.update(execution_count=None, outputs=[])
        cells.append(cell)

    add("intro", "markdown", """
    # Метеоданные заповедника · интерактивный EDA

    Открой ноутбук в Google Colab. Запусти первые три ячейки по порядку и выбери **полный ZIP организаторов** `meteo_hack_dataset.zip` или `meteo_hack_dataset (1).zip`. Распаковывать ZIP не нужно. Дальше выбери нужный раздел и нажми ▶. После изменения фильтров запусти соответствующую ячейку снова.

    **Для защиты** начни с Кр1 и давления за апрель 2024, затем покажи сравнение поля и поляны. На графиках можно приближать даты, отключать серии и наводить курсор на записи. Значения и время хранятся как в исходных данных.
    """)
    add("install", "code", '%pip -q install "pandas>=2.2,<4" "plotly>=6,<8"')
    add("methods", "code", '# @title Правила расчёта и графики\n' + '\n\n'.join(sources()), form=True)
    add("upload", "code", """
    # @title Открыть архив наблюдений
    from os import environ
    from zipfile import BadZipFile
    import plotly.io as pio

    archive_path = ""  # @param {type:"string"}
    local_path = archive_path.strip() or environ.get("METEO_ARCHIVE")
    if local_path:
        archive = MeteoArchive.read(local_path)
    else:
        from google.colab import files

        uploaded = files.upload()
        selected = [name for name in uploaded if name.lower().endswith(".zip")]
        if len(selected) != 1:
            raise ValueError("Выбери один ZIP из материалов хакатона.")
        try:
            archive = MeteoArchive.read(BytesIO(uploaded[selected[0]]))
        except BadZipFile as error:
            raise ValueError("Не удалось прочитать ZIP. Скачай исходный архив заново.") from error
        del uploaded
        pio.renderers.default = "colab"

    show_inventory(archive)
    print(f"Всего {sum(len(frame) for frame in archive.observations.values()):,} наблюдений")
    """, form=True)
    add("explorer-info", "markdown", """
    ## Станция, показатель и период

    Первый запуск показывает давление на Кр1. В строке под графиком увидишь **28 из 30** пригодных суток по станции и **2 из 30** по давлению. Зелёные точки — пригодные замеры, красные крестики — исключённые. График после фильтрации сохраняет отдельные замеры с неполных суток. В сводки входят только сутки, прошедшие порог. Чтобы увидеть конкретную запись, наведи курсор на точку или укажи дату в `detail_day`.

    Полноту станции и полноту отдельного показателя считаем раздельно. Порог по показателю применяется ко всем исследовательским сводкам в этом ноутбуке. Для давления Кр1 это часть задания, для остальных параметров это явно выбранный строгий режим EDA. Сутки считаются по 72 уникальным срокам АМЕ-60 или 18 уникальным часам Сокол М1. Суточное среднее использует все пригодные записи суток. Для декады или месяца усредняем пригодные суточные средние с одинаковым весом дней. Проверка месяца требует не более 10 неполных дней и максимум 4 подряд. Месяцы и декады, выбранные частично, остаются помечены как частичные.
    """)
    add("explorer", "code", """
    # @title Выбрать данные и график
    station_label = "Кр1 · Поляна"  # @param ["АМЕ-60", "Кр1 · Поляна", "Кр2 · Поле", "Добавленная станция"]
    metric_label = "Атмосферное давление"  # @param ["Температура воздуха", "Относительная влажность", "Скорость ветра", "Порывы ветра", "Направление ветра", "Температура почвы на 5 см", "Температура почвы на 10 см", "Температура почвы на 20 см", "Атмосферное давление"]
    start = "2024-04-01"  # @param {type:"date"}
    end = "2024-04-30"  # @param {type:"date"}
    view = "Замеры и флаги"  # @param ["Замеры и флаги", "Полнота по дням", "Среднее за сутки", "Минимум за сутки", "Максимум за сутки", "Среднее за декаду", "Среднее за месяц", "Направления по секторам"]
    detail_day = "2024-04-02"  # @param {type:"string"}

    station = "extra" if station_label == "Добавленная станция" else next(code for code, label in STATIONS.items() if label == station_label)
    metric = next(code for code, (label, unit) in METRICS.items() if label == metric_label)
    if station not in archive.observations:
        raise ValueError("Сначала загрузи CSV в разделе «Добавить станцию для исследования».")
    selected_rows, selected_daily, selected_monthly, selected_ten_day = show_selection(
        archive, station, metric, start, end, view, detail_day.strip()
    )
    """, form=True)
    add("quality-info", "markdown", """
    ## Где в архиве есть пригодные данные

    Тепловая карта показывает долю пригодных суток по станции. Пустая ячейка означает, что месяц отсутствует в эталонной таблице. Проценты позволяют быстро найти сезон с хорошим покрытием и длинные пробелы в архиве.
    """)
    add("quality", "code", """
    # @title Сверить покрытие с эталоном
    checked, mismatches = reference_coverage(archive)
    print(f"Совпали {len(checked) - len(mismatches)} из {len(checked)} строк coverage_by_month.csv")
    if not mismatches.empty:
        display(mismatches)
    station_coverage_figure(checked, archive.labels, MONTHS).show(config={"displaylogo": False})
    """, form=True)
    add("comparison-info", "markdown", """
    ## Поляна и поле · 2025

    Разница везде считается как **Кр2 − Кр1**. Для месячного графика берём общие пригодные дни. Для суточного хода сравниваем средние внутри одного и того же часа одной и той же даты и показываем число пар. В июле средняя разница мала, а в отдельные часы станции расходятся заметнее. Часы подписаны по времени архива.
    """)
    add("comparison", "code", """
    # @title Выбрать месяц для суточного хода
    month = 7  # @param {type:"slider", min:1, max:12, step:1}
    archive_comparison(archive, month)
    """, form=True)
    add("historical-info", "markdown", """
    ## Один месяц в разные годы

    Сравниваем только одноимённые календарные месяцы одной станции. На графике видны пропуски и число лет в расчёте. При трёх и более пригодных годах показываем описательный линейный тренд за десятилетие. Эту оценку можно использовать для формулировки исследовательского вопроса.
    """)
    add("historical", "code", """
    # @title Историческое сравнение и отклонения
    history_station = "АМЕ-60"  # @param ["АМЕ-60", "Кр1 · Поляна", "Кр2 · Поле"]
    history_metric = "Температура воздуха"  # @param ["Температура воздуха", "Относительная влажность", "Скорость ветра", "Порывы ветра", "Температура почвы на 5 см", "Температура почвы на 10 см", "Температура почвы на 20 см", "Атмосферное давление"]
    month = 7  # @param {type:"slider", min:1, max:12, step:1}
    first_year = 1992  # @param {type:"integer"}
    last_year = 2021  # @param {type:"integer"}
    station_code = next(code for code, label in STATIONS.items() if label == history_station)
    metric_code = next(code for code, (label, unit) in METRICS.items() if label == history_metric)
    history = show_history(archive, station_code, metric_code, month, first_year, last_year)
    """, form=True)
    add("extras-info", "markdown", """
    ## Дополнительные действия

    Добавленную станцию можно сразу открыть в фильтре основного графика. Для пробной загрузки нужен CSV в формате хакатона. Название, данные и профиль действуют в текущем сеансе Colab. Укажи профиль по частоте наблюдений, принятой для этой станции.
    """)
    add("extra", "code", """
    # @title Добавить станцию для исследования
    from google.colab import files

    new_name = "Новая станция"  # @param {type:"string"}
    profile = "18 часов"  # @param ["18 часов", "72 срока"]
    batch = files.upload()
    csvs = [name for name in batch if name.lower().endswith(".csv")]
    if len(csvs) != 1:
        raise ValueError("Выбери один CSV в формате архива.")
    name = csvs[0]
    count = archive.add_station(name, batch[name], new_name, 18 if profile == "18 часов" else 72)
    print(f"Добавлено {count:,} строк. В основном графике выбери «Добавленная станция» и даты из CSV.")
    del batch
    """, form=True)
    add("export", "code", """
    # @title Скачать таблицу текущего выбора
    export_view = "Исходные записи"  # @param ["Исходные записи", "Сутки", "Декады", "Месяцы"]
    tables = {"Исходные записи": selected_rows, "Сутки": selected_daily.reset_index(),
              "Декады": selected_ten_day, "Месяцы": selected_monthly}
    filename = f"eda_{station}_{metric}.csv"
    tables[export_view].assign(station=archive.labels[station], metric=metric, unit=METRICS[metric][1]).to_csv(
        filename, index=False, encoding="utf-8-sig"
    )
    try:
        from google.colab import files
    except ImportError:
        print(f"Таблица сохранена в {filename}")
    else:
        files.download(filename)
    """, form=True)
    add("walker-info", "markdown", """
    ## Свободный анализ в PyGWalker

    Запусти этот раздел, если хочешь сама собирать графики. Для отдельных замеров выбери **Scatter**. Для суточных средних поставь `day` на X, а `mean` на Y. Вариант с исходными данными включает причины исключения. Для выборки свыше 25 000 строк используется равномерное прореживание, а кнопка скачивания отдаёт полную таблицу.
    """)
    add("walker-install", "code", '%pip -q install "pygwalker==0.5.0.1"')
    add("walker", "code", """
    # @title Открыть выбранную таблицу в PyGWalker
    import pygwalker as pyg

    walker_view = "Пригодные замеры"  # @param ["Исходные записи", "Пригодные замеры", "Суточная сводка", "Месячное покрытие"]
    walker_tables = {
        "Исходные записи": selected_rows,
        "Пригодные замеры": selected_rows.loc[selected_rows.valid],
        "Суточная сводка": selected_daily.reset_index(),
        "Месячное покрытие": selected_monthly,
    }
    data = walker_tables[walker_view]
    if data.empty:
        print("В этой таблице нет записей. Выбери другую таблицу или период.")
    else:
        step = max(1, (len(data) + 24999) // 25000)
        print(f"В PyGWalker показано {len(data.iloc[::step]):,} из {len(data):,} строк")
        pyg.walk(data.iloc[::step].reset_index(drop=True))
    """, form=True)
    add("methods-references", "markdown", """
    ## Для защиты

    1. Покажи исключённые замеры давления и полноту Кр1 по дням.
    2. Открой 2 апреля и покажи исходный файл и флаг `pressure_hpa:range`.
    3. Сравни среднюю разницу за июль и суточный ход поля и поляны.
    4. Скачай таблицу выбранного сценария, чтобы сохранить воспроизводимый результат.

    Расчёты основаны на описании кейса, `README.md` из архива и проверке по `coverage_by_month.csv`. [Plotly в Colab](https://plotly.com/python/renderers/) · [Документация PyGWalker](https://docs.kanaries.net/pygwalker)
    """)
    return {"cells": cells, "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "file_extension": ".py"},
        "colab": {"provenance": []},
    }, "nbformat": 4, "nbformat_minor": 5}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="Build an offline demo from the supplied ZIP")
    args = parser.parse_args()
    target = ROOT / "notebooks" / "meteo_eda_colab.ipynb"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(notebook(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(target)
    if args.archive:
        from eda.report import write_report

        print(write_report(args.archive, ROOT / "output" / "meteo_eda_demo.html"))


if __name__ == "__main__":
    main()
