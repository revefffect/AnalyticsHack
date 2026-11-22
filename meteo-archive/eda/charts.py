"""Interactive charts shared by the Colab notebook and offline defense report."""

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


GREEN = "#117666"
RED = "#a83629"
BLUE = "#355b93"
NEUTRAL = "#52645e"


def finish(figure, title, *, height=430):
    figure.update_layout(
        template="simple_white", title=dict(text=title, x=0.025), height=height, autosize=True,
        font=dict(family="Arial, sans-serif", size=13, color="#15322a"),
        margin=dict(l=60, r=32, t=75, b=70),
        legend=dict(orientation="h", y=-0.18, x=0), separators=", ", hoverlabel=dict(namelength=-1),
    )
    figure.update_yaxes(gridcolor="#e6eeea", zerolinecolor="#9fb2a9")
    return figure


def measurements_figure(rows, unit, title):
    plot = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.15,
                         subplot_titles=("Исходные замеры", "После проверки качества"))
    for valid, color, label in [(True, GREEN, "Пригодный замер"), (False, RED, "Исключённый замер")]:
        measured = rows.loc[rows["valid"].eq(valid) & rows["value"].notna()]
        if measured.empty:
            continue
        step = max(1, (len(measured) + 7499) // 7500)
        displayed = measured.iloc[::step]
        trace = go.Scatter(
            x=displayed["datetime"], y=displayed["value"], mode="markers", name=label,
            marker=dict(color=color, size=5, opacity=0.77, symbol="circle" if valid else "x"),
            customdata=displayed[["status", "qc_flag", "source", "csv_line"]].to_numpy(),
            hovertemplate="%{x|%d.%m.%Y %H:%M}<br>%{y:.2f} " + unit
            + "<br>%{customdata[0]}<br>%{customdata[1]}<br>%{customdata[2]} · строка %{customdata[3]}<extra></extra>",
        )
        plot.add_trace(trace, row=1, col=1)
        if valid:
            plot.add_trace(go.Scatter(trace, showlegend=False), row=2, col=1)
    if not plot.data:
        plot.add_annotation(text="За выбранный период нет числовых значений", showarrow=False, xref="paper", yref="paper", x=0.5, y=0.5)
    plot.update_yaxes(title_text=unit)
    plot.update_xaxes(title_text="Дата и время архива", row=2, col=1)
    return finish(plot, title, height=625)


def coverage_figure(daily, minimum, title):
    plot = go.Figure()
    for column, name, color in [("station_slots", "По станции", BLUE), ("metric_slots", "По показателю", GREEN)]:
        plot.add_bar(x=daily.index, y=daily[column], name=name, marker_color=color,
                     hovertemplate="%{x|%d.%m.%Y}<br>%{y} доступных интервалов<extra>" + name + "</extra>")
    plot.add_hline(y=minimum, line_dash="dash", line_color=RED, annotation_text=f"Порог {minimum}")
    plot.update_layout(barmode="group")
    plot.update_yaxes(title_text="Сроков за сутки" if minimum == 72 else "Часов за сутки", rangemode="tozero")
    plot.update_xaxes(title_text="Дата архива")
    return finish(plot, title)


def aggregates_figure(daily, monthly, ten_day, granularity, stat, unit, title):
    if granularity == "Сутки":
        points = daily[stat]
        name = {"mean": "Среднее", "min": "Минимум", "max": "Максимум"}[stat]
    else:
        points = (monthly if granularity == "Месяц" else ten_day).set_index("start")["mean"]
        name = "Среднее суточных средних"
    plot = go.Figure(go.Scatter(x=points.index, y=points, name=name, mode="lines+markers", connectgaps=False,
                                line=dict(color=GREEN), marker=dict(size=5),
                                hovertemplate="%{x|%d.%m.%Y}<br>%{y:.2f} " + unit + "<extra>" + name + "</extra>"))
    if not points.notna().any():
        plot.add_annotation(text="В этом периоде нет пригодных агрегатов", showarrow=False, xref="paper", yref="paper", x=0.5, y=0.5)
    plot.update_yaxes(title_text=unit)
    plot.update_xaxes(title_text="Начало периода")
    return finish(plot, title)


def station_coverage_figure(checked, labels, months):
    plot = make_subplots(rows=3, cols=1, vertical_spacing=0.12, subplot_titles=list(labels.values())[:3])
    for i, station in enumerate(list(labels)[:3], 1):
        source = checked[checked["station"].eq(station)].copy()
        source["percent"] = 100 * source["station_days"] / source["days"]
        years = range(int(source.year.min()), int(source.year.max()) + 1)
        grid = source.pivot(index="month", columns="year", values="percent").reindex(index=range(1, 13), columns=years)
        plot.add_trace(go.Heatmap(
            x=list(years), y=list(months), z=grid.to_numpy(), coloraxis="coloraxis",
            hovertemplate="%{y} %{x}<br>Пригодные дни %{z:.0f}%<extra></extra>",
        ), row=i, col=1)
        plot.update_yaxes(autorange="reversed", row=i, col=1)
        plot.update_xaxes(type="category", nticks=12, row=i, col=1)
    plot.update_layout(coloraxis=dict(colorscale=[[0, "#f4e8e0"], [1, GREEN]], cmin=0, cmax=100,
                                      colorbar=dict(title="Дни, %", len=0.72)))
    return finish(plot, "Покрытие наблюдений по месяцам", height=790)


def temperature_comparison_figure(monthly, hourly, months, month):
    plot = make_subplots(rows=2, cols=1, vertical_spacing=0.19,
                         subplot_titles=("Разница по месяцам", f"Средний суточный ход · {months[month - 1]}"))
    for y, x, hover_counts, row, name in [
        (monthly["mean"], [months[i - 1] for i in monthly.index.month], monthly["count"], 1, "Общих дней"),
        (hourly["delta"], [f"{h:02d}:00" for h in hourly.index], hourly["pairs"], 2, "Пар дней"),
    ]:
        plot.add_trace(go.Bar(x=x, y=y, customdata=hover_counts,
                              marker_color=[BLUE if val < 0 else GREEN for val in y], showlegend=False,
                              hovertemplate="%{x}<br>Кр2 − Кр1 %{y:+.2f} °C<br>" + name + " %{customdata:.0f}<extra></extra>"),
                       row=row, col=1)
    plot.update_yaxes(title_text="Кр2 − Кр1, °C")
    plot.update_xaxes(nticks=12, row=2, col=1)
    return finish(plot, "Температура на поле и поляне · 2025", height=625)


def history_figure(result, baseline, unit, title):
    plot = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.16,
                         subplot_titles=("Месячные средние", "Отклонение от среднего выбранных лет"))
    plot.add_trace(go.Scatter(x=result["year"], y=result["mean"], name="Пригодные месяцы", mode="lines+markers",
                              line=dict(color=GREEN), connectgaps=False), row=1, col=1)
    if result["trend"].notna().any():
        plot.add_trace(go.Scatter(x=result["year"], y=result["trend"], name="Описательный тренд", mode="lines",
                                  line=dict(color=BLUE, dash="dot")), row=1, col=1)
    if pd.notna(baseline):
        plot.add_hline(y=baseline, line_dash="dash", line_color=NEUTRAL, row=1, col=1)
    plot.add_trace(go.Bar(x=result["year"], y=result["difference"], name="Отклонение", showlegend=False,
                          marker_color=[BLUE if val < 0 else GREEN for val in result["difference"]]), row=2, col=1)
    plot.update_yaxes(title_text=unit)
    plot.update_xaxes(title_text="Год", tickformat="d", row=2, col=1)
    return finish(plot, title, height=595)


def wind_figure(rows, title):
    direction = rows.loc[rows["valid"] & rows["value"].between(0, 360), "value"]
    plot = go.Figure()
    if not direction.empty:
        sector = ((direction + 11.25) / 22.5).astype(int) % 16
        counts = sector.value_counts().reindex(range(16), fill_value=0)
        plot.add_barpolar(r=100 * counts / len(direction), theta=[i * 22.5 for i in range(16)],
                          width=19, marker_color=GREEN, customdata=counts,
                          hovertemplate="%{theta}°<br>%{r:.1f}% замеров<br>%{customdata} записей<extra></extra>")
    else:
        plot.add_annotation(text="Пригодных направлений за этот период нет", showarrow=False, x=0.5, y=0.5,
                            xref="paper", yref="paper")
    plot.update_layout(polar=dict(angularaxis=dict(direction="clockwise", rotation=90,
                                                   tickvals=list(range(0, 360, 45)),
                                                   ticktext=["С", "СВ", "В", "ЮВ", "Ю", "ЮЗ", "З", "СЗ"]),
                                  radialaxis=dict(ticksuffix="%")))
    return finish(plot, title)
