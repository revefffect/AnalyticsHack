"""Load the reserve archive once and expose validated daily/monthly series.

The organizer rules implemented here are the ones documented in
``docs/data-audit.md``: flags apply to one parameter, ``dup_conflict`` removes
the whole row, a day is valid after 72 distinct AME-60 times or 18 distinct
Sokol hours, and a month is valid with at most 10 incomplete days and no run of
5 incomplete days.
"""

from __future__ import annotations

import calendar
import hashlib
import math
import pickle
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE = ROOT / "data" / "raw" / "meteo_hack_dataset.zip"
CACHE_DIR = ROOT / ".cache"
CACHE_FILE = CACHE_DIR / "dataset.pkl"
CACHE_VERSION = 5

STATIONS = {
    "AME60": {
        "label": "АМЕ-60",
        "place": "Урочище Красное · базовая станция",
        "instrument": "АМЕ-60",
        "threshold": 72,
        "slots": 96,
        "period": "1992–2021",
    },
    "KR1": {
        "label": "Сокол М1 · Кр1",
        "place": "Поляна",
        "instrument": "Сокол М1",
        "threshold": 18,
        "slots": 24,
        "period": "2022–2025",
    },
    "KR2": {
        "label": "Сокол М1 · Кр2",
        "place": "Поле",
        "instrument": "Сокол М1",
        "threshold": 18,
        "slots": 24,
        "period": "2022–2025",
    },
}

PARAMETERS = {
    "t_air": {"label": "Температура воздуха", "unit": "°C", "decimals": 1, "kind": "line"},
    "rh": {"label": "Относительная влажность", "unit": "%", "decimals": 0, "kind": "line"},
    "wind_speed": {"label": "Скорость ветра", "unit": "м/с", "decimals": 1, "kind": "line"},
    "wind_gust": {"label": "Порывы ветра", "unit": "м/с", "decimals": 1, "kind": "line"},
    "wind_dir": {"label": "Направление ветра", "unit": "°", "decimals": 0, "kind": "circular"},
    "soil_05": {"label": "Температура почвы, 5 см", "unit": "°C", "decimals": 1, "kind": "line"},
    "soil_10": {"label": "Температура почвы, 10 см", "unit": "°C", "decimals": 1, "kind": "line"},
    "soil_20": {"label": "Температура почвы, 20 см", "unit": "°C", "decimals": 1, "kind": "line"},
    "pressure_hpa": {"label": "Атмосферное давление", "unit": "гПа", "decimals": 1, "kind": "line"},
}

STATION_PARAMS = {
    "AME60": ["t_air", "rh", "wind_speed", "wind_gust", "wind_dir", "soil_05", "soil_10", "soil_20"],
    "KR1": ["t_air", "rh", "wind_speed", "wind_gust", "wind_dir", "pressure_hpa"],
    "KR2": ["t_air", "rh", "wind_speed", "wind_gust", "wind_dir", "pressure_hpa"],
}

ANNOTATION_KINDS = {
    "anomaly": "Аномалия",
    "event": "Событие",
    "exclusion": "Исключение",
    "risk": "Риск",
    "note": "Заметка",
}

SEVERITIES = {"info": "Инфо", "warning": "Внимание", "critical": "Критично"}


def _station_code(path: str) -> str:
    stem = Path(path).stem
    code = stem.rsplit("_", 1)[0].lower()
    return {"ame60": "AME60", "kr1": "KR1", "kr2": "KR2"}[code]


def _prepare_frame(df: pd.DataFrame, station: str) -> pd.DataFrame:
    df = df.copy()
    df["datetime"] = pd.to_datetime(df["datetime"], format="%Y-%m-%d %H:%M")
    df["date"] = df["datetime"].dt.normalize()
    df["year"] = df["datetime"].dt.year.astype("int16")
    df["month"] = df["datetime"].dt.month.astype("int8")
    df["day"] = df["datetime"].dt.day.astype("int8")
    if station == "AME60":
        df["slot"] = df["datetime"].dt.strftime("%H:%M")
    else:
        df["slot"] = df["datetime"].dt.strftime("%H")

    flags = df["qc_flag"].fillna("").astype(str)
    df["dup_conflict"] = flags.str.contains("dup_conflict", regex=False)
    for param in PARAMETERS:
        if param not in df.columns:
            df[param] = np.nan
        marked = flags.str.contains(f"{param}:", regex=False)
        df[f"{param}_ok"] = df[param].notna() & ~df["dup_conflict"] & ~marked
    return df


def _circular_mean(values: pd.Series) -> float:
    radians = np.deg2rad(values.to_numpy(dtype=float))
    angle = math.degrees(math.atan2(np.sin(radians).mean(), np.cos(radians).mean()))
    return angle % 360


def _build_daily(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for station, df in frames.items():
        threshold = STATIONS[station]["threshold"]
        for param in STATION_PARAMS[station]:
            ok = df.loc[df[f"{param}_ok"], ["date", "slot", param]]
            if ok.empty:
                continue
            grouped = ok.groupby("date", sort=True)
            counts = grouped.agg(n=(param, "size"), slots=("slot", "nunique"))
            if PARAMETERS[param]["kind"] == "circular":
                means = grouped[param].apply(_circular_mean)
            else:
                means = grouped[param].mean()
            part = counts.join(means.rename("mean"))
            part["valid"] = part["slots"] >= threshold
            part.loc[~part["valid"], "mean"] = np.nan
            part = part.reset_index()
            part["station"] = station
            part["parameter"] = param
            rows.append(part)
    if not rows:
        return pd.DataFrame(columns=["date", "n", "slots", "mean", "valid", "station", "parameter"])
    daily = pd.concat(rows, ignore_index=True)
    daily["date"] = pd.to_datetime(daily["date"])
    return daily[["station", "parameter", "date", "mean", "n", "slots", "valid"]]


def _build_monthly(daily: pd.DataFrame, frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for station, df in frames.items():
        station_days = pd.date_range(df["date"].min(), df["date"].max(), freq="D")
        day_set = {d.date() for d in station_days}
        for param in STATION_PARAMS[station]:
            sub = daily[(daily["station"] == station) & (daily["parameter"] == param)]
            if sub.empty:
                continue
            valid_map = {d.date(): bool(v) for d, v in zip(sub["date"], sub["valid"])}
            mean_map = {
                d.date(): float(m)
                for d, m, v in zip(sub["date"], sub["mean"], sub["valid"])
                if v and pd.notna(m)
            }
            first = station_days.min().date()
            last = station_days.max().date()
            year, month = first.year, first.month
            while (year, month) <= (last.year, last.month):
                days_in_month = calendar.monthrange(year, month)[1]
                valid_days = 0
                longest = streak = 0
                for day in range(1, days_in_month + 1):
                    current = date(year, month, day)
                    if current not in day_set:
                        streak += 1
                    elif valid_map.get(current, False):
                        valid_days += 1
                        streak = 0
                    else:
                        streak += 1
                    longest = max(longest, streak)
                incomplete = days_in_month - valid_days
                month_valid = incomplete <= 10 and longest < 5
                values = [
                    mean_map[date(year, month, day)]
                    for day in range(1, days_in_month + 1)
                    if date(year, month, day) in mean_map
                ]
                rows.append(
                    {
                        "station": station,
                        "parameter": param,
                        "year": year,
                        "month": month,
                        "days_in_month": days_in_month,
                        "valid_days": valid_days,
                        "incomplete_days": incomplete,
                        "longest_gap": longest,
                        "month_valid": month_valid,
                        "mean": float(np.mean(values)) if month_valid and values else np.nan,
                    }
                )
                month += 1
                if month == 13:
                    month = 1
                    year += 1
    monthly = pd.DataFrame(rows)
    if not monthly.empty:
        monthly["year"] = monthly["year"].astype("int16")
        monthly["month"] = monthly["month"].astype("int8")
    return monthly


@dataclass
class Dataset:
    frames: dict[str, pd.DataFrame]
    daily: pd.DataFrame
    monthly: pd.DataFrame
    flags: dict[str, int]
    meta: dict

    def params_for(self, station: str) -> list[str]:
        return STATION_PARAMS[station]

    def range_for(self, station: str) -> tuple[date, date]:
        df = self.frames[station]
        return df["date"].min().date(), df["date"].max().date()

    def calendar_month_baseline(self, station: str, param: str, month: int) -> tuple[float | None, int]:
        sub = self.monthly[
            (self.monthly["station"] == station)
            & (self.monthly["parameter"] == param)
            & (self.monthly["month"] == month)
            & (self.monthly["month_valid"])
        ]
        if sub.empty:
            return None, 0
        return float(sub["mean"].mean()), int(len(sub))

    def series(
        self,
        station: str,
        param: str,
        start: date,
        end: date,
        granularity: str = "month",
    ) -> dict:
        if station not in STATIONS:
            raise ValueError("Неизвестная станция")
        if param not in STATION_PARAMS[station]:
            raise ValueError("Параметр не измеряется этой станцией")
        if start > end:
            raise ValueError("Начало периода позже конца")
        if granularity not in {"day", "month", "year"}:
            raise ValueError("Неизвестная детализация")

        station_first, station_last = self.range_for(station)
        start = max(start, station_first)
        end = min(end, station_last)
        if start > end:
            raise ValueError("В выбранном периоде нет данных станции")

        if granularity == "day":
            points = self._daily_points(station, param, start, end)
        elif granularity == "month":
            points = self._monthly_points(station, param, start, end)
        else:
            points = self._yearly_points(station, param, start, end)

        valid = [p for p in points if p["value"] is not None]
        values = [p["value"] for p in valid]
        baseline, baseline_years = self._period_baseline(station, param, start, end)
        summary = {
            "points": len(points),
            "valid_points": len(valid),
            "coverage": len(valid) / len(points) if points else 0,
            "mean": float(np.mean(values)) if values else None,
            "min": float(np.min(values)) if values else None,
            "max": float(np.max(values)) if values else None,
            "first": valid[0]["label"] if valid else None,
            "last": valid[-1]["label"] if valid else None,
            "last_value": valid[-1]["value"] if valid else None,
            "baseline": baseline,
            "baseline_years": baseline_years,
            "deviation": (valid[-1]["value"] - baseline) if valid and baseline is not None else None,
            "trend_per_decade": self._trend(station, param, start, end),
        }
        return {
            "station": station,
            "parameter": param,
            "unit": PARAMETERS[param]["unit"],
            "label": PARAMETERS[param]["label"],
            "decimals": PARAMETERS[param]["decimals"],
            "station_label": STATIONS[station]["label"],
            "place": STATIONS[station]["place"],
            "granularity": granularity,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "points": points,
            "summary": summary,
            "months": self._monthly_table(station, param, start, end),
        }

    def _daily_points(self, station: str, param: str, start: date, end: date) -> list[dict]:
        sub = self.daily[
            (self.daily["station"] == station)
            & (self.daily["parameter"] == param)
            & (self.daily["date"].dt.date >= start)
            & (self.daily["date"].dt.date <= end)
        ]
        lookup = {
            d.date(): (bool(v), None if pd.isna(m) else float(m), int(s), int(n))
            for d, v, m, s, n in zip(sub["date"], sub["valid"], sub["mean"], sub["slots"], sub["n"])
        }
        baselines = {
            month: self.calendar_month_baseline(station, param, month)[0] for month in range(1, 13)
        }
        points = []
        current = start
        threshold = STATIONS[station]["threshold"]
        while current <= end:
            valid, value, slots, n = lookup.get(current, (False, None, 0, 0))
            baseline = baselines.get(current.month)
            points.append(
                {
                    "date": current.isoformat(),
                    "label": current.strftime("%d.%m.%Y"),
                    "value": value if valid else None,
                    "valid": bool(valid and value is not None),
                    "coverage": min(1.0, slots / threshold) if threshold else 0,
                    "slots": slots,
                    "n": n,
                    "baseline": baseline,
                    "anomaly": (value - baseline) if value is not None and baseline is not None else None,
                }
            )
            current += timedelta(days=1)
        return points

    def _monthly_points(self, station: str, param: str, start: date, end: date) -> list[dict]:
        sub = self.monthly[
            (self.monthly["station"] == station)
            & (self.monthly["parameter"] == param)
            & (self.monthly["year"] >= start.year)
            & (self.monthly["year"] <= end.year)
        ]
        lookup = {
            (int(y), int(m)): row
            for y, m, row in zip(sub["year"], sub["month"], sub.to_dict("records"))
        }
        baselines = {
            month: self.calendar_month_baseline(station, param, month)[0] for month in range(1, 13)
        }
        points = []
        year, month = start.year, start.month
        while (year, month) <= (end.year, end.month):
            row = lookup.get((year, month))
            baseline = baselines.get(month)
            if row is not None:
                value = None if pd.isna(row["mean"]) else float(row["mean"])
                points.append(
                    {
                        "date": f"{year:04d}-{month:02d}-01",
                        "label": f"{month:02d}.{year}",
                        "value": value,
                        "valid": bool(row["month_valid"]) and value is not None,
                        "coverage": row["valid_days"] / row["days_in_month"],
                        "valid_days": int(row["valid_days"]),
                        "days_in_month": int(row["days_in_month"]),
                        "longest_gap": int(row["longest_gap"]),
                        "month_valid": bool(row["month_valid"]),
                        "baseline": baseline,
                        "anomaly": (value - baseline) if value is not None and baseline is not None else None,
                    }
                )
            else:
                points.append(
                    {
                        "date": f"{year:04d}-{month:02d}-01",
                        "label": f"{month:02d}.{year}",
                        "value": None,
                        "valid": False,
                        "coverage": 0,
                        "valid_days": 0,
                        "days_in_month": calendar.monthrange(year, month)[1],
                        "longest_gap": calendar.monthrange(year, month)[1],
                        "month_valid": False,
                        "baseline": baseline,
                        "anomaly": None,
                    }
                )
            month += 1
            if month == 13:
                month = 1
                year += 1
        return points

    def _yearly_points(self, station: str, param: str, start: date, end: date) -> list[dict]:
        sub = self.monthly[
            (self.monthly["station"] == station)
            & (self.monthly["parameter"] == param)
            & (self.monthly["month_valid"])
        ]
        points = []
        for year in range(start.year, end.year + 1):
            part = sub[sub["year"] == year]
            values = part["mean"].dropna()
            points.append(
                {
                    "date": f"{year:04d}-01-01",
                    "label": str(year),
                    "value": float(values.mean()) if len(values) else None,
                    "valid": bool(len(values)),
                    "coverage": len(values) / 12,
                    "valid_months": int(len(values)),
                    "baseline": None,
                    "anomaly": None,
                }
            )
        return points

    def _period_baseline(self, station: str, param: str, start: date, end: date) -> tuple[float | None, int]:
        sub = self.monthly[
            (self.monthly["station"] == station)
            & (self.monthly["parameter"] == param)
            & (self.monthly["month_valid"])
        ]
        if sub.empty:
            return None, 0
        years = sub.groupby("year")["mean"].mean()
        return float(years.mean()), int(len(years))

    def _trend(self, station: str, param: str, start: date, end: date) -> dict | None:
        sub = self.monthly[
            (self.monthly["station"] == station)
            & (self.monthly["parameter"] == param)
            & (self.monthly["month_valid"])
            & (self.monthly["year"] >= start.year)
            & (self.monthly["year"] <= end.year)
        ].dropna(subset=["mean"])
        if len(sub) < 24 or sub["year"].nunique() < 5:
            return None
        x = sub["year"].to_numpy(dtype=float) + (sub["month"].to_numpy(dtype=float) - 0.5) / 12
        y = sub["mean"].to_numpy(dtype=float)
        slope, intercept = np.polyfit(x, y, 1)
        predicted = slope * x + intercept
        ss_res = float(((y - predicted) ** 2).sum())
        ss_tot = float(((y - y.mean()) ** 2).sum())
        return {
            "per_decade": float(slope * 10),
            "points": int(len(sub)),
            "r2": 1 - ss_res / ss_tot if ss_tot else None,
            "first_year": int(sub["year"].min()),
            "last_year": int(sub["year"].max()),
        }

    def _monthly_table(self, station: str, param: str, start: date, end: date) -> list[dict]:
        sub = self.monthly[
            (self.monthly["station"] == station)
            & (self.monthly["parameter"] == param)
            & (self.monthly["year"] >= start.year)
            & (self.monthly["year"] <= end.year)
        ]
        rows = []
        for row in sub.sort_values(["year", "month"]).to_dict("records"):
            if row["year"] == start.year and row["month"] < start.month:
                continue
            if row["year"] == end.year and row["month"] > end.month:
                continue
            rows.append(
                {
                    "year": int(row["year"]),
                    "month": int(row["month"]),
                    "label": f"{int(row['month']):02d}.{int(row['year'])}",
                    "mean": None if pd.isna(row["mean"]) else round(float(row["mean"]), 2),
                    "valid_days": int(row["valid_days"]),
                    "days_in_month": int(row["days_in_month"]),
                    "incomplete_days": int(row["incomplete_days"]),
                    "longest_gap": int(row["longest_gap"]),
                    "month_valid": bool(row["month_valid"]),
                }
            )
        return rows

    def quality(self, station: str, param: str) -> dict:
        df = self.frames[station]
        flags = df["qc_flag"].fillna("").astype(str)
        relevant = flags[flags.str.contains(f"{param}:", regex=False)]
        flag_counts: dict[str, int] = {}
        for value in relevant:
            for tag in str(value).split(";"):
                if tag.startswith(f"{param}:") or tag == "dup_conflict":
                    flag_counts[tag] = flag_counts.get(tag, 0) + 1
        sub = self.monthly[
            (self.monthly["station"] == station) & (self.monthly["parameter"] == param)
        ]
        years = sorted(sub["year"].unique().tolist())
        months = []
        for row in sub.sort_values(["year", "month"]).to_dict("records"):
            months.append(
                {
                    "year": int(row["year"]),
                    "month": int(row["month"]),
                    "valid_days": int(row["valid_days"]),
                    "days_in_month": int(row["days_in_month"]),
                    "incomplete_days": int(row["incomplete_days"]),
                    "longest_gap": int(row["longest_gap"]),
                    "month_valid": bool(row["month_valid"]),
                    "mean": None if pd.isna(row["mean"]) else float(row["mean"]),
                    "coverage": row["valid_days"] / row["days_in_month"],
                }
            )
        excluded_months = [m for m in months if not m["month_valid"]]
        reason_counts: dict[str, int] = {}
        for item in excluded_months:
            if item["incomplete_days"] > 10:
                reason_counts["больше 10 неполных дней"] = reason_counts.get("больше 10 неполных дней", 0) + 1
            if item["longest_gap"] >= 5:
                reason_counts["серия из 5+ пропущенных дней"] = reason_counts.get("серия из 5+ пропущенных дней", 0) + 1
        param_rows = int(df[f"{param}_ok"].sum())
        return {
            "station": station,
            "parameter": param,
            "rows": int(len(df)),
            "valid_values": param_rows,
            "missing_values": int(len(df) - param_rows),
            "flags": dict(sorted(flag_counts.items(), key=lambda kv: -kv[1])),
            "years": years,
            "months": months,
            "excluded_months": excluded_months,
            "exclusion_reasons": reason_counts,
            "station_days": int(df.loc[~df["dup_conflict"], "date"].nunique()),
            "conflict_rows": int(df["dup_conflict"].sum()),
            "off_grid": int(
                (
                    ~df["dup_conflict"]
                    & (df["datetime"].dt.minute % 15 != 0)
                ).sum()
            )
            if station == "AME60"
            else 0,
        }

    def eda(self, station: str, param: str) -> dict:
        df = self.frames[station]
        quality = self.quality(station, param)
        daily = self.daily[
            (self.daily["station"] == station) & (self.daily["parameter"] == param) & self.daily["valid"]
        ]
        values = daily["mean"].dropna()
        quantiles = {}
        if len(values):
            for q in (0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99):
                quantiles[str(q)] = float(values.quantile(q))
        correlations = []
        base = daily.set_index("date")["mean"]
        for other in STATION_PARAMS[station]:
            if other == param:
                continue
            part = self.daily[
                (self.daily["station"] == station)
                & (self.daily["parameter"] == other)
                & self.daily["valid"]
            ].set_index("date")["mean"]
            joined = pd.concat([base, part], axis=1, keys=[param, other]).dropna()
            if len(joined) >= 30:
                corr = joined[param].corr(joined[other])
                if pd.notna(corr):
                    correlations.append({"parameter": other, "label": PARAMETERS[other]["label"], "r": float(corr), "n": int(len(joined))})
        correlations.sort(key=lambda item: -abs(item["r"]))
        anomalies = []
        monthly = self.monthly[
            (self.monthly["station"] == station)
            & (self.monthly["parameter"] == param)
            & self.monthly["month_valid"]
        ]
        for month in range(1, 13):
            part = monthly[monthly["month"] == month].dropna(subset=["mean"])
            if len(part) < 5:
                continue
            median = float(part["mean"].median())
            mad = float((part["mean"] - median).abs().median())
            scale = 1.4826 * mad if mad else float(part["mean"].std())
            if not scale:
                continue
            for row in part.to_dict("records"):
                z = (row["mean"] - median) / scale
                if abs(z) >= 2.5:
                    anomalies.append(
                        {
                            "year": int(row["year"]),
                            "month": int(row["month"]),
                            "label": f"{int(row['month']):02d}.{int(row['year'])}",
                            "value": round(float(row["mean"]), 2),
                            "baseline": round(median, 2),
                            "z": round(float(z), 2),
                            "direction": "выше" if z > 0 else "ниже",
                        }
                    )
        anomalies.sort(key=lambda item: -abs(item["z"]))
        return {
            "quality": quality,
            "distribution": {
                "count": int(len(values)),
                "mean": float(values.mean()) if len(values) else None,
                "std": float(values.std()) if len(values) else None,
                "min": float(values.min()) if len(values) else None,
                "max": float(values.max()) if len(values) else None,
                "quantiles": quantiles,
            },
            "correlations": correlations[:6],
            "anomalies": anomalies[:12],
            "row_counts": {
                "total": int(len(df)),
                "by_year": {str(int(y)): int(n) for y, n in df.groupby("year").size().items()},
            },
        }


def _cache_digest() -> str:
    stat = ARCHIVE.stat()
    return hashlib.sha256(f"{CACHE_VERSION}:{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()


def _load_frames() -> tuple[dict[str, pd.DataFrame], dict[str, int], dict]:
    frames: dict[str, pd.DataFrame] = {}
    flags: dict[str, int] = {}
    meta: dict = {"archive": str(ARCHIVE), "sha256": None, "files": 0, "rows": 0}
    digest = hashlib.sha256()
    with open(ARCHIVE, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    meta["sha256"] = digest.hexdigest()
    with ZipFile(ARCHIVE) as archive:
        names = sorted(n for n in archive.namelist() if "/data/" in n and n.endswith(".csv"))
        meta["files"] = len(names)
        for name in names:
            station = _station_code(name)
            with archive.open(name) as handle:
                df = pd.read_csv(handle, encoding="utf-8-sig")
            frames.setdefault(station, []).append(df)
            meta["rows"] += len(df)
    prepared = {}
    for station, parts in frames.items():
        df = pd.concat(parts, ignore_index=True)
        prepared[station] = _prepare_frame(df, station)
        for tag, count in (
            prepared[station]["qc_flag"].fillna("").astype(str).str.split(";").explode().value_counts().items()
        ):
            if tag:
                flags[tag] = flags.get(tag, 0) + int(count)
    meta["rows_by_station"] = {s: int(len(d)) for s, d in prepared.items()}
    return prepared, dict(sorted(flags.items(), key=lambda kv: -kv[1])), meta


def load_dataset(force: bool = False) -> Dataset:
    if not force and CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "rb") as handle:
                payload = pickle.load(handle)
            if payload.get("digest") == _cache_digest():
                return Dataset(
                    frames=payload["frames"],
                    daily=payload["daily"],
                    monthly=payload["monthly"],
                    flags=payload["flags"],
                    meta=payload["meta"],
                )
        except Exception:
            pass
    frames, flags, meta = _load_frames()
    daily = _build_daily(frames)
    monthly = _build_monthly(daily, frames)
    dataset = Dataset(frames=frames, daily=daily, monthly=monthly, flags=flags, meta=meta)
    CACHE_DIR.mkdir(exist_ok=True)
    with open(CACHE_FILE, "wb") as handle:
        pickle.dump(
            {
                "digest": _cache_digest(),
                "frames": frames,
                "daily": daily,
                "monthly": monthly,
                "flags": flags,
                "meta": meta,
            },
            handle,
            protocol=pickle.HIGHEST_PROTOCOL,
        )
    return dataset


def catalog(dataset: Dataset) -> dict:
    stations = []
    for code, info in STATIONS.items():
        first, last = dataset.range_for(code)
        stations.append(
            {
                "code": code,
                **info,
                "first": first.isoformat(),
                "last": last.isoformat(),
                "parameters": [
                    {"code": p, **PARAMETERS[p]} for p in STATION_PARAMS[code]
                ],
            }
        )
    return {
        "stations": stations,
        "parameters": PARAMETERS,
        "annotation_kinds": ANNOTATION_KINDS,
        "severities": SEVERITIES,
        "meta": dataset.meta,
        "flags": dataset.flags,
        "reserve": {
            "name": "Центрально-Лесной заповедник",
            "place": "Урочище Красное",
            "coordinates": {"lat": 56.46, "lon": 32.92},
        },
    }
