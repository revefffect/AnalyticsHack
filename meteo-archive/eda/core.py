"""Rules and exploratory summaries for the reserve weather archive."""

from io import BytesIO
from itertools import groupby
from pathlib import PurePosixPath
from zipfile import ZipFile

import numpy as np
import pandas as pd


METRICS = {
    "t_air": ("Температура воздуха", "°C"),
    "rh": ("Относительная влажность", "%"),
    "wind_speed": ("Скорость ветра", "м/с"),
    "wind_gust": ("Порывы ветра", "м/с"),
    "wind_dir": ("Направление ветра", "°"),
    "soil_05": ("Температура почвы на 5 см", "°C"),
    "soil_10": ("Температура почвы на 10 см", "°C"),
    "soil_20": ("Температура почвы на 20 см", "°C"),
    "pressure_hpa": ("Атмосферное давление", "гПа"),
}
STATIONS = {"ame60": "АМЕ-60", "kr1": "Кр1 · Поляна", "kr2": "Кр2 · Поле"}
MONTHS = ("Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек")


def parse_observations(stream, filename):
    frame = pd.read_csv(stream, low_memory=False)
    metrics = [metric for metric in METRICS if metric in frame]
    if "datetime" not in frame or not metrics or frame.empty:
        raise ValueError(f"В файле {filename} нужны datetime, хотя бы один известный показатель и записи.")
    frame["datetime"] = pd.to_datetime(frame["datetime"], format="%Y-%m-%d %H:%M")
    if frame["datetime"].isna().any():
        raise ValueError(f"В файле {filename} найдены пустые даты.")
    frame["qc_flag"] = frame["qc_flag"].fillna("").astype(str) if "qc_flag" in frame else ""
    frame[metrics] = frame[metrics].apply(pd.to_numeric)
    frame["source"] = filename
    frame["csv_line"] = np.arange(2, len(frame) + 2)
    return frame[["datetime", *metrics, "qc_flag", "source", "csv_line"]]


class MeteoArchive:
    def __init__(self, observations, reference):
        self.observations = observations
        self.reference = reference
        self.labels = dict(STATIONS)
        self.profiles = {"ame60": 72, "kr1": 18, "kr2": 18}

    @classmethod
    def read(cls, source):
        frames = {station: [] for station in STATIONS}
        with ZipFile(source) as archive:
            ref_path = next((p for p in archive.namelist() if PurePosixPath(p).name == "coverage_by_month.csv"), None)
            if ref_path is None:
                raise ValueError("Выбери полный ZIP организаторов с coverage_by_month.csv.")
            with archive.open(ref_path) as stream:
                reference = pd.read_csv(stream)
            data_dir = PurePosixPath(ref_path).parent / "data"
            for name in sorted(archive.namelist()):
                path = PurePosixPath(name)
                station = path.stem.rsplit("_", 1)[0]
                if path.suffix == ".csv" and path.is_relative_to(data_dir) and station in frames:
                    with archive.open(name) as stream:
                        frames[station].append(parse_observations(stream, path.name))
        if not all(frames.values()):
            raise ValueError("В архиве нужны файлы АМЕ-60, Кр1 и Кр2.")
        observations = {
            station: pd.concat(parts, ignore_index=True).sort_values("datetime", kind="stable")
            for station, parts in frames.items()
        }
        return cls(observations, reference)

    def add_station(self, filename, content, name, minimum):
        if minimum not in (72, 18) or not name.strip():
            raise ValueError("Укажи название станции и профиль полноты.")
        frame = parse_observations(BytesIO(content), filename)
        frame = frame.drop_duplicates(subset=frame.columns.difference(["source", "csv_line"]))
        conflicts = frame.duplicated("datetime", keep=False) & ~flag_token(frame["qc_flag"], "dup_conflict")
        frame.loc[conflicts, "qc_flag"] = frame.loc[conflicts, "qc_flag"].map(
            lambda flags: f"{flags};dup_conflict" if flags else "dup_conflict"
        )
        self.observations["extra"] = frame.sort_values("datetime", kind="stable")
        self.labels["extra"] = name.strip()
        self.profiles["extra"] = minimum
        return len(frame)


def flag_token(flags, name, *, parameter=False):
    ending = ":" if parameter else "(?:;|$)"
    return flags.str.contains(f"(?:^|;){name}{ending}", regex=True, na=False)


def period_bounds(start, end):
    start, end = pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()
    if end < start:
        raise ValueError("Дата окончания должна быть не раньше даты начала.")
    return start, end


def observation_rows(archive, station, metric, start, end):
    frame = archive.observations[station]
    start, end = period_bounds(start, end)
    in_period = frame["datetime"].ge(start) & frame["datetime"].lt(end + pd.Timedelta(days=1))
    rows = frame.loc[in_period, ["datetime", "qc_flag", "source", "csv_line"]].copy()
    rows["value"] = frame.loc[in_period, metric] if metric in frame else np.nan
    rows["day"] = rows["datetime"].dt.normalize()
    rows["hour"] = rows["datetime"].dt.hour
    rows["conflict"] = flag_token(rows["qc_flag"], "dup_conflict")
    bad_direction = station == "ame60" and metric == "wind_dir"
    blocked = rows["datetime"].dt.year.eq(2021) if bad_direction else False
    rows["metric_flag"] = flag_token(rows["qc_flag"], metric, parameter=True)
    missing = ~np.isfinite(rows["value"])
    rows["status"] = np.select(
        [rows["conflict"], np.broadcast_to(blocked, len(rows)), rows["metric_flag"], missing],
        ["Конфликт записей", "Направление ветра за 2021 год исключено", "Флаг показателя", "Нет значения"],
        default="Пригодный замер",
    )
    rows["valid"] = rows["status"].eq("Пригодный замер")
    return rows


def daily_coverage(rows, minimum, start, end, *, scalar=True):
    calendar = pd.date_range(start, end, freq="D", name="day")
    clean = rows.loc[rows["valid"]].groupby("day")
    station = rows.loc[~rows["conflict"]].groupby("day")
    slots = "datetime" if minimum == 72 else "hour"
    daily = pd.DataFrame(index=calendar)
    daily["rows"] = rows.groupby("day").size().reindex(calendar, fill_value=0)
    daily["valid_readings"] = clean.size().reindex(calendar, fill_value=0)
    daily["station_slots"] = station[slots].nunique().reindex(calendar, fill_value=0)
    daily["metric_slots"] = clean[slots].nunique().reindex(calendar, fill_value=0)
    daily["station_ok"] = daily["station_slots"].ge(minimum)
    daily["metric_ok"] = daily["metric_slots"].ge(minimum)
    if scalar:
        for stat in ("mean", "min", "max"):
            daily[stat] = clean["value"].agg(stat).reindex(calendar).where(daily["metric_ok"])
    return daily


def longest_gap(eligible):
    return max((sum(1 for _ in run) for present, run in groupby(eligible) if not present), default=0)


MONTH_MAX_INCOMPLETE_DAYS = 10
MONTH_MAX_CONSECUTIVE_INCOMPLETE_DAYS = 4


def period_summary(daily, start, end, *, by_month, scalar=True):
    if by_month:
        grouped = daily.groupby(daily.index.to_period("M"))
    else:
        offset = np.minimum((daily.index.day - 1) // 10, 2) * 10
        starts = daily.index.to_period("M").to_timestamp() + pd.to_timedelta(offset, unit="D")
        grouped = daily.groupby(starts)
    result = []
    for _, group in grouped:
        first, last = group.index[0], group.index[-1]
        if last < start or first > end:
            continue
        assumed_days = len(group)
        metric_days = int(group["metric_ok"].sum())
        station_days = int(group["station_ok"].sum())
        gap = longest_gap(group["metric_ok"])
        station_gap = longest_gap(group["station_ok"])
        full_selection = start <= first and end >= last
        month_ok = assumed_days - metric_days <= MONTH_MAX_INCOMPLETE_DAYS and gap <= MONTH_MAX_CONSECUTIVE_INCOMPLETE_DAYS
        station_month_ok = assumed_days - station_days <= MONTH_MAX_INCOMPLETE_DAYS and station_gap <= MONTH_MAX_CONSECUTIVE_INCOMPLETE_DAYS
        eligible = bool(metric_days and full_selection and (month_ok or not by_month))
        result.append({
            "start": first, "end": last, "days": assumed_days, "station_days": station_days,
            "metric_days": metric_days, "gap": gap, "eligible": eligible,
            "station_month_ok": station_month_ok, "full_selection": full_selection,
            "mean": group["mean"].mean() if eligible and scalar else np.nan,
        })
    return pd.DataFrame(result)


def analyze(archive, station, metric, start, end):
    start, end = period_bounds(start, end)
    first_month = start.to_period("M").start_time
    last_month = end.to_period("M").end_time.normalize()
    month_rows = observation_rows(archive, station, metric, first_month, last_month)
    scalar = metric != "wind_dir"
    full_days = daily_coverage(month_rows, archive.profiles[station], first_month, last_month, scalar=scalar)
    return (
        month_rows.loc[month_rows["day"].between(start, end)],
        full_days.loc[start:end],
        period_summary(full_days, start, end, by_month=True, scalar=scalar),
        period_summary(full_days, start, end, by_month=False, scalar=scalar),
    )


def reference_coverage(archive):
    actual = []
    for station in STATIONS:
        reference = archive.reference.loc[archive.reference["station"].str.lower().eq(station)]
        first, last = int(reference["year"].min()), int(reference["year"].max())
        _, _, monthly, _ = analyze(archive, station, "t_air", f"{first}-01-01", f"{last}-12-31")
        actual.append(monthly.assign(station=station, year=monthly.start.dt.year, month=monthly.start.dt.month))
    combined = pd.concat(actual, ignore_index=True)
    expected = archive.reference.assign(station=archive.reference["station"].str.lower())
    checked = expected.merge(combined, on=["station", "year", "month"], validate="one_to_one", how="left")
    matches = checked["valid_days"].eq(checked["station_days"]) & checked["month_valid"].eq(checked["station_month_ok"])
    return checked, checked.loc[~matches]


def compare_temperature(archive, year, month):
    start, end = f"{year}-01-01", f"{year}-12-31"
    kr1, kr2 = (analyze(archive, name, "t_air", start, end) for name in ("kr1", "kr2"))
    combined = pd.concat([kr1[1]["mean"].rename("clearing"), kr2[1]["mean"].rename("field")], axis=1)
    available_months = kr1[2].loc[kr1[2]["eligible"], "start"].dt.month
    available_months = set(available_months) & set(kr2[2].loc[kr2[2]["eligible"], "start"].dt.month)
    combined["delta"] = combined["field"] - combined["clearing"]
    monthly = combined["delta"].resample("MS").agg(["mean", "count"])
    monthly["eligible"] = monthly.index.month.isin(available_months)
    monthly["mean"] = monthly["mean"].where(monthly["eligible"])
    paired = combined.loc[combined.index.month == month].dropna()

    def per_hour(rows):
        return rows.loc[rows["valid"] & rows["day"].isin(paired.index)].groupby(["day", "hour"])["value"].mean()

    by_hour = pd.concat([per_hour(kr1[0]).rename("clearing"), per_hour(kr2[0]).rename("field")], axis=1).dropna()
    by_hour["delta"] = by_hour["field"] - by_hour["clearing"]
    hourly = by_hour.groupby("hour").agg(
        clearing=("clearing", "mean"), field=("field", "mean"), delta=("delta", "mean"), pairs=("delta", "size"),
    ).reindex(range(24))
    return monthly, paired, hourly


def historical_context(archive, station, metric, month, first, last):
    if metric == "wind_dir":
        raise ValueError("Для направления ветра смотри распределение по секторам.")
    if first > last:
        raise ValueError("Последний год должен быть не раньше первого.")
    _, _, monthly, _ = analyze(archive, station, metric, f"{first}-01-01", f"{last}-12-31")
    result = monthly.loc[monthly["start"].dt.month.eq(month)].copy()
    result["year"] = result["start"].dt.year
    mean = result["mean"].mean()
    result["difference"] = result["mean"] - mean
    available = result.dropna(subset=["mean"])
    result["trend"] = np.nan
    slope = None
    if len(available) >= 3:
        x = available["year"] - first
        slope, intercept = np.polyfit(x, available["mean"], 1)
        result.loc[available.index, "trend"] = slope * x + intercept
    return result, mean, len(available), None if slope is None else 10 * slope
