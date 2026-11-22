import json
import os
import unittest
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from meteo.api import create_app
from meteo.chart_workspace import ChartDraft, ChartSettings


class DashboardAssistantTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        accounts = {name: {'token': name + '-key', 'role': role}
                    for name, role in [('expert', 'research'), ('peer', 'research'), ('manager', 'business')]}
        self.client = TestClient(create_app(folder.name, demo=False, accounts=accounts))
        self.addCleanup(self.client.close)
        self.headers = {'Authorization': 'Bearer expert-key'}
        upload = self.client.post('/api/uploads', headers=self.headers, params={'filename': 'KR1.csv'},
                                  content='datetime,t_air\n2024-04-01 01:00,3\n')
        self.assertEqual(upload.status_code, 200, upload.text)
        version = self.client.post('/api/imports/' + upload.json()['hash'], headers=self.headers, json={})
        self.assertEqual(version.status_code, 200, version.text)
        self.base = '/api/datasets/' + version.json()['id']
        self.selection = {'station': 'KR1', 'parameter': 't_air', 'start': '2024-04-01',
                          'end': '2024-04-01', 'step': 'day'}
        self.request = {'selection': self.selection, 'settings': {},
                        'messages': [{'role': 'user', 'content': 'Оставь только график и таблицу'}]}

    def suggest(self):
        return self.client.post(self.base + '/chart-assistant', headers=self.headers, json=self.request)

    def test_local_session_saves_personal_dashboard_and_honors_revocation(self):
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': '', 'YANDEX_API_KEY': '', 'YANDEX_FOLDER_ID': ''}):
            result = self.suggest()
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['source'], 'local')
            self.assertEqual(result.json()['patch'], {'panels': ['chart', 'table']})
            self.request['messages'][0]['content'] = 'Покажи температуру столбцами, оставь график и таблицу'
            compound = self.suggest()
            self.assertEqual(compound.status_code, 200, compound.text)
            self.assertEqual(compound.json()['patch'], {'kind': 'bar', 'panels': ['chart', 'table']})
            self.request['messages'][0]['content'] = 'Что можно улучшить?'
            question = self.suggest().json()
            self.assertTrue(question['questions'])
            self.assertEqual(question['patch'], {})
        path = self.base + '/chart-workspace'
        self.assertEqual(self.client.get(path, headers=self.headers, params=self.selection).json(),
                         ChartDraft().model_dump(mode='json'))
        save = {'selection': self.selection, 'settings': result.json()['patch']}
        self.assertEqual(self.client.put(path, headers=self.headers, json=save).status_code, 200)
        self.assertEqual(self.client.get(path, headers=self.headers, params=self.selection).json()['settings']['panels'],
                         ['chart', 'table'])
        peer = {'Authorization': 'Bearer peer-key'}
        self.assertEqual(self.client.get(path, headers=peer, params=self.selection).status_code, 404)
        self.client.put(self.base + '/members/peer', headers=self.headers, json={'role': 'reader'}).raise_for_status()
        self.assertEqual(self.client.get(path, headers=peer, params=self.selection).json()['settings'], ChartSettings().model_dump())
        self.client.delete(self.base + '/members/peer', headers=self.headers).raise_for_status()
        self.assertEqual(self.client.post(self.base + '/chart-assistant', headers=peer, json=self.request).status_code, 404)
        manager = {'Authorization': 'Bearer manager-key'}
        self.assertEqual(self.client.post(self.base + '/chart-assistant', headers=manager, json=self.request).status_code, 403)

    def test_direct_and_yandex_openai_compatible_contracts(self):
        cases = [
            ({'DEEPSEEK_API_KEY': 'direct-test', 'DEEPSEEK_MODEL': 'deepseek-flash'}, 'deepseek',
             'https://api.deepseek.com/chat/completions', 'deepseek-flash', 'Bearer direct-test'),
            ({'YANDEX_API_KEY': 'yc-test', 'YANDEX_FOLDER_ID': 'demo', 'DEEPSEEK_API_KEY': ''}, 'yandex',
             'https://ai.api.cloud.yandex.net/v1/chat/completions', 'gpt://demo/deepseek-v4.1-flash', 'Api-Key yc-test'),
        ]
        for env, source, url, model, auth in cases:
            with self.subTest(source=source), patch.dict(os.environ, {'YANDEX_DEEPSEEK_MODEL': '', **env}):
                payload = {'reply': 'Сначала таблица.', 'patch': {'panels': ['table', 'metrics']}, 'questions': []}
                encoded = json.dumps({'choices': [{'message': {'content': json.dumps(payload)}}]}).encode()
                with patch('meteo.chart_workspace.urlopen', return_value=BytesIO(encoded)) as transport:
                    response = self.suggest()
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), {'source': source, **payload})
                sent = transport.call_args.args[0]
                self.assertEqual(sent.full_url, url)
                self.assertEqual(sent.get_header('Authorization'), auth)
                body = json.loads(sent.data)
                self.assertEqual(body['model'], model)
                self.assertEqual(body['messages'][-1], self.request['messages'][-1])
                self.assertNotIn(self.selection['start'], body['messages'][0]['content'])
                self.assertNotIn('expert-key', sent.data.decode())

    def test_rejects_unsupported_patches_on_both_sides(self):
        for panels in ([], ['chart', 'chart'], ['not-a-panel']):
            with self.subTest(panels=panels):
                self.assertEqual(self.client.put(self.base + '/chart-workspace', headers=self.headers,
                    json={'selection': self.selection, 'settings': {'panels': panels}}).status_code, 422)
        invalid = {'reply': 'Настройки', 'questions': [], 'patch': {'panels': ['chart'], 'edits': {}}}
        encoded = json.dumps({'choices': [{'message': {'content': json.dumps(invalid)}}]}).encode()
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'invalid-test'}), \
                patch('meteo.chart_workspace.urlopen', return_value=BytesIO(encoded)):
            self.assertEqual(self.suggest().status_code, 502)
