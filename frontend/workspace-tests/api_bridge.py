"""Serve real HTTP responses over stdio to the workspace DOM integration test."""
import base64
import json
import sys
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from meteo.api import create_app
from meteo.importer import import_upload
from meteo.store import Store
from tools.build_station_demo import archive_slice, SOKOL


with TemporaryDirectory() as directory:
    store = Store(directory)
    payload = archive_slice(SOKOL, '2024-04-01', '2024-04-30')
    digest = store.save_blob('research', 'Кр1 и Кр2.zip', payload)
    import_upload(store, 'research', digest, name='Архив наблюдений Кр1 и Кр2', source='station')
    accounts = {'research': {'token': 'editor-test', 'role': 'research'},
                'business': {'token': 'reader-test', 'role': 'business'}}
    with TestClient(create_app(directory, accounts=accounts, demo=False)) as client:
        for line in sys.stdin:
            msg = json.loads(line)
            try:
                response = client.request(msg['method'], msg['path'], headers=msg['headers'],
                                          content=base64.b64decode(msg['body']))
                result = {'status': response.status_code, 'headers': dict(response.headers),
                          'body': base64.b64encode(response.content).decode()}
            except Exception as error:
                result = {'error': repr(error)}
            print(json.dumps({'id': msg['id'], **result}), flush=True)
