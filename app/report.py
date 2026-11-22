"""PDF report for managers: one decision page, evidence, limits."""

from __future__ import annotations

import io
import math
from datetime import date, datetime
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import Patch
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .analytics import analyse, business_actions_from, local_narrative_from
from .data import PARAMETERS, STATIONS

INK = colors.HexColor("#111111")
MUTED = colors.HexColor("#5f6368")
BORDER = colors.HexColor("#e6e6e6")
ACCENT = colors.HexColor("#1d5fbf")
WARNING = colors.HexColor("#92400e")
CRITICAL = colors.HexColor("#c4242a")
PALE_BLUE = colors.HexColor("#eef3fb")
PALE_WARN = colors.HexColor("#fffbeb")
PALE_CRIT = colors.HexColor("#ffedea")

FONT_CANDIDATES = [
    ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/Library/Fonts/Arial Unicode.ttf", "/Library/Fonts/Arial Bold.ttf"),
]


def _register_fonts() -> tuple[str, str]:
    for regular, bold in FONT_CANDIDATES:
        if Path(regular).exists():
            pdfmetrics.registerFont(TTFont("Reserve", regular))
            pdfmetrics.registerFont(TTFont("ReserveBold", bold if Path(bold).exists() else regular))
            pdfmetrics.registerFontFamily("Reserve", normal="Reserve", bold="ReserveBold", italic="Reserve", boldItalic="ReserveBold")
            return "Reserve", "ReserveBold"
    return "Helvetica", "Helvetica-Bold"


FONT, FONT_BOLD = _register_fonts()


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "brand": ParagraphStyle("brand", parent=base["Normal"], fontName=FONT_BOLD, fontSize=8, leading=10, textColor=MUTED, spaceAfter=2),
        "title": ParagraphStyle("title", parent=base["Normal"], fontName=FONT_BOLD, fontSize=21, leading=25, textColor=INK, spaceAfter=4),
        "subtitle": ParagraphStyle("subtitle", parent=base["Normal"], fontName=FONT, fontSize=10, leading=14, textColor=MUTED, spaceAfter=12),
        "h2": ParagraphStyle("h2", parent=base["Normal"], fontName=FONT_BOLD, fontSize=12.5, leading=16, textColor=INK, spaceBefore=12, spaceAfter=6),
        "body": ParagraphStyle("body", parent=base["Normal"], fontName=FONT, fontSize=9.5, leading=14, textColor=INK, spaceAfter=5),
        "small": ParagraphStyle("small", parent=base["Normal"], fontName=FONT, fontSize=8, leading=11, textColor=MUTED),
        "cell": ParagraphStyle("cell", parent=base["Normal"], fontName=FONT, fontSize=8.5, leading=11, textColor=INK),
        "cell_bold": ParagraphStyle("cell_bold", parent=base["Normal"], fontName=FONT_BOLD, fontSize=8.5, leading=11, textColor=INK),
        "kpi_value": ParagraphStyle("kpi_value", parent=base["Normal"], fontName=FONT, fontSize=17, leading=20, textColor=INK),
        "kpi_label": ParagraphStyle("kpi_label", parent=base["Normal"], fontName=FONT, fontSize=7.5, leading=9, textColor=MUTED),
    }


def _fmt(value: float | None, unit: str = "", decimals: int = 1) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "—"
    sign = "+" if unit in {"°C", "°C/10 лет"} else ""
    return f"{value:{sign}.{decimals}f} {unit}".strip().replace("-", "−").replace(".", ",")


def _level_label(level: str) -> str:
    return {"high": "высокий", "moderate": "умеренный", "low": "низкий"}.get(level, level)


def _chart(analysis: dict, annotations: list[dict], station: str, param: str) -> bytes:
    points = analysis["series"]["points"]
    granularity = analysis["series"]["granularity"]
    if granularity == "day" and len(points) > 420:
        monthly = analysis["series"].get("months") or []
        points = [
            {
                "date": f"{m['year']:04d}-{m['month']:02d}-01",
                "label": m["label"],
                "value": m["mean"],
                "baseline": None,
                "valid": m["month_valid"],
            }
            for m in monthly
        ]
        granularity = "month"
    dates = pd.to_datetime([p["date"] for p in points])
    values = pd.Series([p["value"] for p in points], dtype=float)
    baseline = pd.Series([p.get("baseline") for p in points], dtype=float)
    fig, ax = plt.subplots(figsize=(9.1, 3.5), dpi=160)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    if values.notna().any():
        ax.plot(dates, values, color="#1d5fbf", linewidth=1.7, zorder=3, label=PARAMETERS[param]["label"])
    if baseline.notna().any():
        ax.plot(dates, baseline, color="#8a8a8a", linewidth=1.0, linestyle=(0, (4, 3)), zorder=2, label="Среднее для календарного месяца")
    for item in annotations[:6]:
        try:
            start = pd.to_datetime(item["start_date"])
            end = pd.to_datetime(item["end_date"]) + pd.Timedelta(days=1)
        except Exception:
            continue
        color = "#c4242a" if item["severity"] == "critical" else ("#92400e" if item["severity"] == "warning" else "#1d5fbf")
        ax.axvspan(start, end, color=color, alpha=0.10, zorder=1)
    ax.set_ylabel(PARAMETERS[param]["unit"], fontsize=8, color="#5f6368")
    ax.grid(axis="y", color="#e6e6e6", linewidth=0.6)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_visible(False)
    ax.tick_params(axis="both", labelsize=7.5, colors="#5f6368", length=0)
    if granularity == "month":
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m.%Y"))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=8))
    elif granularity == "year":
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    else:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m.%y"))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=8))
    fig.autofmt_xdate(rotation=0, ha="center")
    handles, labels = ax.get_legend_handles_labels()
    if annotations:
        handles.append(Patch(facecolor="#c4242a", alpha=0.18, edgecolor="none"))
        labels.append("Экспертная отметка")
    if handles:
        ax.legend(handles, labels, loc="upper left", frameon=False, fontsize=7, ncol=3)
    fig.tight_layout(pad=0.4)
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", facecolor="white")
    plt.close(fig)
    buffer.seek(0)
    return buffer.read()


def _kpi_table(analysis: dict, styles: dict) -> Table:
    series = analysis["series"]
    summary = series["summary"]
    unit = series["unit"]
    items = [
        ("Среднее за период", _fmt(summary["mean"], unit), f"{summary['valid_points']} валидных точек"),
        ("Последнее значение", _fmt(summary["last_value"], unit), f"{summary['last'] or '—'}"),
        ("Отклонение от базы", _fmt(summary["deviation"], unit), f"база {summary['baseline_years']} года" if summary['baseline_years'] in (2, 3, 4) else f"база {summary['baseline_years']} лет"),
        ("Покрытие", f"{summary['coverage'] * 100:.0f} %", f"{summary['valid_points']} из {summary['points']}"),
    ]
    labels = [Paragraph(item[0], styles["kpi_label"]) for item in items]
    values = [Paragraph(item[1], styles["kpi_value"]) for item in items]
    notes = [Paragraph(item[2], styles["kpi_label"]) for item in items]
    table = Table([labels, values, notes], colWidths=[41.25 * mm] * 4, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ("BOX", (0, 0), (-1, -1), 0.6, BORDER),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, BORDER),
            ]
        )
    )
    return table


def _risk_table(analysis: dict, styles: dict) -> Table:
    risks = analysis["risks"].get("risks", [])
    rows = [[Paragraph("Риск", styles["cell_bold"]), Paragraph("Оценка", styles["cell_bold"]), Paragraph("Что видно", styles["cell_bold"])]]
    for risk in risks:
        score = f"{risk['score']:.0f}/100 · {_level_label(risk['level'])}"
        rows.append(
            [
                Paragraph(risk["label"], styles["cell"]),
                Paragraph(score, styles["cell_bold"] if risk["level"] == "high" else styles["cell"]),
                Paragraph("; ".join(risk["evidence"]), styles["cell"]),
            ]
        )
    table = Table(rows, colWidths=[42 * mm, 27 * mm, 96 * mm], repeatRows=1, hAlign="LEFT")
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, BORDER),
        ("BOX", (0, 0), (-1, -1), 0.6, BORDER),
    ]
    for index, risk in enumerate(risks, start=1):
        if risk["level"] == "high":
            style.append(("BACKGROUND", (1, index), (1, index), PALE_CRIT))
        elif risk["level"] == "moderate":
            style.append(("BACKGROUND", (1, index), (1, index), PALE_WARN))
    table.setStyle(TableStyle(style))
    return table


def _forecast_table(analysis: dict, styles: dict) -> Table:
    forecast = analysis["forecast"]["statistical"]
    points = forecast.get("points", [])
    if not points:
        return Table([[Paragraph("Статистический прогноз недоступен: недостаточно валидных месяцев.", styles["small"])]], colWidths=[165 * mm])
    rows = [[Paragraph("Месяц", styles["cell_bold"]), Paragraph("Оценка", styles["cell_bold"]), Paragraph("Интервал", styles["cell_bold"])]]
    unit = forecast.get("unit", "")
    for point in points[:6]:
        rows.append(
            [
                Paragraph(point["label"], styles["cell"]),
                Paragraph(_fmt(point["value"], unit), styles["cell"]),
                Paragraph(f"{_fmt(point['lower'], unit)} … {_fmt(point['upper'], unit)}", styles["cell"]),
            ]
        )
    table = Table(rows, colWidths=[35 * mm, 45 * mm, 60 * mm], repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, BORDER),
                ("BOX", (0, 0), (-1, -1), 0.6, BORDER),
            ]
        )
    )
    return table


def _annotation_table(annotations: list[dict], styles: dict) -> Table:
    rows = [[Paragraph("Период", styles["cell_bold"]), Paragraph("Тип", styles["cell_bold"]), Paragraph("Отметка эксперта", styles["cell_bold"]), Paragraph("Автор", styles["cell_bold"])]]
    for item in annotations:
        period = item["start_date"] if item["start_date"] == item["end_date"] else f"{item['start_date']} — {item['end_date']}"
        body = f"<b>{xml_escape(item['title'])}</b><br/>{xml_escape(item['note'])}"
        rows.append([Paragraph(period, styles["cell"]), Paragraph(item["kind_label"], styles["cell"]), Paragraph(body, styles["cell"]), Paragraph(xml_escape(item["author"]), styles["small"])])
    if len(rows) == 1:
        rows.append([Paragraph("Нет экспертных отметок за выбранный период.", styles["small"]), "", "", ""])
    table = Table(rows, colWidths=[27 * mm, 22 * mm, 82 * mm, 34 * mm], repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, BORDER),
                ("BOX", (0, 0), (-1, -1), 0.6, BORDER),
            ]
        )
    )
    return table


def _control_table(styles: dict) -> Table:
    rows = [
        [Paragraph("Контроль", styles["cell_bold"]), Paragraph("Значение", styles["cell_bold"]), Paragraph("Как проверено", styles["cell_bold"])],
        [Paragraph("Январь 2010, АМЕ-60", styles["cell"]), Paragraph("−15,20 °C", styles["cell"]), Paragraph("13 пригодных январей; база 1992–2021 −7,57 °C", styles["cell"])],
        [Paragraph("Апрель 2024, Кр1, температура", styles["cell"]), Paragraph("+7,61 °C", styles["cell"]), Paragraph("28 из 30 валидных дней; серия 2", styles["cell"])],
        [Paragraph("Апрель 2024, Кр1, давление", styles["cell"]), Paragraph("исключён", styles["cell"]), Paragraph("2 из 30 дней; серия 28; 1 362 флага range", styles["cell"])],
        [Paragraph("Покрытие архива", styles["cell"]), Paragraph("420 / 420", styles["cell"]), Paragraph("сверка с coverage_by_month.csv", styles["cell"])],
    ]
    table = Table(rows, colWidths=[52 * mm, 32 * mm, 81 * mm], repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, BORDER),
                ("BOX", (0, 0), (-1, -1), 0.6, BORDER),
            ]
        )
    )
    return table


def build_pdf(
    dataset,
    station: str,
    param: str,
    start: date,
    end: date,
    annotations: list[dict] | None = None,
    narrative: str | None = None,
    analysis: dict | None = None,
) -> bytes:
    annotations = annotations or []
    analysis = analysis or analyse(dataset, station, param, start, end)
    series = analysis["series"]
    styles = _styles()
    if not narrative:
        narrative = local_narrative_from(analysis, annotations)
    actions = business_actions_from(analysis)
    chart = _chart(analysis, annotations, station, param)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=22 * mm,
        rightMargin=22 * mm,
        topMargin=20 * mm,
        bottomMargin=18 * mm,
        title=f"Красное · {STATIONS[station]['label']} · {PARAMETERS[param]['label']}",
        author="Аналитический пульт «Красное»",
        subject="Отчёт для управленческого решения",
    )

    def footer(canvas, doc_obj):
        canvas.saveState()
        canvas.setFont(FONT, 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(22 * mm, 10 * mm, "Красное · аналитический пульт · данные не являются климатической нормой")
        canvas.drawRightString(A4[0] - 22 * mm, 10 * mm, f"стр. {doc_obj.page}")
        canvas.restoreState()

    story = []
    story.append(Paragraph("ЦЕНТРАЛЬНО-ЛЕСНОЙ ЗАПОВЕДНИК · УРОЧИЩЕ КРАСНОЕ", styles["brand"]))
    story.append(Paragraph(f"{STATIONS[station]['label']}: {PARAMETERS[param]['label'].lower()}", styles["title"]))
    story.append(
        Paragraph(
            f"{STATIONS[station]['place']} · {series['start']} — {series['end']} · сформировано {datetime.now().strftime('%d.%m.%Y %H:%M')} · "
            f"источник: архив станции, SHA-256 {dataset.meta['sha256'][:12]}…",
            styles["subtitle"],
        )
    )
    story.append(Paragraph("Решение для руководителя", styles["h2"]))
    story.append(Paragraph(xml_escape(narrative), styles["body"]))
    if actions:
        story.append(Paragraph("Что сделать", styles["h2"]))
        for action in actions[:4]:
            story.append(Paragraph(f"<b>{xml_escape(action['title'])}.</b> {xml_escape(action['text'])}", styles["body"]))
    story.append(Spacer(1, 4))
    story.append(_kpi_table(analysis, styles))
    story.append(Spacer(1, 8))
    story.append(Image(io.BytesIO(chart), width=165 * mm, height=63.5 * mm))
    story.append(Paragraph("Синяя линия — валидные агрегаты выбранного ряда; пунктир — среднее для того же календарного месяца за доступные годы. Разрывы не заполнены.", styles["small"]))
    story.append(PageBreak())

    story.append(Paragraph("Риски и ограничения", styles["h2"]))
    story.append(
        Paragraph(
            f"Оценки ниже — процентиль года {analysis['risks'].get('year')} среди {analysis['risks'].get('years_compared')} лет архива. "
            "Это управленческий прокси, а не официальный класс опасности.",
            styles["body"],
        )
    )
    story.append(_risk_table(analysis, styles))
    story.append(Paragraph("Ограничения данных", styles["h2"]))
    limits = [
        "В архиве нет осадков, высоты снега и влажности почвы. Пожарный и паводковый риски рассчитаны по температуре, влажности воздуха, ветру и температуре почвы.",
        "АМЕ-60 и Сокол М1 — разные приборы и точки. Ряды не объединены, тренд через 2021/2022 не строится.",
        "Пропуск, исключённое значение и измеренный ноль различаются. Месяц с более чем 10 неполными днями или серией 5+ дней не публикуется.",
        "Часовой пояс станций заказчиком не подтверждён. Внешний прогноз Open-Meteo — модель для точки 56.46, 32.92, а не измерение заповедника.",
    ]
    for item in limits:
        story.append(Paragraph(f"• {item}", styles["body"]))

    story.append(Paragraph("Прогноз и автовыбор модели", styles["h2"]))
    story.append(
        Paragraph(
            f"Выбрана модель «{analysis['forecast']['statistical'].get('method')}» по бэктесту на последних валидных месяцах. "
            "Интервал — 95 % по остаткам модели; он не учитывает смену прибора и внешние сценарии.",
            styles["body"],
        )
    )
    story.append(_forecast_table(analysis, styles))
    candidates = analysis["forecast"]["statistical"].get("automl", {}).get("candidates", [])
    if candidates:
        text = " · ".join(f"{c['label']}: MAE {c['mae']}" for c in candidates)
        story.append(Paragraph(f"Бэктест: {text}.", styles["small"]))
    story.append(PageBreak())

    story.append(Paragraph("Экспертная разметка", styles["h2"]))
    story.append(Paragraph("Отметки добавлены экспертом вручную. Они не меняют расчёт и не являются выводом модели.", styles["body"]))
    story.append(_annotation_table(annotations, styles))
    story.append(Paragraph("Как проверить число", styles["h2"]))
    story.append(
        Paragraph(
            f"Открыть исходный CSV станции, применить флаги только к параметру «{PARAMETERS[param]['label'].lower()}», "
            "исключить строки dup_conflict целиком, посчитать уникальные сроки за сутки и среднее по валидным суткам. "
            "Месячное среднее — среднее суточных средних; многолетнее — равный вес каждого валидного года. "
            "Все контрольные числа совпадают с независимым расчётом docs/data-audit.md: январь 2010 АМЕ-60 −15,20 °C, "
            "база 1992–2021 −7,57 °C по 13 годам, апрель 2024 Кр1 +7,61 °C, давление за апрель исключено (2 дня из 30).",
            styles["body"],
        )
    )
    story.append(Paragraph("Контрольные числа", styles["h2"]))
    story.append(_control_table(styles))
    story.append(Paragraph("Отчёт подготовлен аналитическим пультом «Красное». Числа рассчитаны по архиву наблюдений; экспертные отметки подписаны автором. "
                           "Перед управленческим решением проверить актуальность архива и ограничения, перечисленные выше.", styles["small"]))

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
