"""Render a saved management report. Run as a module to rebuild the archive example."""
from datetime import datetime
from io import BytesIO
from threading import Lock

from meteo.analytics import PERIODS
from meteo.importer import PARAMETERS, STATIONS

# Matplotlib's renderer and rc configuration are shared within a worker process.
_render_lock = Lock()
PAGE_W, PAGE_H = 595, 842
MARGIN, RIGHT = 44, PAGE_W - 44
INK, MUTED, FOREST, RULE = '#172a27', '#4a605a', '#18634c', '#c9d4ce'
LABELS = {'anomaly': 'Аномалия', 'sensor_issue': 'Прибор', 'weather_event': 'Погодное событие',
          'normal': 'В пределах нормы', 'gap': 'Пропуск'}
SOURCES = {'station': 'Станционные наблюдения', 'archive': 'Архив наблюдений', 'upload': 'Загруженный набор', 'stream': 'Поток измерений',
           'stream_snapshot': 'Снимок потока измерений', 'reanalysis': 'Реанализ', 'forecast': 'Прогноз'}


def _number(value, unit=''):
    if value is None:
        return 'нет данных'
    return f'{value:,.2f}'.replace(',', ' ').replace('.', ',') + (f' {unit}' if unit else '')


def _day(value):
    return str(value)[:10]


def _plot(page, data):
    """Keep missing periods as gaps and use the original time spacing."""
    points = data['points']
    if not any(row.get('value') is not None for row in points):
        page.paragraph('Выбранный интервал не содержит пригодных значений. Проверить полноту исходных измерений.')
        return
    import matplotlib.dates as dates

    height = 100
    page.space(height + 35)
    axis = page.fig.add_axes(((MARGIN + 34) / PAGE_W, (PAGE_H - page.y - height) / PAGE_H,
                              (RIGHT - MARGIN - 40) / PAGE_W, (height - 6) / PAGE_H))
    times = [datetime.fromisoformat(row['start']) for row in points]
    values = [float('nan') if row.get('value') is None else row['value'] for row in points]
    axis.plot(times, values, color=FOREST, linewidth=1.3, marker='.' if len(points) < 90 else None,
              markersize=4, rasterized=len(points) > 2000)
    axis.yaxis.grid(color=RULE, linewidth=.55)
    axis.tick_params(axis='both', length=0, labelsize=8, pad=5, colors=MUTED)
    for edge in ('left', 'right', 'top'):
        axis.spines[edge].set_visible(False)
    axis.spines['bottom'].set_color(RULE)
    if len(times) == 1:
        axis.set_xticks(times)
    else:
        interval = max(1, (len(times) - 1) // 3)
        indices = sorted({0, interval, 2 * interval, len(times) - 1})
        axis.set_xticks([times[i] for i in indices if i < len(times)])
    axis.xaxis.set_major_formatter(dates.DateFormatter('%H:%M' if data['step'] == 'instant'
                                                        and len({_day(x) for x in times}) == 1 else '%d.%m.%y'))
    page.y += height + 30


class _Pages:
    def __init__(self, output):
        from matplotlib.textpath import TextToPath
        self.output = output
        self.text_metrics = TextToPath()
        self.fig = None
        self.page_number = 0
        self.new()

    def new(self):
        from matplotlib.figure import Figure
        if self.fig is not None:
            self.output.savefig(self.fig)
        self.fig = Figure(figsize=(PAGE_W / 72, PAGE_H / 72), facecolor='white')
        self.page_number += 1
        self.y = 65
        self.text(MARGIN, 27, 'МЕТЕОАРХИВ', size=10, bold=True, color=FOREST)
        self.text(RIGHT, 27, 'УПРАВЛЕНЧЕСКИЙ ОТЧЁТ', size=8, color=MUTED, align='right')
        self.line(44)
        self.line(799)
        self.text(MARGIN, 811, 'AnalyticsHack  /  Метеорологические наблюдения', size=8, color=MUTED)
        self.text(RIGHT, 811, str(self.page_number), size=8, color=MUTED, align='right')

    def text(self, x, y, value, *, size=10.5, bold=False, color=INK, align='left'):
        self.fig.text(x / PAGE_W, 1 - y / PAGE_H, str(value), va='top', ha=align, size=size,
                      color=color, family='DejaVu Sans', weight='bold' if bold else 'normal', parse_math=False)

    def line(self, y):
        from matplotlib.lines import Line2D
        self.fig.add_artist(Line2D([MARGIN / PAGE_W, RIGHT / PAGE_W], [1 - y / PAGE_H] * 2,
                                   transform=self.fig.transFigure, color=RULE, linewidth=.7))

    def space(self, n):
        if self.y + n > 780:
            self.new()

    def paragraph(self, text, *, size=10.5, bold=False, color=INK, after=8):
        from matplotlib.font_manager import FontProperties
        font = FontProperties(family='DejaVu Sans', size=size, weight='bold' if bold else 'normal')
        width = RIGHT - MARGIN
        line_height = size * 1.43

        def paint(line):
            self.space(line_height)
            self.text(MARGIN, self.y, line, size=size, bold=bold, color=color)
            self.y += line_height

        def fits(value):
            return self.text_metrics.get_text_width_height_descent(value, font, ismath=False)[0] <= width

        line = ''
        for word in str(text or '').split():
            candidate = f'{line} {word}' if line else word
            if line and not fits(candidate):
                paint(line)
                line = ''
            if not fits(word):
                for character in word:
                    if line and not fits(line + character):
                        paint(line)
                        line = ''
                    line += character
            else:
                line = f'{line} {word}' if line else word
        if line:
            paint(line)
        self.y += after

    def section(self, title):
        self.space(58)
        self.y += 4
        self.paragraph(title, size=15, bold=True, after=5)


def business_report_pdf(snapshot, insights, annotations=()):
    """Return an A4 PDF using only the supplied immutable snapshot and its analysis."""
    # Loading the chart renderer only on export keeps normal API startup inexpensive.
    from matplotlib import rc_context
    from matplotlib.backends.backend_pdf import PdfPages

    output = BytesIO()
    name = PARAMETERS.get(snapshot['parameter'], (snapshot['parameter'],))[0]
    station = STATIONS.get(snapshot['station'], snapshot['station'])
    density = insights['coverage']
    unit = snapshot.get('unit', '')
    metadata = {'Title': 'Метеоархив — отчёт для руководителя', 'Author': 'AnalyticsHack',
                'CreationDate': None, 'ModDate': None}
    with _render_lock, rc_context({'pdf.fonttype': 42, 'font.family': 'DejaVu Sans',
                                   'text.usetex': False}), PdfPages(output, metadata=metadata) as pdf:
        page = _Pages(pdf)
        page.paragraph(snapshot.get('title') or name, size=22, bold=True, after=3)
        page.paragraph(f"{station}   /   {snapshot['start']} — {snapshot['end']}", color=MUTED, after=12)
        page.section('Что показывают данные')
        coverage_text = f"{name}. {PERIODS.get(snapshot['step'], snapshot['step'])}. Пригодных суток {density['valid_days']} из {density['days']}."
        if snapshot['step'] != 'day':
            coverage_text += f" Пригодных периодов {density['valid_periods']} из {density['periods']}."
        page.paragraph(coverage_text)
        dist = insights['distribution']
        if dist['median'] is not None:
            page.paragraph(f"Медиана пригодных значений — {_number(dist['median'], unit)}. "
                           f"Диапазон от {_number(dist['min'], unit)} до {_number(dist['max'], unit)}.")
        if insights['trend'].get('description'):
            page.paragraph(insights['trend']['description'])
        page.paragraph(f'{name}, {unit}', color=MUTED, size=9, after=2)
        _plot(page, snapshot)
        page.section('Что проверить до решения')
        for item in insights['management']:
            if item['title'] == 'Покрытие исходных данных':
                continue
            page.space(45)
            page.paragraph(item['title'], bold=True, size=11, after=2)
            page.paragraph(item['detail'], after=7)

        page.new()
        page.section('Прогноз и проверка точности')
        model = insights['automl']
        if model['status'] == 'forecast' and model.get('forecast'):
            target = model['forecast']
            page.paragraph(f"Оценка на {target['date']} — {_number(target['value'], unit)}. "
                           f"Метод — {target.get('label') or target['method']}.", bold=True)
        if model.get('reason'):
            page.paragraph(model['reason'])
        for item in model.get('candidates', []):
            page.paragraph(f"{item.get('label') or item['method']}  ·  средняя абсолютная ошибка "
                           f"{_number(item['mae'], unit)}  ·  контрольных точек {item['n']}.", after=4)

        page.section('Автоматически отмеченные отклонения')
        for anomaly in insights['anomalies'][:4]:
            page.paragraph(f"{anomaly['date']}  ·  {_number(anomaly['value'], unit)}. "
                           f"{anomaly['explanation']}", after=4)
        if insights.get('anomaly_count', len(insights['anomalies'])) > 4:
            total = insights.get('anomaly_count', len(insights['anomalies']))
            page.paragraph(f"Показано 4 из {total} отмеченных отклонений.", color=MUTED)
        elif not insights['anomalies']:
            page.paragraph('В этом срезе алгоритм не выделил точек для проверки.')

        page.section('Экспертная разметка')
        if not annotations:
            page.paragraph('В сохранённом срезе нет экспертных отметок.')
        for mark in annotations:
            page.paragraph(f"{_day(mark['start'])} — {_day(mark['end'])}  ·  {LABELS.get(mark['label'], mark['label'])}  ·  "
                           f"{mark['author']}. {mark.get('note', '')}", after=5)

        page.section('Источник и границы применения')
        meta = snapshot.get('metadata') or {}
        page.paragraph(f"{snapshot.get('name', '')}. {meta.get('provider', '')}".strip(), after=4)
        page.paragraph(f"Тип источника — {SOURCES.get(snapshot.get('source'), snapshot.get('source', 'не указан'))}. "
                       f"Версия набора {snapshot['dataset']}. Правила качества {snapshot.get('qc', 'не указаны')}. "
                       f"Станция {snapshot['station']}, показатель {snapshot['parameter']}.", size=9.5, color=MUTED)
        if meta.get('archive_member'):
            page.paragraph(meta['archive_member'], size=9.5, color=MUTED, after=4)
        page.paragraph(snapshot.get('method', ''), size=9.5, color=MUTED, after=2)
        pdf.savefig(page.fig)
    return output.getvalue()


def _build_archive_example(destination):
    import json
    from datetime import date
    from pathlib import Path
    from tempfile import TemporaryDirectory

    from meteo.analytics import slice_data
    from meteo.api import seed
    from meteo.decision_insights import decision_insights
    from meteo.store import Store

    with TemporaryDirectory() as directory:
        store = Store(directory)
        version = seed(store)['id']
        with store.connect() as db:
            dataset = db.execute('SELECT * FROM datasets WHERE id=?', (version,)).fetchone()
            snapshot = slice_data(db, dataset, 'KR2', 't_air', date(2024, 4, 1), date(2024, 4, 30), 'day')
            metadata = json.loads(dataset['metadata'])
            metadata['archive_member'] = 'meteo_hack_dataset.zip / meteo_clgz_hackathon/data/sokol_m1/kr2_2024.csv'
            snapshot.update(title='Температура в поле в апреле', metadata=metadata)
    Path(destination).write_bytes(business_report_pdf(snapshot, decision_insights(snapshot)))


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Собрать пример PDF на архиве Центрально-Лесного заповедника')
    parser.add_argument('--example', metavar='PDF', required=True, help='Путь к готовому PDF')
    _build_archive_example(parser.parse_args().example)
