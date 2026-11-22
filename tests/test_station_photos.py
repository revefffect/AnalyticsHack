from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from meteo.api import create_app
from meteo.station_photos import MAX_BYTES, station_photo_url
from meteo.store import Store


ACCOUNTS = {
    'owner': {'role': 'research', 'token': 'owner-token'},
    'peer': {'role': 'research', 'token': 'peer-token'},
    'reader': {'role': 'business', 'token': 'reader-token'},
}


def auth(who):
    return {'Authorization': 'Bearer ' + ACCOUNTS[who]['token']}


def photo(fmt, color='green'):
    stream = BytesIO()
    metadata = Image.Exif()
    metadata[315] = 'metadata must be removed'
    Image.new('RGB', (32, 24), color).save(stream, fmt, exif=metadata)
    return stream.getvalue()


class StationPhotos(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.client = TestClient(create_app(self.temp.name, accounts=ACCOUNTS, demo=False))
        self.addCleanup(self.client.close)
        upload = self.client.post('/api/uploads', params={'filename': 'KR1.csv'},
                                  content='datetime,t_air\n2024-04-01 01:12,2', headers=auth('owner'))
        imported = self.client.post('/api/imports/' + upload.json()['hash'], json={}, headers=auth('owner'))
        self.version = imported.json()['id']
        self.url = station_photo_url(self.version, 'KR1')

    def put(self, data, media_type='image/png', who='owner', url=None, credit='Полевой журнал'):
        return self.client.put(url or self.url, params={'credit': credit}, content=data,
                               headers={**auth(who), 'Content-Type': media_type})

    def set_shared(self, shared):
        result = self.client.put('/api/datasets/' + self.version + '/visibility',
                                 params={'shared': shared}, headers=auth('owner'))
        self.assertEqual(result.status_code, 200, result.text)

    def test_photo_permission_tracks_source_dataset(self):
        self.assertEqual(self.client.get(self.url, headers=auth('owner')).status_code, 404)
        response = self.put(photo('PNG'))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {'photo_url': self.url, 'photo_credit': 'Полевой журнал'})
        self.assertEqual(self.client.get(self.url).status_code, 401)
        self.assertEqual(self.client.get(self.url, headers=auth('reader')).status_code, 404)
        self.set_shared(True)
        card = self.client.get('/api/stations', headers=auth('reader')).json()[0]
        self.assertEqual((card['photo_url'], card['photo_credit']), (self.url, 'Полевой журнал'))
        self.assertEqual(card['datasets'][0]['photo_url'], self.url)
        self.assertIsNone(card['datasets'][0]['photo_upload_url'])
        owner_card = self.client.get('/api/stations', headers=auth('owner')).json()[0]
        self.assertEqual(owner_card['datasets'][0]['photo_upload_url'], self.url)
        visible = self.client.get(self.url, headers=auth('reader'))
        self.assertEqual(visible.status_code, 200)
        self.assertEqual(visible.headers['Content-Type'], 'image/jpeg')
        self.assertIn('no-store', visible.headers['Cache-Control'])
        with Image.open(BytesIO(visible.content)) as image:
            self.assertEqual(image.size, (32, 24))
            self.assertFalse(image.getexif())
        self.assertEqual(self.put(photo('PNG'), who='peer').status_code, 403)
        self.assertEqual(self.put(photo('PNG'), who='reader').status_code, 403)
        self.set_shared(False)
        self.assertEqual(self.client.get(self.url, headers=auth('reader')).status_code, 404)
        self.assertEqual(self.client.get('/api/stations', headers=auth('reader')).json(), [])
        with TestClient(create_app(self.temp.name, accounts=ACCOUNTS, demo=False)) as restarted:
            self.assertEqual(restarted.get(self.url, headers=auth('owner')).content, visible.content)

    def test_rejects_unknown_station_unsupported_format_and_invalid_image(self):
        self.assertEqual(self.put(photo('PNG'), url=station_photo_url(self.version, 'KR2')).status_code, 404)
        self.assertEqual(self.put(b'<svg/>', 'image/svg+xml').status_code, 415)
        self.assertEqual(self.put(b'<svg/>').status_code, 422)
        self.assertEqual(self.put(photo('PNG'), 'image/jpeg').status_code, 422)
        self.assertEqual(self.put(photo('PNG')[:30]).status_code, 422)
        self.assertEqual(self.put(b'x' * (MAX_BYTES + 1)).status_code, 413)
        with patch('meteo.station_photos.MAX_PIXELS', 100):
            self.assertEqual(self.put(photo('PNG')).status_code, 413)
        self.assertEqual(self.client.get(self.url, headers=auth('owner')).status_code, 404)

    def test_concurrent_replacements_are_atomic(self):
        candidates = [('JPEG', 'image/jpeg', 'blue'), ('WEBP', 'image/webp', 'red')]

        def replace(candidate):
            fmt, mime, color = candidate
            return self.put(photo(fmt, color), mime, credit=color)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(replace, candidates))
        for response in results:
            self.assertEqual(response.status_code, 200, response.text)
        current = self.client.get(self.url, headers=auth('owner'))
        self.assertEqual(current.status_code, 200)
        with Image.open(BytesIO(current.content)) as image:
            red, _, blue = image.getpixel((0, 0))
        with Store(self.temp.name).connect() as db:
            credits = [row['credit'] for row in db.execute('SELECT credit FROM station_photos')]
        self.assertEqual(credits, ['red' if red > blue else 'blue'])
