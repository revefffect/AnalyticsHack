"""Station cards built from the observations visible to the current account."""
from meteo.access import READABLE_DATASETS
from meteo.importer import STATIONS
from meteo.station_photos import station_photo_url


RESERVE_AREA = 'Урочище Красное, Центрально-Лесной заповедник'
DESCRIPTIONS = {
    'AME60': 'Исторический ряд АМЕ-60 в урочище Красное. Плановый интервал наблюдений — 15 минут.',
    'KR1': 'Сокол М1 на поляне в урочище Красное. Параллельные наблюдения на поле ведёт Кр2.',
    'KR2': 'Сокол М1 на поле в урочище Красное. Параллельные наблюдения на поляне ведёт Кр1.',
}

# Coordinates and publication periods are from the individual FLUXNET site records.
REFERENCE_SITES = [
    {
        'code': 'RU-Fyo', 'name': 'Фёдоровское',
        'description': 'Башня наблюдений в старовозрастном ельнике. В каталоге ICOS доступны получасовые ряды.',
        'latitude': 56.4615, 'longitude': 32.9221,
        'photo_page_url': 'https://acp.copernicus.org/articles/23/2273/2023/#F1',
        'publications': [
            {'name': 'FLUXNET2015', 'url': 'https://doi.org/10.18140/FLX/1440183', 'period': '1998–2014'},
            {'name': 'ICOS · архивная версия', 'url': 'https://meta.icos-cp.eu/objects/_Sa3_K8KVMUepKJ5wkVBSfIH',
             'period': '1998–2018'},
            {'name': 'ICOS · Fluxnet Product', 'url': 'https://meta.icos-cp.eu/objects/q-l42N313hkrHvz_p_cZm1EB',
             'period': '1998–2025'},
        ],
    },
    {
        'code': 'RU-Fy2', 'name': 'Фёдоровское · сухой ельник',
        'description': 'Лесная площадка Fyodorovskoye dry spruce. Сеть FLUXNET опубликовала ряд потоков метана.',
        'latitude': 56.4476, 'longitude': 32.9019,
        'publications': [
            {'name': 'FLUXNET-CH4', 'url': 'https://doi.org/10.18140/FLX/1669657', 'period': '2015–2018'},
            {'name': 'ICOS · Fluxnet Archive', 'url': 'https://meta.icos-cp.eu/objects/-FEeYNcv7UkMTJ4l72w8CLL9',
             'period': '2015–2025'},
        ],
    },
]


def reference_stations():
    return [{'photo_url': None, 'photo_credit': None, **site,
             'id': 'reference:' + site['code'], 'location_kind': 'reference',
             'location_label': 'Центрально-Лесной заповедник, Тверская область',
             'source_url': 'https://fluxnet.org/sites/siteinfo/' + site['code'],
             'datasets': []} for site in REFERENCE_SITES]


def station_catalog(db, user):
    rows = db.execute(f"""
        WITH visible AS (
            SELECT id, name, can_edit, source, created_at FROM ({READABLE_DATASETS})
            WHERE source IN ('station', 'stream')
        ), series AS (
            SELECT dataset, station, MIN(stamp) AS start, MAX(stamp) AS end, COUNT(*) AS rows
            FROM observations WHERE dataset IN (SELECT id FROM visible)
            GROUP BY dataset, station
        )
        SELECT d.*, s.station, s.start, s.end, s.rows, p.station AS photo_station, p.credit
        FROM series s JOIN visible d ON d.id=s.dataset
        LEFT JOIN station_photos p ON p.dataset=s.dataset AND p.station=s.station
        ORDER BY s.station, d.created_at DESC, d.name, d.id
    """, {'username': user['name']})
    cards = {}
    for row in rows:
        site = row['station']
        if site not in cards:
            known = site in DESCRIPTIONS
            cards[site] = {
                'id': site, 'name': STATIONS.get(site, site),
                'description': DESCRIPTIONS.get(site, 'Станция из подключённого набора наблюдений.'),
                'location_label': RESERVE_AREA if known else 'Местоположение не указано',
                'location_kind': 'area' if known else 'unknown', 'latitude': None, 'longitude': None,
                'photo_url': None, 'photo_credit': None, 'datasets': [],
            }
        card = cards[site]
        dataset = {key: row[key] for key in ('id', 'name', 'source', 'start', 'end', 'rows')}
        dataset['can_upload_photo'] = bool(row['can_edit'])
        photo_path = station_photo_url(row['id'], site)
        dataset['photo_upload_url'] = photo_path if dataset['can_upload_photo'] else None
        dataset['photo_url'] = photo_path if row['photo_station'] is not None else None
        dataset['photo_credit'] = row['credit']
        card['datasets'].append(dataset)
        if dataset['photo_url'] and card['photo_url'] is None:
            card['photo_url'] = dataset['photo_url']
            card['photo_credit'] = row['credit']
    return list(cards.values())
