from io import BytesIO
import unittest

from pypdf import PdfReader

from meteo.business_report import business_report_pdf
from meteo.decision_insights import decision_insights


class BusinessReportTest(unittest.TestCase):
    def test_cyrillic_text_snapshot_mark_and_explicit_abstention(self):
        report = {'id': 'saved-1', 'dataset': 'archive-version', 'station': 'KR1', 'parameter': 'pressure_hpa',
                  'unit': 'гПа', 'title': 'Проверка давления', 'name': 'Архив с поляны',
                  'step': 'month', 'start': '2024-04-01', 'end': '2024-04-30', 'source': 'upload',
                  'qc': 'qc-release', 'coverage': {'valid_days': 2, 'days': 30, 'valid_periods': 0, 'periods': 1},
                  'points': [{'start': '2024-04-01', 'end': '2024-04-30', 'value': None, 'eligible': False}]}
        mark = {'start': report['start'], 'end': report['end'], 'label': 'sensor_issue', 'note': 'Проверен журнал.',
                'author': 'Анна'}
        insights = decision_insights(report)
        blob = business_report_pdf(report, insights, [mark])
        reader = PdfReader(BytesIO(blob))
        self.assertGreaterEqual(len(reader.pages), 2)
        text = ' '.join(' '.join(p.extract_text().split()) for p in reader.pages)
        for expected in ('Проверка давления', 'Анна', 'Проверен журнал.', 'Прибор', '2 из 30',
                         'archive-version', 'qc-release', 'исходных измерений'):
            self.assertIn(expected, text)
        self.assertIn(insights['automl']['reason'], text)
        self.assertEqual(blob, business_report_pdf(report, insights, [mark]))
