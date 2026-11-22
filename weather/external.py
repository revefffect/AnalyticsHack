"""External data catalogue and import of an explicitly selected ERA5 grid series."""

from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
from http.client import HTTPException
import json
import math
import os
import ssl
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ARCHIVE_API = "https://archive-api.open-meteo.com/v1/archive"
CUSTOMER_API = "https://customer-archive-api.open-meteo.com/v1/archive"
OPEN_METEO_DOCS = "https://open-meteo.com/en/docs/historical-weather-api"
CC_BY = "https://creativecommons.org/licenses/by/4.0/"
EIP_TERMS = "https://eip.meteo.ru/opendata"

SOURCE_CARDS = [
    {
        "id": "open-meteo-era5", "title": "Температура воздуха по ERA5",
        "provider": "Open-Meteo · ECMWF/Copernicus", "territory": "Глобальная сетка",
        "stations": [], "period": "С 1940 года, с задержкой публикации",
        "parameters": ["t_air"], "resolution": "1 час, исходная сетка 0,25°",
        "format": "JSON", "data_type": "reanalysis", "updated_at": None,
        "completeness": "Вычисляется по выбранному срезу", "version": "ERA5",
        "access": "api", "integration": "connector", "url": OPEN_METEO_DOCS,
        "terms": "Данные CC BY 4.0. Общедоступный API для некоммерческого использования. Для коммерческого архива требуется подписка с доступом к Historical Weather API.",
        "terms_url": "https://open-meteo.com/en/pricing",
        "instructions": "Выберите координаты и даты в UTC. Для коммерческого подключения администратор задаёт ключ Open-Meteo.",
    },
    {
        "id": "rhm-soil320", "title": "Температура почвы на глубинах до 320 см",
        "provider": "Росгидромет", "territory": "По станциям паспорта набора",
        "stations": [], "period": "В паспорте указано продление по 2015 год",
        "parameters": ["Температура почвы на 12 глубинах от 2 до 320 см", "Признак качества по глубине"],
        "resolution": "Дата наблюдения до суток", "format": None,
        "data_type": "station_observations", "updated_at": None, "completeness": None,
        "version": "data-202402051313-structure-202402051313",
        "access": "external_file", "integration": "manual", "url": "https://eip.meteo.ru/opendata/4025000218-soil320",
        "terms": "Условия открытых данных ЕИП, со ссылкой на источник.", "terms_url": EIP_TERMS,
        "instructions": "В паспорте ЕИП откройте набор данных и описание структуры. При недоступной выдаче используйте контакт владельца из паспорта. Для загрузки в платформу требуется файл поддерживаемого формата.",
    },
    {
        "id": "rhm-aerodata", "title": "Радиозондовые наблюдения 12 станций ГСНК",
        "provider": "ФГБУ «ВНИИГМИ-МЦД»", "territory": "12 станций ГСНК",
        "stations": [], "period": None, "parameters": ["Радиозондовые наблюдения"],
        "resolution": "Срочные данные", "format": "CSV", "data_type": "upper_air_observations",
        "updated_at": None, "completeness": None, "version": "data-202402071319-structure-202402071319",
        "access": "external_file", "integration": "manual", "url": "https://eip.meteo.ru/opendata/4025000218-aerodata",
        "terms": "Условия открытых данных ЕИП, со ссылкой на источник.", "terms_url": EIP_TERMS,
        "instructions": "Откройте паспорт ЕИП и раздел набора и структуры. Там также указан каталог ВНИИГМИ-МЦД. Уточните доступный период у владельца. До подготовки профиля аэрологических наблюдений используйте файл вне расчётов приземной температуры.",
    },
]


class ExternalSourceError(RuntimeError):
    """A remote archive is unavailable or returned inconsistent measurements."""


def _coordinate(value, limit):
    return type(value) in (int, float) and math.isfinite(value) and abs(value) <= limit


def fetch_open_meteo(latitude: float, longitude: float, start: str, end: str, *,
                     commercial: bool = False, api_key: str | None = None) -> dict:
    """Fetch one ERA5 interval and retain the authoritative JSON alongside normalized rows."""
    if not _coordinate(latitude, 90) or not _coordinate(longitude, 180):
        raise ValueError("Проверьте широту и долготу точки.")
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except (TypeError, ValueError):
        raise ValueError("Укажите даты в формате ГГГГ-ММ-ДД.") from None
    if first < date(1940, 1, 1) or last < first or last > datetime.now(timezone.utc).date():
        raise ValueError("Для ERA5 укажите прошедший период с 1940 года в порядке возрастания.")
    days = (last - first).days + 1
    if days > 366:
        raise ValueError("Для интерактивного импорта выберите до 366 дней за один запрос.")
    endpoint = CUSTOMER_API if commercial else ARCHIVE_API
    query = {
        "latitude": latitude, "longitude": longitude,
        "start_date": first.isoformat(), "end_date": last.isoformat(),
        "hourly": "temperature_2m", "models": "era5", "timezone": "UTC", "temperature_unit": "celsius",
    }
    source_url = endpoint + "?" + urlencode(query)
    request_url = source_url
    if commercial:
        key = api_key or os.environ.get("OPEN_METEO_API_KEY")
        if not key or not key.strip():
            raise ValueError("Для коммерческого архива нужен ключ подписки Open-Meteo с доступом к историческим данным.")
        request_url += "&" + urlencode({"apikey": key})
    req = Request(request_url, headers={"Accept": "application/json", "User-Agent": "AnalyticsHack/1"})
    tls = None
    if sys.platform == "win32":
        try:
            import truststore
        except ImportError:
            pass
        else:
            tls = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    try:
        with urlopen(req, timeout=20, context=tls) as response:
            raw = response.read(2_000_001)
    except HTTPError as exc:
        raise ExternalSourceError(f"Архив Open-Meteo вернул HTTP {exc.code}.") from None
    except (URLError, OSError, HTTPException):
        raise ExternalSourceError("Соединение с архивом Open-Meteo недоступно.") from None
    if len(raw) > 2_000_000:
        raise ExternalSourceError("Ответ Open-Meteo превышает ожидаемый объём для выбранного периода.")
    try:
        source_response = raw.decode("utf-8")
        data = json.loads(source_response)
        hourly = data["hourly"]
        stamps, values = hourly["time"], hourly["temperature_2m"]
        if data["utc_offset_seconds"] != 0 or data["hourly_units"]["temperature_2m"] != "°C":
            raise ValueError("Unexpected units or offset")
        if not isinstance(stamps, list) or not isinstance(values, list) or len(values) != len(stamps) or len(stamps) != days * 24:
            raise ValueError("Unexpected time axis")
        grid_lat, grid_lon = data["latitude"], data["longitude"]
        if not _coordinate(grid_lat, 90) or not _coordinate(grid_lon, 180):
            raise ValueError("Unexpected grid coordinates")
        elevation = data.get("elevation")
        if elevation is not None and (type(elevation) not in (int, float) or not math.isfinite(elevation)):
            raise ValueError("Unexpected elevation")
        midnight = datetime.combine(first, time.min)
        observations = []
        for index, (stamp, value) in enumerate(zip(stamps, values)):
            expected = midnight + timedelta(hours=index)
            if stamp != expected.isoformat(timespec="minutes"):
                raise ValueError("Unexpected UTC hour")
            if value is not None and (type(value) not in (float, int) or not math.isfinite(value)):
                raise ValueError("Unexpected air temperature")
            observations.append({
                "datetime": expected.isoformat(" ", timespec="minutes"), "t_air": value,
                "timezone": "UTC", "unit": "°C", "qc_flag": "", "source_ref": f"/hourly/temperature_2m/{index}",
            })
    except (TypeError, ValueError, KeyError, OverflowError):
        raise ExternalSourceError("Open-Meteo вернул неполную сетку времени или неожиданный формат данных.") from None
    valid_hours = sum(value is not None for value in values)
    version_content = [query, grid_lat, grid_lon, elevation, values]
    version_bytes = json.dumps(version_content, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
    metadata = {
        "source": "Open-Meteo / ECMWF ERA5", "data_type": "reanalysis", "model": "ERA5",
        "parameter": "t_air", "source_field": "temperature_2m", "unit": "°C", "timezone": "UTC",
        "period": {"start": first.isoformat(), "end": last.isoformat()},
        "requested_coordinates": {"latitude": latitude, "longitude": longitude},
        "grid_coordinates": {"latitude": grid_lat, "longitude": grid_lon},
        "elevation_m": elevation, "elevation_policy": "open_meteo_default_dem_downscaling",
        "interval_seconds": 3600,
        "valid_hours": valid_hours, "expected_hours": days * 24,
        "dataset_url": OPEN_METEO_DOCS, "source_url": source_url,
        "access_mode": "commercial" if commercial else "noncommercial",
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "license": "CC BY 4.0", "license_url": CC_BY,
        "attribution": "Open-Meteo · ERA5, ECMWF/Copernicus Climate Change Service (C3S). Часовые данные, поля и время приведены к формату платформы.",
        "dataset_doi": "https://doi.org/10.24381/cds.adbb2d47",
        "source_sha256": sha256(raw).hexdigest(), "series_sha256": sha256(version_bytes).hexdigest(),
        "schema_version": "open-meteo-era5-hourly-v1",
    }
    return {"metadata": metadata, "observations": observations, "source_response": source_response}
