import csv
import gzip
import json
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
from io import BytesIO, StringIO
from pathlib import PurePosixPath
from zipfile import BadZipFile, ZipFile
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from django.db import transaction
from django.utils import timezone

from eda.core import METRICS, MeteoArchive, parse_observations

from .models import DashboardBinding, Dataset, ImportDraft, SavedMapping, Station


SCHEMA = "observations-v1"
QC_VERSION = "case-qc-v1"
ALIASES = {
    "datetime": ("datetime", "timestamp", "date_time", "дата и время", "дата_время", "время"),
    "t_air": ("t_air", "temperature", "temperature_2m", "температура", "температура воздуха", "t"),
    "rh": ("rh", "humidity", "relative_humidity_2m", "влажность"),
    "wind_speed": ("wind_speed", "wind_speed_10m", "скорость ветра"),
    "wind_gust": ("wind_gust", "порывы ветра"),
    "wind_dir": ("wind_dir", "wind_direction", "направление ветра"),
    "soil_05": ("soil_05", "температура почвы 5 см"),
    "soil_10": ("soil_10", "температура почвы 10 см"),
    "soil_20": ("soil_20", "температура почвы 20 см"),
    "pressure_hpa": ("pressure_hpa", "pressure", "surface_pressure", "давление"),
    "qc_flag": ("qc_flag", "flag", "флаг"),
}
UNIT_OPTIONS = {metric: (unit,) for metric, (_, unit) in METRICS.items()}
for metric in ("t_air", "soil_05", "soil_10", "soil_20"):
    UNIT_OPTIONS[metric] = ("°C", "°F", "K")
UNIT_OPTIONS.update(pressure_hpa=("гПа", "Па", "кПа"), wind_speed=("м/с", "км/ч"), wind_gust=("м/с", "км/ч"))
RANGES = {"t_air": (-90, 65), "rh": (0, 100), "wind_speed": (0, 150), "wind_gust": (0, 150),
          "wind_dir": (0, 360), "pressure_hpa": (300, 1100)}
STATION_NAMES = {"ame60": "АМЕ-60", "kr1": "Сокол М1 Кр1 · Поляна", "kr2": "Сокол М1 Кр2 · Поле"}
WHOLE_FILE = "__all__"


class ImportProblem(ValueError):
    pass


@dataclass
class Table:
    name: str
    frame: pd.DataFrame
    encoding: str = ""
    delimiter: str = ""
    station_code: str = ""


def _zip_members(contents):
    try:
        archive = ZipFile(BytesIO(contents))
    except BadZipFile as error:
        raise ImportProblem("Файл ZIP или XLSX повреждён.") from error
    entries = [entry for entry in archive.infolist() if not entry.is_dir()]
    if len(entries) > 400 or sum(entry.file_size for entry in entries) > 120 * 1024 * 1024:
        archive.close()
        raise ImportProblem("Архив превышает допустимый объём распакованных данных.")
    return archive, entries


def _csv_table(contents, name, station_code=""):
    try:
        source = contents.decode("utf-8-sig")
        encoding = "UTF-8"
    except UnicodeDecodeError:
        try:
            source = contents.decode("cp1251")
        except UnicodeDecodeError as error:
            raise ImportProblem("Сохрани CSV в UTF-8 или Windows-1251.") from error
        encoding = "Windows-1251"
    try:
        delimiter = csv.Sniffer().sniff(source[:4096], delimiters=",;\t").delimiter
    except csv.Error:
        delimiter = ","
    frame = pd.read_csv(StringIO(source), sep=delimiter, dtype=object, keep_default_na=False)
    return Table(name, frame, encoding, delimiter, station_code)


def load_tables(filename, contents):
    extension = PurePosixPath(filename.lower()).suffix
    try:
        if extension == ".csv":
            return [_csv_table(contents, filename)]
        if extension == ".json":
            raw = json.loads(contents)
            if isinstance(raw, dict):
                raw = raw.get("records")
            if not isinstance(raw, list) or not raw or not all(isinstance(row, dict) for row in raw):
                raise ImportProblem("JSON должен содержать список записей или поле records со списком.")
            return [Table(filename, pd.DataFrame(raw), "UTF-8")]
        if extension == ".xlsx":
            archive, _ = _zip_members(contents)
            archive.close()
            with pd.ExcelFile(BytesIO(contents), engine="openpyxl") as workbook:
                return [Table(sheet, workbook.parse(sheet, dtype=object, keep_default_na=False)) for sheet in workbook.sheet_names]
        if extension == ".zip":
            archive, entries = _zip_members(contents)
            with archive:
                tables = []
                for entry in entries:
                    path = PurePosixPath(entry.filename)
                    if path.suffix.lower() != ".csv" or path.name == "coverage_by_month.csv" or path.name.startswith("._"):
                        continue
                    code = path.stem.rsplit("_", 1)[0] if path.parent.name in ("ame60", "sokol_m1") else ""
                    tables.append(_csv_table(archive.read(entry), str(path), code if code in STATION_NAMES else ""))
            if tables:
                return tables
            raise ImportProblem("В архиве нет CSV с наблюдениями.")
    except ImportProblem:
        raise
    except (ValueError, KeyError, OSError, TypeError, BadZipFile, RuntimeError) as error:
        raise ImportProblem("Не удалось прочитать таблицу. Проверь структуру и формат файла.") from error
    raise ImportProblem("Загрузи файл CSV, XLSX, JSON или ZIP с таблицами CSV.")


def guesses(tables):
    columns = list(dict.fromkeys(str(column) for table in tables for column in table.frame.columns))
    candidates = {key: [column for column in columns if column.strip().casefold() in aliases] for key, aliases in ALIASES.items()}
    return columns, candidates


def append_flag(flags, mask, flag):
    if mask.any():
        current = flags.loc[mask]
        flags.loc[mask] = current.where(current.eq(""), current + ";") + flag


def normalized_table(table, columns, units, time_zone):
    raw = table.frame
    if raw.empty or columns.get("datetime") not in raw:
        raise ImportProblem(f"В «{table.name}» отсутствуют записи или колонка времени.")
    original = raw[columns["datetime"]].astype(str).str.strip()
    offsets = original.str.extract(r"(?:T|\s)\d\d:\d\d(?:[:.\d]+)?(?:\s?(Z|[+-]\d\d:?\d\d))$", expand=False)
    naive = original.where(offsets.isna(), original.str.replace(r"\s?(?:Z|[+-]\d\d:?\d\d)$", "", regex=True))
    clock = pd.to_datetime(naive, format="mixed", errors="coerce")
    if clock.isna().any() or not pd.api.types.is_datetime64_any_dtype(clock):
        raise ImportProblem(f"В «{table.name}» есть непрочитанные даты. Используй даты ISO с часами и минутами.")
    frame = pd.DataFrame({"datetime": clock, "original_datetime": original, "source_timezone": offsets.fillna(time_zone or ""),
                          "source": table.name, "csv_line": np.arange(2, len(raw) + 2)})
    qc = columns.get("qc_flag")
    frame["qc_flag"] = raw[qc].fillna("").astype(str).str.strip() if qc in raw else ""
    for metric in METRICS:
        column = columns.get(metric)
        if column not in raw:
            continue
        text = raw[column].astype("string").fillna("").str.strip().str.replace(",", ".", regex=False)
        value = pd.to_numeric(text.where(text.ne("")), errors="coerce")
        invalid = text.ne("") & ~np.isfinite(value)
        frame["original_" + metric] = text
        frame["present_" + metric] = True
        frame[metric] = value.where(~invalid).astype(float)
        flags = frame["qc_flag"].copy()
        append_flag(flags, invalid, metric + ":invalid_number")
        unit = units.get(metric, METRICS[metric][1])
        if unit not in UNIT_OPTIONS[metric]:
            raise ImportProblem(f"Неподдерживаемая единица {unit} для {metric}.")
        factor, offset = {"°F": (5 / 9, -32 * 5 / 9), "K": (1, -273.15), "Па": (0.01, 0), "кПа": (10, 0),
                          "км/ч": (1 / 3.6, 0)}.get(unit, (1, 0))
        frame[metric] = frame[metric] * factor + offset
        if metric in RANGES:
            low, high = RANGES[metric]
            append_flag(flags, frame[metric].notna() & ~frame[metric].between(low, high), metric + ":physical_range")
        frame["qc_flag"] = flags
    if not any(metric in frame for metric in METRICS):
        raise ImportProblem(f"Сопоставь хотя бы один показатель из «{table.name}».")
    return frame


def merge_tables(frames):
    frame = pd.concat(frames, ignore_index=True).sort_values("datetime", kind="stable")
    metrics = [metric for metric in METRICS if metric in frame]
    if frame.datetime.duplicated().any():
        dup = frame.datetime.duplicated(keep=False)
        conflicts = frame.loc[dup].groupby("datetime")[metrics].nunique(dropna=False).gt(1).any(axis=1)
        flagged = frame["qc_flag"].str.split(";").map(lambda flags: "dup_conflict" in flags)
        precise = ["datetime", "qc_flag", *metrics]
        frame = frame.loc[~frame.duplicated(precise) | flagged].copy()
        flags = frame["qc_flag"].copy()
        append_flag(flags, frame.datetime.isin(conflicts.index[conflicts]) & ~flagged.reindex(frame.index), "dup_conflict")
        frame["qc_flag"] = flags
    return frame


def confirm_import(draft, plan, preset=""):
    columns, units, zone = plan["columns"], plan["units"], plan.get("time_zone", "").strip()
    if zone:
        try:
            ZoneInfo(zone)
        except (ValueError, KeyError) as error:
            raise ImportProblem("Укажи часовой пояс IANA, например Europe/Moscow.") from error
    mapped = list(columns.values())
    if len(mapped) != len(set(mapped)):
        raise ImportProblem("Одна колонка может быть сопоставлена только одному полю.")
    tables = load_tables(draft.filename, draft.contents)
    if plan["member"] != WHOLE_FILE:
        tables = [table for table in tables if table.name == plan["member"]]
    if not tables:
        raise ImportProblem("Выбранный лист или файл отсутствует.")
    target_stations = list(Station.objects.filter(name__in=STATION_NAMES.values(), location__name='Урочище «Красное»'))
    known_stations = {station.name: station for station in target_stations}
    groups = {}
    for table in tables:
        canonical = STATION_NAMES.get(table.station_code)
        if canonical and canonical not in known_stations:
            raise ImportProblem("Подготовь каталог станций командой seed_catalog.")
        target = known_stations[canonical] if canonical else draft.station
        groups.setdefault(target, []).append(normalized_table(table, columns, units, zone))
    digest = sha256(draft.contents).hexdigest()
    plan_digest = sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    versions = []
    with transaction.atomic():
        for station, frames in groups.items():
            frame = merge_tables(frames)
            payload = gzip.compress(frame.to_csv(index=False).encode(), compresslevel=4)
            dataset, _ = Dataset.objects.defer("contents", "normalization").get_or_create(
                station=station, uploaded_by=draft.researcher, sha256=digest, mapping_hash=plan_digest, defaults={
                    "original_name": draft.filename, "contents": draft.contents,
                    "normalization": payload, "profile": station.profile, "row_count": len(frame),
                    "first_observation": frame.datetime.min().date(), "last_observation": frame.datetime.max().date(),
                    "metrics": [metric for metric in METRICS if metric in frame],
                    "source_kind": plan.get("source_kind", Dataset.Source.OBSERVED),
                    "source_meta": {**plan.get("meta", {}), "schema": SCHEMA, "qc": QC_VERSION, "mapping": plan,
                                    "identical_duplicates": sum(len(frame) for frame in frames) - len(frame)},
                },
            )
            DashboardBinding.objects.get_or_create(dataset=dataset, defaults={"uid": f"meteo-{dataset.version.hex}"})
            versions.append(dataset)
        if preset:
            SavedMapping.objects.update_or_create(researcher=draft.researcher, name=preset, defaults={"mapping": plan})
    return versions


def dataset_frame(dataset):
    if dataset.normalization is None:
        frame = parse_observations(BytesIO(dataset.contents), dataset.original_name)
        frame["original_datetime"] = frame.datetime.dt.strftime("%Y-%m-%d %H:%M")
        frame["source_timezone"] = ""
        return frame
    with gzip.GzipFile(fileobj=BytesIO(dataset.normalization)) as compressed:
        frame = pd.read_csv(compressed, dtype={"original_datetime": str, "source_timezone": str, "qc_flag": str}, low_memory=False)
    frame["datetime"] = pd.to_datetime(frame["datetime"], format="mixed")
    frame["qc_flag"] = frame["qc_flag"].fillna("").astype(str)
    return frame


def create_draft(user, station, filename, contents):
    ImportDraft.objects.filter(researcher=user, created_at__lt=timezone.now() - timedelta(hours=24)).delete()
    return ImportDraft.objects.create(researcher=user, station=station, filename=filename, contents=contents)


def archive_for(dataset, frame):
    code = "ame60" if dataset.station.name == STATION_NAMES["ame60"] else "dataset"
    archive = MeteoArchive({code: frame}, None)
    archive.profiles[code] = dataset.profile or dataset.station.profile
    return archive, code
