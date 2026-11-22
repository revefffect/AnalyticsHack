from io import BytesIO
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient
from pypdf import PdfReader

from meteo.api import create_app
from meteo.importer import canonical, import_upload
from meteo.store import Store
from tools.build_station_demo import archive_slice


class DecisionReportAPI(unittest.TestCase):
    def test_business_reader_pdf_uses_frozen_expert_evidence_and_live_acl(self):
        with TemporaryDirectory() as directory:
            accounts = {'expert': {'role': 'research', 'token': 'expert-test'},
                        'manager': {'role': 'business', 'token': 'manager-test'}}
            filters = {'station': 'KR1', 'parameter': 't_air', 'start': '2024-04-01', 'end': '2024-04-02'}
            raw = archive_slice(['sokol_m1/kr1_2024.csv'], filters['start'], filters['end'])
            store = Store(directory)
            digest = store.save_blob('expert', 'archive.zip', raw)
            version = import_upload(store, 'expert', digest)['id']
            base = '/api/datasets/' + version
            with TestClient(create_app(directory, accounts=accounts, demo=False)) as client:
                def request(user, method, path, **kwargs):
                    return client.request(method, path, headers={'Authorization': 'Bearer ' + accounts[user]['token']},
                                          **kwargs)

                member = base + '/members/manager'
                self.assertEqual(request('expert', 'PUT', member, json={'role': 'reader'}).status_code, 200)
                mark = {**filters, 'label': 'weather_event', 'note': 'Проверено на поляне'}
                self.assertEqual(request('expert', 'POST', base + '/annotations', json=mark).status_code, 201)
                response = request('manager', 'POST', base + '/reports',
                                   json={**filters, 'step': 'day', 'title': 'Сводка для смены'})
                self.assertEqual(response.status_code, 200, response.text)
                report = response.json()
                report_path = '/api/reports/' + report['id']
                self.assertEqual([row['note'] for row in report['annotations']], [mark['note']])
                later_note = 'Добавлено позднее'
                self.assertEqual(request('expert', 'POST', base + '/annotations',
                                         json={**mark, 'note': later_note}).status_code, 201)
                self.assertEqual(request('manager', 'GET', report_path).json(), report)

                pdf = request('manager', 'GET', report_path + '/pdf')
                self.assertEqual(pdf.status_code, 200, pdf.text[:200] if pdf.status_code != 200 else '')
                self.assertEqual(pdf.headers['Content-Type'], 'application/pdf')
                self.assertIn('attachment', pdf.headers['Content-Disposition'])
                text = ' '.join(' '.join(page.extract_text().split()) for page in PdfReader(BytesIO(pdf.content)).pages)
                self.assertIn('Сводка для смены', text)
                self.assertIn(mark['note'], text)
                self.assertNotIn(later_note, text)
                self.assertEqual(request('expert', 'GET', report_path + '/pdf').status_code, 404)

                # Pre-analysis snapshots contain only the original immutable series.
                legacy = {key: value for key, value in report.items() if key not in ('insights', 'annotations')}
                legacy['id'] = 'legacy-report'
                with store.connect(write=True) as db:
                    db.execute('INSERT INTO reports(id,owner,dataset,body) VALUES(?,?,?,?)',
                               (legacy['id'], 'manager', version, canonical(legacy)))
                old = request('manager', 'GET', '/api/reports/legacy-report/pdf')
                self.assertEqual(old.status_code, 200)
                older_text = ' '.join(page.extract_text() for page in PdfReader(BytesIO(old.content)).pages)
                self.assertIn('Сводка для смены', older_text)
                self.assertNotIn(mark['note'], older_text)
                self.assertNotIn(later_note, older_text)

                self.assertEqual(request('expert', 'DELETE', member).status_code, 204)
                self.assertEqual(request('manager', 'GET', report_path + '/pdf').status_code, 404)
                self.assertEqual(request('manager', 'GET', '/api/reports/legacy-report/pdf').status_code, 404)
