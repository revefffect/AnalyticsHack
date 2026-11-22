"""Deterministic analysis: trends, anomalies, risk proxies, forecasts, AutoML."""

from __future__ import annotations

import math
import os
from datetime import date, datetime, timedelta
from functools import lru_cache

import numpy as np
import pandas as pd
import requests

from .data import Dataset, PARAMETERS, STATIONS

RISK_LABELS = {
    "fire": "Пожарная опасность",
    "flood": "Риск подтопления",
    "heat": "Тепловой стресс",
    "drought": "Почвенная засуха",
}

RISK_NOTES = {
    "fire": "Прокси по температуре, влажности и ветру. Осадков в архиве нет, поэтому это не класс пожарной опасности.",
    "flood": "Прокси по морозным дням зимы и скорости весеннего прогрева. Высоты снега и осадков в архиве нет.",
    "heat": "Средняя температура лета и число жарких дней относительно лет архива.",
    "drought": "Прокси по сухости воздуха, температуре почвы и ветру. Влажности почвы в архиве нет.",
}


def _ru_plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if n % 100 in (11, 12, 13, 14):
        return many
    if n % 10 == 1:
        return one
    if n % 10 in (2, 3, 4):
        return few
    return many


def _fmt(value: float | None, unit: str = "", decimals: int = 1) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "—"
    text = f"{value:.{decimals}f}"
    if value > 0 and unit in {"°C", "°C/10 лет"}:
        text = "+" + text
    text = text.replace(".", ",").replace("-", "−")
    return f"{text} {unit}".strip()


def _level(score: float) -> str:
    if score >= 70:
        return "high"
    if score >= 40:
        return "moderate"
    return "low"


def _percentile(value: float, values: list[float], higher_worse: bool = True) -> float:
    clean = [v for v in values if v is not None and math.isfinite(v)]
    if not clean or value is None or not math.isfinite(value):
        return 50.0
    below = sum(1 for v in clean if v < value)
    equal = sum(1 for v in clean if v == value)
    pct = (below + 0.5 * equal) / len(clean) * 100
    return pct if higher_worse else 100 - pct


def _daily_map(dataset: Dataset, station: str, param: str) -> pd.Series:
    sub = dataset.daily[
        (dataset.daily["station"] == station)
        & (dataset.daily["parameter"] == param)
        & dataset.daily["valid"]
    ]
    return pd.Series(sub["mean"].to_numpy(), index=pd.to_datetime(sub["date"]), name=param)


def _year_metrics(dataset: Dataset, station: str, year: int) -> dict | None:
    t = _daily_map(dataset, station, "t_air")
    rh = _daily_map(dataset, station, "rh")
    wind = _daily_map(dataset, station, "wind_speed")
    soil = _daily_map(dataset, station, "soil_10") if station == "AME60" else pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    frame = pd.concat([t.rename("t"), rh.rename("rh"), wind.rename("wind"), soil.rename("soil")], axis=1)
    frame = frame[frame.index.year == year]
    if frame.empty:
        return None
    summer = frame[(frame.index.month >= 6) & (frame.index.month <= 8)]
    winter = frame[frame.index.month.isin([1, 2, 12])]
    spring = frame[frame.index.month.isin([3, 4])]
    hot = summer[summer["t"] >= 25]
    dry_hot = hot[hot["rh"] <= 40]
    fire_days = dry_hot[dry_hot["wind"] >= 3]
    frost_days = int((winter["t"] < 0).sum())
    winter_min = float(winter["t"].min()) if winter["t"].notna().any() else None
    summer_t = float(summer["t"].mean()) if summer["t"].notna().any() else None
    summer_rh = float(summer["rh"].mean()) if summer["rh"].notna().any() else None
    summer_soil = float(summer["soil"].mean()) if summer["soil"].notna().any() else None
    mar = float(frame[frame.index.month == 3]["t"].mean()) if (frame.index.month == 3).any() else None
    apr = float(frame[frame.index.month == 4]["t"].mean()) if (frame.index.month == 4).any() else None
    melt = (apr - mar) if mar is not None and apr is not None else None
    spring_soil = float(spring["soil"].mean()) if spring["soil"].notna().any() else None
    return {
        "year": year,
        "days": int(frame["t"].notna().sum()),
        "summer_t": summer_t,
        "summer_rh": summer_rh,
        "summer_soil": summer_soil,
        "hot_days": int((summer["t"] >= 25).sum()),
        "very_hot_days": int((summer["t"] >= 30).sum()),
        "dry_days": int((summer["rh"] <= 40).sum()),
        "fire_days": int(len(fire_days)),
        "fire_wind": float(fire_days["wind"].mean()) if len(fire_days) else None,
        "frost_days": frost_days,
        "winter_min": winter_min,
        "melt": melt,
        "spring_soil": spring_soil,
        "max_t": float(frame["t"].max()) if frame["t"].notna().any() else None,
        "min_t": float(frame["t"].min()) if frame["t"].notna().any() else None,
        "rh_min": float(frame["rh"].min()) if frame["rh"].notna().any() else None,
        "wind_max": float(frame["wind"].max()) if frame["wind"].notna().any() else None,
    }


def _risk_scores(dataset: Dataset, station: str, target_year: int) -> dict:
    years = sorted(
        dataset.monthly[
            (dataset.monthly["station"] == station)
            & (dataset.monthly["parameter"] == "t_air")
            & dataset.monthly["month_valid"]
        ]["year"].unique().tolist()
    )
    if not years:
        return {"risks": [], "metrics": [], "year": target_year, "years_compared": 0}
    metrics = [m for y in years if (m := _year_metrics(dataset, station, int(y)))]
    if not metrics:
        return {"risks": [], "metrics": [], "year": target_year, "years_compared": 0}
    current = next((m for m in metrics if m["year"] == target_year), metrics[-1])
    current = current or metrics[-1]
    values = {key: [m[key] for m in metrics if m.get(key) is not None] for key in (
        "summer_t", "summer_rh", "summer_soil", "fire_days", "frost_days", "melt", "spring_soil", "hot_days", "dry_days"
    )}
    heat_pct = _percentile(current["summer_t"], values["summer_t"])
    dry_pct = _percentile(current["summer_rh"], values["summer_rh"], higher_worse=False)
    fire_days_pct = _percentile(current["fire_days"], values["fire_days"])
    wind_pct = _percentile(current["fire_wind"] or 0, [m["fire_wind"] for m in metrics if m.get("fire_wind") is not None])
    frost_pct = _percentile(current["frost_days"], values["frost_days"])
    melt_pct = _percentile(current["melt"], values["melt"])
    spring_soil_pct = _percentile(current["spring_soil"], values["spring_soil"], higher_worse=False)
    soil_heat_pct = _percentile(current["summer_soil"], values["summer_soil"])
    scores = {
        "fire": 0.30 * heat_pct + 0.30 * dry_pct + 0.25 * fire_days_pct + 0.15 * wind_pct,
        "flood": 0.40 * frost_pct + 0.40 * melt_pct + 0.20 * spring_soil_pct,
        "heat": 0.55 * heat_pct + 0.45 * _percentile(current["hot_days"], values["hot_days"]),
        "drought": 0.45 * dry_pct + 0.30 * soil_heat_pct + 0.15 * wind_pct + 0.10 * _percentile(current["dry_days"], values["dry_days"]),
    }
    evidence = {
        "fire": [
            f"{current['fire_days']} дней с жарой ≥25 °C, сухостью ≤40 % и ветром ≥3 м/с",
            f"средняя летняя температура {_fmt(current['summer_t'], '°C')}",
            f"минимум влажности {_fmt(current['rh_min'], '%', 0)}",
        ],
        "flood": [
            f"{current['frost_days']} морозных дней зимой",
            f"весенний прогрев март → апрель {_fmt(current['melt'], '°C')}",
            f"средняя температура почвы весной {_fmt(current['spring_soil'], '°C')}",
        ],
        "heat": [
            f"{current['hot_days']} дней ≥25 °C, из них {current['very_hot_days']} дней ≥30 °C",
            f"максимум {_fmt(current['max_t'], '°C')}",
            f"средняя летняя температура {_fmt(current['summer_t'], '°C')}",
        ],
        "drought": [
            f"{current['dry_days']} сухих летних дней (RH ≤40 %)",
            f"средняя летняя влажность {_fmt(current['summer_rh'], '%', 0)}",
            f"средняя температура почвы летом {_fmt(current['summer_soil'], '°C')}",
        ],
    }
    risks = []
    for key in ("fire", "flood", "heat", "drought"):
        score = max(0.0, min(100.0, float(scores[key])))
        clean_evidence = [line for line in evidence[key] if not line.rstrip().endswith("—")]
        risks.append(
            {
                "key": key,
                "label": RISK_LABELS[key],
                "score": round(score, 1),
                "level": _level(score),
                "evidence": clean_evidence or evidence[key][:1],
                "note": RISK_NOTES[key],
                "year": current["year"],
            }
        )
    return {
        "risks": risks,
        "year": current["year"],
        "years_compared": len(metrics),
        "metrics": metrics,
        "target_year": target_year,
    }


def _automl(dataset: Dataset, station: str, param: str) -> dict:
    sub = dataset.monthly[
        (dataset.monthly["station"] == station)
        & (dataset.monthly["parameter"] == param)
        & dataset.monthly["month_valid"]
    ].dropna(subset=["mean"]).sort_values(["year", "month"])
    rows = sub.to_dict("records")
    if len(rows) < 18:
        return {"parameter": param, "best": None, "candidates": [], "forecast": [], "note": "Недостаточно валидных месяцев для бэктеста."}
    series = [(int(r["year"]), int(r["month"]), float(r["mean"])) for r in rows]

    def predict(model: str, upto: int, year: int, month: int) -> float | None:
        history = series[: upto + 1]
        if model == "seasonal_naive":
            for y, m, v in reversed(history):
                if m == month and y < year:
                    return v
            return None
        if model == "climatology":
            values = [v for y, m, v in history if m == month and y < year]
            return float(np.mean(values)) if values else None
        if model == "trend":
            if len(history) < 12:
                return None
            x = np.arange(len(history), dtype=float)
            y = np.array([v for _, _, v in history], dtype=float)
            slope, intercept = np.polyfit(x, y, 1)
            return float(slope * (upto + 1) + intercept)
        if model == "recent_mean":
            return float(np.mean([v for _, _, v in history[-3:]]))
        if model == "damped":
            last = history[-1][2]
            same = [v for y, m, v in history if m == month and y < year]
            if not same:
                return last
            clim = float(np.mean(same))
            return clim + 0.5 * (last - clim)
        return None

    candidates = []
    for model in ("seasonal_naive", "climatology", "trend", "recent_mean", "damped"):
        errors = []
        for i, (year, month, actual) in enumerate(series[-12:], start=len(series) - 12):
            predicted = predict(model, i - 1, year, month)
            if predicted is not None and math.isfinite(predicted):
                errors.append(abs(predicted - actual))
        if errors:
            candidates.append({"model": model, "mae": float(np.mean(errors)), "n": len(errors)})
    if not candidates:
        return {"parameter": param, "best": None, "candidates": [], "forecast": [], "note": "Модели не дали прогноза."}
    candidates.sort(key=lambda item: (item["mae"], -item["n"]))
    best = candidates[0]["model"]
    last_year, last_month, _ = series[-1]
    forecast = []
    residual = np.std([
        abs(predict(best, i - 1, y, m) - v)
        for i, (y, m, v) in enumerate(series[-12:], start=len(series) - 12)
        if predict(best, i - 1, y, m) is not None
    ]) if len(series) >= 12 else 0.0
    for step in range(1, 7):
        month = last_month + step
        year = last_year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        value = predict(best, len(series) - 1, year, month)
        if value is None:
            continue
        forecast.append(
            {
                "date": f"{year:04d}-{month:02d}-01",
                "label": f"{month:02d}.{year}",
                "value": round(float(value), 2),
                "lower": round(float(value - 1.96 * residual), 2),
                "upper": round(float(value + 1.96 * residual), 2),
            }
        )
    labels = {
        "seasonal_naive": "Сезонное наивное",
        "climatology": "Климатология месяца",
        "trend": "Линейный тренд",
        "recent_mean": "Среднее последних 3 месяцев",
        "damped": "Демпфированный сезонный",
    }
    return {
        "parameter": param,
        "unit": PARAMETERS[param]["unit"],
        "best": best,
        "best_label": labels[best],
        "candidates": [
            {"model": c["model"], "label": labels[c["model"]], "mae": round(c["mae"], 2), "n": c["n"]}
            for c in candidates
        ],
        "forecast": forecast,
        "note": "Бэктест на последних 12 валидных месяцах; модель выбирается автоматически по MAE.",
    }


def _statistical_forecast(dataset: Dataset, station: str, param: str, months: int = 6) -> dict:
    automl = _automl(dataset, station, param)
    return {
        "method": automl.get("best_label") or "Сезонное среднее",
        "points": automl.get("forecast", []),
        "unit": PARAMETERS[param]["unit"],
        "note": "Статистический прогноз по ряду станции, не модель погоды.",
        "automl": automl,
    }


def _daily_forecast(dataset: Dataset, station: str, param: str, days: int = 14) -> list[dict]:
    sub = dataset.daily[
        (dataset.daily["station"] == station)
        & (dataset.daily["parameter"] == param)
        & dataset.daily["valid"]
    ].dropna(subset=["mean"])
    if sub.empty:
        return []
    sub = sub.copy()
    sub["doy"] = pd.to_datetime(sub["date"]).dt.dayofyear
    climate = sub.groupby("doy")["mean"].agg(["mean", "std", "count"]).reset_index()
    last_date = pd.to_datetime(sub["date"]).max()
    recent = sub[pd.to_datetime(sub["date"]) >= last_date - pd.Timedelta(days=14)]
    climate_recent = climate[climate["doy"].isin(recent["doy"])]
    if len(climate_recent):
        anomaly = float(recent["mean"].mean() - climate_recent["mean"].mean())
    else:
        anomaly = 0.0
    points = []
    for step in range(1, days + 1):
        current = last_date + pd.Timedelta(days=step)
        doy = current.dayofyear
        row = climate[climate["doy"] == doy]
        if row.empty:
            continue
        base = float(row["mean"].iloc[0])
        spread = float(row["std"].iloc[0]) if pd.notna(row["std"].iloc[0]) else 0.0
        value = base + 0.6 * anomaly
        points.append(
            {
                "date": current.date().isoformat(),
                "label": current.strftime("%d.%m"),
                "value": round(value, 2),
                "lower": round(value - 1.96 * spread, 2),
                "upper": round(value + 1.96 * spread, 2),
            }
        )
    return points


@lru_cache(maxsize=8)
def _external_forecast(cache_key: str) -> dict | None:
    if os.environ.get("RESERVE_OFFLINE") == "1":
        return {"available": False, "reason": "Офлайн-режим: внешний прогноз отключён", "points": []}
    url = (
        "https://api.open-meteo.com/v1/forecast"
        "?latitude=56.46&longitude=32.92"
        "&hourly=temperature_2m,relative_humidity_2m,wind_speed_10m,surface_pressure"
        "&forecast_days=7&timezone=Europe%2FMoscow"
    )
    try:
        response = requests.get(url, timeout=12)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 - external service, show the reason
        return {"available": False, "reason": f"Open-Meteo недоступен: {exc.__class__.__name__}", "points": []}
    hourly = payload.get("hourly", {})
    times = hourly.get("time", [])
    if not times:
        return {"available": False, "reason": "Open-Meteo вернул пустой ответ", "points": []}
    frame = pd.DataFrame(
        {
            "time": pd.to_datetime(times),
            "t": hourly.get("temperature_2m"),
            "rh": hourly.get("relative_humidity_2m"),
            "wind": hourly.get("wind_speed_10m"),
            "pressure": hourly.get("surface_pressure"),
        }
    )
    frame["date"] = frame["time"].dt.date
    daily = frame.groupby("date").agg(
        t_mean=("t", "mean"),
        t_min=("t", "min"),
        t_max=("t", "max"),
        rh_mean=("rh", "mean"),
        wind_max=("wind", "max"),
        pressure_mean=("pressure", "mean"),
    ).reset_index()
    points = [
        {
            "date": row["date"].isoformat(),
            "label": row["date"].strftime("%d.%m"),
            "t_mean": round(float(row["t_mean"]), 1),
            "t_min": round(float(row["t_min"]), 1),
            "t_max": round(float(row["t_max"]), 1),
            "rh_mean": round(float(row["rh_mean"]), 0),
            "wind_max": round(float(row["wind_max"]), 1),
            "pressure_mean": round(float(row["pressure_mean"]), 1),
        }
        for _, row in daily.iterrows()
    ]
    return {
        "available": True,
        "source": "Open-Meteo · модель, не измерение станции",
        "issued_at": datetime.now().isoformat(timespec="minutes"),
        "points": points,
        "note": "Внешний краткосрочный прогноз для точки 56.46, 32.92. Не заменяет наблюдения АМЕ-60 или Сокол М1.",
    }


def _period_quality(dataset: Dataset, station: str, param: str, start: date, end: date) -> dict:
    sub = dataset.monthly[
        (dataset.monthly["station"] == station)
        & (dataset.monthly["parameter"] == param)
        & (dataset.monthly["year"] >= start.year)
        & (dataset.monthly["year"] <= end.year)
    ]
    rows = []
    for row in sub.sort_values(["year", "month"]).to_dict("records"):
        if row["year"] == start.year and row["month"] < start.month:
            continue
        if row["year"] == end.year and row["month"] > end.month:
            continue
        if not row["month_valid"]:
            rows.append(
                {
                    "year": int(row["year"]),
                    "month": int(row["month"]),
                    "valid_days": int(row["valid_days"]),
                    "days_in_month": int(row["days_in_month"]),
                    "incomplete_days": int(row["incomplete_days"]),
                    "longest_gap": int(row["longest_gap"]),
                }
            )
    reasons: dict[str, int] = {}
    for row in rows:
        if row["incomplete_days"] > 10:
            reasons["больше 10 неполных дней"] = reasons.get("больше 10 неполных дней", 0) + 1
        if row["longest_gap"] >= 5:
            reasons["серия из 5+ пропущенных дней"] = reasons.get("серия из 5+ пропущенных дней", 0) + 1
    return {
        "excluded_months": rows,
        "exclusion_reasons": reasons,
        "total_months": int(len(sub)),
        "valid_months": int(len(sub) - len(rows)),
    }


def analyse(dataset: Dataset, station: str, param: str, start: date, end: date) -> dict:
    series = dataset.series(station, param, start, end, "month")
    monthly = dataset.monthly[
        (dataset.monthly["station"] == station)
        & (dataset.monthly["parameter"] == param)
        & dataset.monthly["month_valid"]
    ].dropna(subset=["mean"])
    eda = dataset.eda(station, param)
    period_quality = _period_quality(dataset, station, param, start, end)
    target_year = end.year
    valid_years = sorted(monthly["year"].unique().tolist())
    if valid_years:
        target_year = min(target_year, int(max(valid_years)))
    risk = _risk_scores(dataset, station, target_year)
    automl = _automl(dataset, station, param)
    forecast = _statistical_forecast(dataset, station, param)
    daily_forecast = _daily_forecast(dataset, station, param) if param in {"t_air", "rh", "wind_speed", "wind_gust", "pressure_hpa"} else []
    external = _external_forecast(f"{date.today().isoformat()}:{station}") if param in {"t_air", "rh", "wind_speed", "pressure_hpa"} else None
    insights = _insights(dataset, station, param, start, end, series, risk, eda, period_quality)
    return {
        "series": series,
        "eda": eda,
        "period_quality": period_quality,
        "risks": risk,
        "forecast": {
            "statistical": forecast,
            "daily": daily_forecast,
            "external": external,
        },
        "insights": insights,
    }


def _insights(dataset: Dataset, station: str, param: str, start: date, end: date, series: dict, risk: dict, eda: dict, period_quality: dict) -> list[dict]:
    out = []
    summary = series["summary"]
    trend = summary.get("trend_per_decade")
    unit = PARAMETERS[param]["unit"]
    if trend:
        direction = "рост" if trend["per_decade"] > 0 else "снижение"
        out.append(
            {
                "kind": "trend",
                "severity": "info",
                "title": "Тренд ряда",
                "text": f"За {trend['first_year']}–{trend['last_year']} {direction} {abs(trend['per_decade']):.2f} {unit}/10 лет "
                        f"по {trend['points']} валидным месяцам (R²={trend['r2']:.2f}). Это описание ряда, не прогноз.",
            }
        )
    if summary.get("deviation") is not None and summary.get("baseline") is not None:
        out.append(
            {
                "kind": "anomaly",
                "severity": "warning" if abs(summary["deviation"]) > 2 * (summary.get("std") or 1) else "info",
                "title": "Последнее значение против базы",
                "text": f"Последний валидный месяц: {_fmt(summary['last_value'], unit)}; среднее по базе "
                        f"{_fmt(summary['baseline'], unit)} ({summary['baseline_years']} {_ru_plural(summary['baseline_years'], 'год', 'года', 'лет')}); отклонение {_fmt(summary['deviation'], unit)}.",
            }
        )
    if risk.get("risks"):
        top = max(risk["risks"], key=lambda item: item["score"])
        if top["score"] >= 40:
            out.append(
                {
                    "kind": "risk",
                    "severity": "critical" if top["score"] >= 70 else "warning",
                    "title": top["label"],
                    "text": f"Оценка {top['score']:.0f}/100 по {risk['years_compared']} годам архива. " + "; ".join(top["evidence"][:2]) + ".",
                }
            )
    if period_quality["excluded_months"]:
        out.append(
            {
                "kind": "quality",
                "severity": "warning",
                "title": "Пропуски в выбранном периоде",
                "text": f"{len(period_quality['excluded_months'])} {_ru_plural(len(period_quality['excluded_months']), 'месяц', 'месяца', 'месяцев')} {'не проходит' if len(period_quality['excluded_months']) == 1 else 'не проходят'} правило полноты. Причина: "
                        + ", ".join(f"{k} — {v}" for k, v in period_quality["exclusion_reasons"].items())
                        + ". Пустая ячейка означает пропуск, а не ноль.",
            }
        )
    if eda["anomalies"]:
        top = eda["anomalies"][0]
        out.append(
            {
                "kind": "anomaly",
                "severity": "info",
                "title": "Аномалия в архиве",
                "text": f"{top['label']}: {top['value']} {unit} против медианы {top['baseline']} {unit} "
                        f"({top['direction']} на {abs(top['z']):.1f} устойчивых σ).",
            }
        )
    if not out:
        out.append({"kind": "info", "severity": "info", "title": "Ряд без выраженных сигналов", "text": "В выбранном периоде нет тренда, аномалий или рисков выше порога."})
    return out


def local_narrative_from(analysis: dict, annotations: list[dict]) -> str:
    series = analysis["series"]
    summary = series["summary"]
    risk = analysis["risks"]
    unit = series["unit"]
    parts = [
        f"{series['station_label']} ({series['place']}), {series['label'].lower()}, "
        f"{series['start']} — {series['end']}."
    ]
    if summary["mean"] is not None:
        parts.append(
            f"Среднее за выбранный период {_fmt(summary['mean'], unit)} по {summary['valid_points']} валидным точкам "
            f"({summary['coverage'] * 100:.0f} % календаря)."
        )
    if summary.get("trend_per_decade") is not None:
        parts.append(
            f"Наклон {_fmt(summary['trend_per_decade'], f'{unit}/10 лет', 2)} по {summary['valid_points']} валидным месяцам."
        )
    if risk.get("risks"):
        top = sorted(risk["risks"], key=lambda item: -item["score"])[:2]
        parts.append("Риски: " + "; ".join(f"{r['label'].lower()} {r['score']:.0f}/100" for r in top) + ".")
    if annotations:
        critical = sum(1 for a in annotations if a["severity"] == "critical")
        parts.append(
            f"Экспертная разметка: {len(annotations)} {_ru_plural(len(annotations), 'отметка', 'отметки', 'отметок')}, "
            f"из них {critical} {_ru_plural(critical, 'критичная', 'критичных', 'критичных')}."
        )
    parts.append("Осадков, высоты снега и влажности почвы в архиве нет: риски рассчитаны как прокси по температуре, влажности воздуха, ветру и температуре почвы.")
    return " ".join(parts)


def local_narrative(dataset: Dataset, station: str, param: str, start: date, end: date, annotations: list[dict]) -> str:
    return local_narrative_from(analyse(dataset, station, param, start, end), annotations)


def business_actions_from(analysis: dict) -> list[dict]:
    actions = []
    for risk in sorted(analysis["risks"].get("risks", []), key=lambda item: -item["score"])[:3]:
        if risk["score"] < 40:
            continue
        if risk["key"] == "fire":
            actions.append({"title": "Проверить противопожарный план", "text": "Сверить дежурство, подъезды и запрет на открытый огонь на период высокой оценки пожарной опасности.", "risk": risk["label"]})
        elif risk["key"] == "flood":
            actions.append({"title": "Подготовить весенний паводок", "text": "Проверить дороги и логистику в низинах, заранее согласовать объезды и оповещение.", "risk": risk["label"]})
        elif risk["key"] == "heat":
            actions.append({"title": "Скорректировать летние работы", "text": "Перенести полевые работы с полудня, проверить водоснабжение и тень на маршрутах.", "risk": risk["label"]})
        elif risk["key"] == "drought":
            actions.append({"title": "Проверить почвенную влагу", "text": "Осадков и влажности почвы в архиве нет; перед выводами о засухе нужен отдельный датчик или внешний источник.", "risk": risk["label"]})
    quality = analysis.get("period_quality", {})
    if quality.get("excluded_months"):
        actions.append(
            {
                "title": "Не публиковать исключённые месяцы",
                "text": f"{len(quality['excluded_months'])} {_ru_plural(len(quality['excluded_months']), 'месяц', 'месяца', 'месяцев')} в выбранном периоде {'не проходит' if len(quality['excluded_months']) == 1 else 'не проходят'} правило полноты. В отчёте оставить пустую ячейку и объяснить причину.",
                "risk": "Качество данных",
            }
        )
    if not actions:
        actions.append({"title": "Продолжить наблюдение", "text": "Пороговые риски не превышены; сохранить текущий режим и проверить ряд после обновления архива.", "risk": "Нет"})
    return actions


def business_actions(dataset: Dataset, station: str, param: str, start: date, end: date) -> list[dict]:
    return business_actions_from(analyse(dataset, station, param, start, end))


@lru_cache(maxsize=32)
def _archive_value(target: str) -> dict:
    if os.environ.get("RESERVE_OFFLINE") == "1":
        return {"available": False, "reason": "Офлайн-режим: внешняя оценка отключена"}
    url = (
        "https://archive-api.open-meteo.com/v1/archive"
        "?latitude=56.46&longitude=32.92"
        f"&start_date={target}&end_date={target}"
        "&daily=temperature_2m_mean,relative_humidity_2m_mean,wind_speed_10m_max,surface_pressure_mean"
        "&timezone=Europe%2FMoscow"
    )
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        daily = response.json().get("daily", {})
        if not daily.get("time"):
            return {"available": False, "reason": "Open-Meteo archive вернул пустой ответ"}
        return {
            "available": True,
            "source": "Open-Meteo archive · реанализ, не измерение станции",
            "temperature_2m_mean": daily.get("temperature_2m_mean", [None])[0],
            "relative_humidity_2m_mean": daily.get("relative_humidity_2m_mean", [None])[0],
            "wind_speed_10m_max": daily.get("wind_speed_10m_max", [None])[0],
            "surface_pressure_mean": daily.get("surface_pressure_mean", [None])[0],
        }
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": f"Open-Meteo archive недоступен: {exc.__class__.__name__}"}


def estimate_gap(dataset: Dataset, station: str, param: str, target: date) -> dict:
    """Estimate a missing value without ever presenting the estimate as an observation."""
    daily = dataset.daily[
        (dataset.daily["station"] == station) & (dataset.daily["parameter"] == param)
    ]
    exact = daily[daily["date"].dt.date == target]
    if not exact.empty and bool(exact["valid"].iloc[0]) and pd.notna(exact["mean"].iloc[0]):
        return {
            "date": target.isoformat(),
            "station": station,
            "parameter": param,
            "observed": round(float(exact["mean"].iloc[0]), 2),
            "unit": PARAMETERS[param]["unit"],
            "recommendation": "Есть валидное наблюдение: для отчёта используйте его. Оценки не нужны.",
            "climatology": None,
            "neighbor": None,
            "external": None,
        }
    valid = daily[daily["valid"]].dropna(subset=["mean"]).copy()
    valid["month"] = pd.to_datetime(valid["date"]).dt.month
    valid["day"] = pd.to_datetime(valid["date"]).dt.day
    same_day = valid[(valid["month"] == target.month) & (valid["day"] == target.day)]
    if len(same_day) < 3:
        same_day = valid[valid["month"] == target.month]
    climatology = None
    if len(same_day):
        climatology = {
            "value": round(float(same_day["mean"].mean()), 2),
            "std": round(float(same_day["mean"].std()), 2) if len(same_day) > 1 else None,
            "count": int(len(same_day)),
            "basis": "тот же календарный день" if len(valid[(valid["month"] == target.month) & (valid["day"] == target.day)]) >= 3 else "тот же календарный месяц",
        }
    neighbor = None
    if station in {"KR1", "KR2"}:
        other = "KR2" if station == "KR1" else "KR1"
        part = dataset.daily[
            (dataset.daily["station"] == other)
            & (dataset.daily["parameter"] == param)
            & (dataset.daily["date"].dt.date == target)
        ]
        if not part.empty and bool(part["valid"].iloc[0]) and pd.notna(part["mean"].iloc[0]):
            neighbor = {
                "station": other,
                "station_label": STATIONS[other]["label"],
                "value": round(float(part["mean"].iloc[0]), 2),
                "note": "Соседняя точка того же прибора. Не замена наблюдению: разность точек может быть систематической.",
            }
    external = None
    if param in {"t_air", "rh", "wind_speed", "pressure_hpa"}:
        raw = _archive_value(target.isoformat())
        if raw.get("available"):
            key = {"t_air": "temperature_2m_mean", "rh": "relative_humidity_2m_mean", "wind_speed": "wind_speed_10m_max", "pressure_hpa": "surface_pressure_mean"}[param]
            value = raw.get(key)
            external = {"value": value, "source": raw["source"], "note": "Модель/реанализ для точки 56.46, 32.92; не измерение заповедника."}
        else:
            external = {"value": None, "source": raw.get("reason", "недоступно")}
    pieces = []
    if climatology:
        pieces.append(f"климатология {climatology['value']} {PARAMETERS[param]['unit']} (±{climatology['std'] or '—'}, n={climatology['count']})")
    if neighbor:
        pieces.append(f"соседняя точка {neighbor['value']} {PARAMETERS[param]['unit']}")
    if external and external.get("value") is not None:
        pieces.append(f"внешняя модель {external['value']} {PARAMETERS[param]['unit']}")
    recommendation = (
        "Наблюдение отсутствует. Для отчёта оставьте пустую ячейку; если нужна оценка, подпишите её как оценку и укажите источник. "
        + ("Доступно: " + "; ".join(pieces) + ". " if pieces else "")
        + "Не смешивайте оценку с измерением и не заполняйте ею многолетний тренд."
    )
    return {
        "date": target.isoformat(),
        "station": station,
        "parameter": param,
        "observed": None,
        "unit": PARAMETERS[param]["unit"],
        "climatology": climatology,
        "neighbor": neighbor,
        "external": external,
        "recommendation": recommendation,
    }
