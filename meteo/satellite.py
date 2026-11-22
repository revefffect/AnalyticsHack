"""Links to NASA preprocessed imagery and analysis-ready products by area and time."""
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

GIBS = 'https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi'
WORLDVIEW = 'https://worldview.earthdata.nasa.gov/'
CMR = 'https://cmr.earthdata.nasa.gov/search/granules.umm_json'
AREAS = {
    'reserve': ('Заповедник и урочище Красное', [32.70, 56.30, 33.15, 56.56]),
    'west': ('Западные окрестности', [32.15, 56.25, 32.70, 56.58]),
    'east': ('Восточные окрестности', [33.15, 56.30, 33.72, 56.62]),
    'region': ('Заповедник и соседние районы', [32.15, 56.12, 33.72, 56.92]),
}
LAYERS = {
    'truecolor': {
        'name': 'Снимок в естественных цветах', 'layer': 'VIIRS_SNPP_CorrectedReflectance_TrueColor',
        'unit': 'RGB', 'cadence': 'Суточная мозаика',
        'description': 'Облачность и поверхность. Новые кадры появляются после пролёта и обработки.',
    },
    'surface': {
        'name': 'Температура поверхности', 'layer': 'MODIS_Terra_Land_Surface_Temp_Day',
        'unit': 'K', 'cadence': 'Суточный продукт от 1 км',
        'description': 'Температура поверхности по MODIS. Облачность оставляет пропуски. Станция измеряет температуру воздуха.',
        'product': 'MOD11A1', 'version': '061',
    },
    'vegetation': {
        'name': 'Состояние растительности · NDVI', 'layer': 'VIIRS_NOAA20_NDVI_8Day',
        'unit': 'безразмерный', 'cadence': 'Восьмидневный композит',
        'description': 'Снижение индекса может отражать сезонность, облака или стресс растений. Сравнивайте с тем же сезоном.',
    },
    'hotspots': {
        'name': 'Тепловые аномалии · FIRMS', 'layer': 'VIIRS_NOAA20_Thermal_Anomalies_375m_All',
        'unit': 'точки', 'cadence': 'Суточный слой, 375 м',
        'description': 'Спутниковые детекции тепловых аномалий требуют проверки. Пустой слой возможен при отсутствии детекций или обзора.',
    },
}
REFERENCE_PRODUCTS = {
    'soil_moisture': {'name': 'SMAP · влажность верхнего слоя почвы', 'product': 'SPL3SMP_E',
                      'resolution': '9 км, суточный', 'lookback': 7,
                      'url': 'https://nsidc.org/data/spl3smp_e'},
    'vegetation': {'name': 'MODIS · NDVI / EVI', 'product': 'MOD13Q1', 'version': '061',
                   'resolution': '250 м, 16 дней', 'lookback': 32,
                   'url': 'https://ladsweb.modaps.eosdis.nasa.gov/missions-and-measurements/products/MOD13Q1/'},
    'spectral': {'name': 'Sentinel-2 MSI L2A · спектральные каналы и маска облаков',
                 'resolution': '10–20 м для нужных каналов', 'url': 'https://browser.dataspace.copernicus.eu/'},
}


def options():
    return {'areas': {name: {'name': title, 'bbox': bbox} for name, (title, bbox) in AREAS.items()},
            'layers': {name: {k: info[k] for k in ('name', 'unit', 'cadence', 'description')}
                       for name, info in LAYERS.items()},
            'today': datetime.now(timezone.utc).date().isoformat()}


def cmr_search(product, extent, day, lookback, version=None):
    args = {'short_name': product, 'bounding_box': extent, 'page_size': 10,
            'temporal': f'{day - timedelta(days=lookback)}T00:00:00Z,{day}T23:59:59Z'}
    if version:
        args['version'] = version
    return CMR + '?' + urlencode(args)


def imagery(area, layer, day):
    today = datetime.now(timezone.utc).date()
    if area not in AREAS or layer not in LAYERS or not date(2012, 1, 1) <= day <= today:
        raise ValueError('Выберите область, спутниковый слой и дату вплоть до сегодняшнего дня по UTC')
    title, bbox = AREAS[area]
    extent = ','.join(map(str, bbox))
    metadata = LAYERS[layer]
    base = LAYERS['truecolor']['layer']
    layer_id = metadata['layer']
    layers = ','.join([base, layer_id]) if layer == 'hotspots' else layer_id
    # Worldview lists the topmost layer first. WMS draws the first layer at the bottom.
    world_layers = ','.join([layer_id, base]) if layer == 'hotspots' else layer_id
    west, south, east, north = bbox
    url = GIBS + '?' + urlencode({
        'SERVICE': 'WMS', 'VERSION': '1.3.0', 'REQUEST': 'GetMap',
        'CRS': 'EPSG:4326', 'BBOX': f'{south},{west},{north},{east}', 'WIDTH': 1000, 'HEIGHT': 580,
        'LAYERS': layers, 'STYLES': '', 'FORMAT': 'image/png', 'TIME': f'{day}', 'TRANSPARENT': 'false',
    })
    products = []
    for data in REFERENCE_PRODUCTS.values():
        item = {**data}
        if data.get('product'):
            item['granules'] = cmr_search(data['product'], extent, day, data['lookback'], data.get('version'))
        products.append(item)
    if layer == 'surface':
        products.insert(0, {'name': 'MODIS MOD11A1 · значения и пиксельный QC', 'resolution': '1 км, суточный',
                            'granules': cmr_search('MOD11A1', extent, day, 3, '061'),
                            'url': 'https://ladsweb.modaps.eosdis.nasa.gov/missions-and-measurements/products/MOD11A1/'})
    return {
        'area': title, 'bbox': bbox, 'date': str(day), 'date_timezone': 'UTC', 'layer': layer,
        'product': metadata, 'image_url': url, 'worldview': WORLDVIEW + '?' +
        urlencode({'v': extent, 'l': world_layers, 't': f'{day}T00:00:00Z'}),
        'firms': 'https://firms.modaps.eosdis.nasa.gov/map/',
        'attribution': 'NASA EOSDIS Global Imagery Browse Services (GIBS), NASA Earthdata',
        'reference_products': products,
    }
