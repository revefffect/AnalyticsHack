"""Open-Meteo adapter. Keep the provider response and normalized rows in one raw document."""
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import os
from urllib.parse import urlencode
from urllib.request import urlopen

from pydantic import BaseModel, Field

from meteo.importer import canonical, import_upload

FIELDS = {'temperature_2m': 't_air', 'relative_humidity_2m': 'rh', 'surface_pressure': 'pressure_hpa',
          'precipitation': 'precipitation'}


class WeatherRequest(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    start: date
    end: date
    kind: str = 'reanalysis'


def open_meteo(store, owner, query, *, fetch=urlopen):
    if query.kind not in {'reanalysis', 'forecast'} or not 0 <= (query.end - query.start).days <= 366:
        raise ValueError('Выберите тип данных и период не более 367 суток')
    key = os.getenv('OPEN_METEO_API_KEY')
    if os.getenv('OPEN_METEO_COMMERCIAL') == '1' and not key:
        raise ValueError('Для коммерческого доступа нужен коммерческий ключ Open-Meteo')
    forecast = query.kind == 'forecast'
    model = 'ecmwf_ifs025' if forecast else 'era5'
    host = ('api' if forecast else 'archive-api') + '.open-meteo.com'
    endpoint = 'https://' + ('customer-' if key else '') + host + ('/v1/forecast' if forecast else '/v1/archive')
    args = {'latitude': query.latitude, 'longitude': query.longitude, 'start_date': query.start,
            'end_date': query.end, 'hourly': ','.join(FIELDS), 'timezone': 'GMT', 'models': model}
    if key:
        args['apikey'] = key
    try:
        with fetch(endpoint + '?' + urlencode(args), timeout=12) as response:
            payload = json.loads(response.read(12 * 1024 * 1024))
        hourly = payload['hourly']
        times = hourly['time']
        if not times or any(len(hourly[name]) != len(times) for name in FIELDS):
            raise ValueError
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ConnectionError('Open-Meteo не вернул ожидаемый ряд. Проверьте сеть, даты и тариф.') from exc
    station = 'GRID-' + sha256(f'{query.latitude},{query.longitude}'.encode()).hexdigest()[:8].upper()
    rows = [{'datetime': time + '+00:00', 'station': station,
             **{canonical_key: hourly[provider_key][i] for provider_key, canonical_key in FIELDS.items()}}
            for i, time in enumerate(times)]
    metadata = {
        'provider': 'Open-Meteo / ECMWF', 'license': 'CC BY 4.0',
        'attribution': 'Weather data by Open-Meteo.com (CC BY 4.0)',
        'territory': f'{query.latitude}, {query.longitude}', 'cadence': '1 час', 'format': 'JSON',
        'type': 'Прогноз' if forecast else 'Реанализ', 'model': model, 'endpoint': endpoint,
        'retrieved_at': datetime.now(timezone.utc).isoformat(), 'requested': query.model_dump(mode='json'),
        'grid': [payload.get('latitude'), payload.get('longitude')],
    }
    filename = 'open-meteo.json'
    digest = store.save_blob(owner, filename, canonical({'observations': rows, 'provider_response': payload}).encode())
    return import_upload(store, owner, digest, name=f'Open-Meteo · {model} · {metadata["territory"]}',
                         source=query.kind, metadata=metadata)
