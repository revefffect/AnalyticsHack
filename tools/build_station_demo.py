"""Build the standalone station walkthrough with the same UI and API responses as the app.

From the repository root run python -m tools.build_station_demo.
"""
import base64
import csv
from datetime import date, timedelta
import gzip
from io import BytesIO, StringIO, TextIOWrapper
import json
import mimetypes
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from zipfile import ZipFile, ZipInfo

from fastapi.testclient import TestClient

from meteo.api import create_app
from meteo.chart_workspace import ChartDraft, ChartSettings, local_guide
from meteo.importer import import_upload
from meteo.store import Store


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'meteo/web'
DESTINATION = ROOT / 'docs/demo/station-atlas.html'
PEOPLE = ['Анна', 'Карина', 'Никита', 'Антон', 'Alex']
ARCHIVE = ROOT / 'data/raw/meteo_hack_dataset.zip'
SOKOL = ['sokol_m1/kr1_2024.csv', 'sokol_m1/kr2_2024.csv']
SAMPLES = [
    ('Температура воздуха · январь 2010', ['ame60/ame60_2010.csv'], '2010-01-01', '2010-01-31'),
    ('Температура воздуха · апрель 2024', SOKOL, '2024-04-01', '2024-04-30'),
    ('Температура воздуха · июль 2024', SOKOL, '2024-07-01', '2024-07-07'),
]


def archive_slice(members, start, end):
    # Retain the timestamps, values and source QC. Keep the case's station filenames for import detection.
    output = BytesIO()
    with ZipFile(ARCHIVE) as archive, ZipFile(output, 'w') as subset:
        for member in members:
            path = 'meteo_clgz_hackathon/data/' + member
            selected = StringIO(newline='')
            writer = csv.DictWriter(selected, ('datetime', 't_air', 'qc_flag'), extrasaction='ignore')
            writer.writeheader()
            with archive.open(path) as raw, TextIOWrapper(raw, encoding='utf-8-sig', newline='') as rows:
                for row in csv.DictReader(rows):
                    if start <= row['datetime'][:10] <= end:
                        writer.writerow(row)
            subset.writestr(ZipInfo(member), selected.getvalue().encode())
    return output.getvalue()


def sampled_responses():
    owner = PEOPLE[0]
    accounts = {name: {'token': f'fixture{i}', 'role': 'research' if i == 0 else 'business'}
                for i, name in enumerate(PEOPLE)}
    with TemporaryDirectory() as directory:
        store = Store(directory)
        uploads = {}
        for name, members, start, end in SAMPLES:
            raw = archive_slice(members, start, end)
            digest = store.save_blob(owner, name + '.zip', raw)
            uploads[name] = (digest, raw)
            import_upload(store, owner, digest, name=name, source='station', metadata={
                'territory': 'Урочище Красное', 'provider': 'Центрально-Лесной заповедник',
                'type': 'Архив наблюдений', 'format': 'ZIP / CSV', 'source_files': members,
                'license': 'Материалы кейса 02 • выборка по температуре',
            })
        with store.connect(write=True) as db:
            db.execute("UPDATE datasets SET created_at = '2024-07-08 12:00:00'")

        with TestClient(create_app(directory, accounts=accounts, demo=False)) as client:
            client.headers['Authorization'] = 'Bearer fixture0'

            def get(url, **kwargs):
                response = client.get(url, **kwargs)
                response.raise_for_status()
                return response.json()

            fixtures = {path: get(path) for path in (
                '/health', '/api/session', '/api/dictionary', '/api/views', '/api/datasets',
                '/api/stations', '/api/stations/references', '/api/catalog', '/api/templates',
                '/api/alerts', '/api/satellite', '/api/reports',
            )}
            satellite = fixtures['/api/satellite']
            reference_day = '2024-04-01'
            satellite['today'] = reference_day
            images = {f'{area}|{layer}': get('/api/satellite/imagery', params={
                'area': area, 'layer': layer, 'day': reference_day})
                for area in satellite['areas'] for layer in satellite['layers']}
            quality, downloads, formats, insights = {}, {}, {}, {}
            report_pdf = None
            preview = None
            series = {}
            for dataset in fixtures['/api/datasets']:
                base = '/api/datasets/' + dataset['id']
                for name, role in zip(PEOPLE[1:3], ('editor', 'reader')):
                    client.put(base + '/members/' + name, json={'role': role}).raise_for_status()
                fixtures[base + '/members'] = get(base + '/members')
                digest, raw = uploads[dataset['name']]
                downloads[base] = base64.b64encode(raw).decode('ascii')
                if dataset['name'] == SAMPLES[2][0]:
                    response = client.post('/api/uploads', params={'filename': dataset['name'] + '.zip'}, content=raw)
                    response.raise_for_status()
                    preview = {'name': dataset['name'] + '.zip', 'response': response.json(), 'dataset': dataset['id']}
                for station in dataset['stations']:
                    day = date.fromisoformat(station['start'][:10])
                    while day.isoformat() <= station['end'][:10]:
                        quality['|'.join((base, station['station'], str(day)))] = get(base + '/quality', params={
                            'station': station['station'], 'parameter': 't_air', 'day': str(day)})
                        day += timedelta(days=1)

                    for step in ('month', 'decade', 'day'):
                        params = {'station': station['station'], 'parameter': 't_air', 'step': step,
                                  'start': station['start'][:10], 'end': station['end'][:10]}
                        key = '|'.join((base + '/series', station['station'], step))
                        series[key] = get(base + '/series', params=params)
                        insights[key] = get(base + '/insights', params=params)
                        csv_response = client.get(base + '/export', params={**params, 'format': 'csv'})
                        csv_response.raise_for_status()
                        formats[key] = csv_response.text
                        if dataset['name'] == SAMPLES[1][0] and params['station'] == 'KR1' and step == 'day':
                            report = client.post(base + '/reports', json={**params,
                                'title': 'Температура на Кр1 за апрель 2024 года',
                                'template': 'Операционная сводка'})
                            report.raise_for_status()
                            # Stabilize this generated fixture before exporting its precise PDF.
                            original_report = report.json()
                            report = {**original_report, 'id': 'archive-summary'}
                            with store.connect(write=True) as db:
                                db.execute("UPDATE reports SET id=?, body=?, created_at=? WHERE id=?",
                                           (report['id'], json.dumps(report, ensure_ascii=False),
                                            '2024-07-08 12:00:00', original_report['id']))
                            fixtures['/api/reports/archive-summary'] = report
                            fixtures['/api/reports'] = [{'id': report['id'], 'title': report['title'],
                                                         'dataset': dataset['id'], 'created_at': '2024-07-08 12:00:00'}]
                            pdf = client.get('/api/reports/archive-summary/pdf')
                            pdf.raise_for_status()
                            assert pdf.content.startswith(b'%PDF-')
                            report_pdf = base64.b64encode(pdf.content).decode('ascii')
    if not report_pdf:
        raise ValueError('April KR1 management report was not generated')
    presets = {kind: local_guide(text, ChartSettings()).model_dump(exclude_none=True)
               for kind, text in {'line': 'покажи линию', 'bar': 'покажи столбцы',
                                  'scatter': 'покажи точки точками', 'histogram': 'покажи гистограмму',
                                  'panels': 'оставь только график и таблицу', 'help': 'уточните'}.items()}
    return {'fixtures': fixtures, 'sampleSeries': series, 'sampleQuality': quality, 'csvExports': formats,
            'sampleInsights': insights, 'reportPdf': report_pdf, 'chartPresets': presets,
            'defaultChart': ChartDraft().model_dump(mode='json'),
            'satelliteLayers': images, 'rawArchives': downloads, 'sampleUpload': preview}


def data_url(name):
    asset = WEB / name
    mime = mimetypes.guess_type(name)[0] or 'application/octet-stream'
    return f'data:{mime};base64,' + base64.b64encode(asset.read_bytes()).decode('ascii')


def inline_css(match):
    sheet = (WEB / match[1]).read_text()
    sheet = re.sub(r"""url\(\s*['"]?/assets/([^)'"]+)['"]?\s*\)""",
                   lambda asset: f'url("{data_url(asset[1])}")', sheet)
    return '<style>\n' + sheet + '\n</style>'


def inline_modules():
    # Compile the named-ESM subset used by our local assets, retaining module scopes
    # and aliases. Vendor bundles have arbitrary ESM syntax and run without rewriting.
    compiled = {}
    import_pattern = r"""^import\s+\{([^}]+)\}\s+from\s+['"]([^'"]+)['"];?"""
    dynamic_pattern = r"""import\(\s*['"](\.[^'"]+)['"]\s*\)"""

    def aliases(items, export=False):
        result = []
        for item in items.split(','):
            names = item.strip().split(' as ')
            if names == ['']:
                continue
            source, destination = names[0], names[-1]
            if source == destination:
                result.append(source)
            else:
                result.append(f'{destination}: {source}' if export else f'{source}: {destination}')
        return result

    def compile_module(path):
        key = path.relative_to(WEB).as_posix()
        slot = f'modules[{json.dumps(key)}]'
        if key in compiled:
            return slot
        if path.parent.name == 'vendor':
            compiled[key] = f'{slot} = await import("{data_url(key)}");'
            return slot
        source = path.read_text()

        def dependency(name):
            return compile_module((path.parent / name).resolve())

        source = re.sub(import_pattern, lambda match:
                        'const {' + ', '.join(aliases(match[1])) + '} = ' + dependency(match[2]) + ';',
                        source, flags=re.M)
        source = re.sub(dynamic_pattern, lambda match: 'Promise.resolve(' + dependency(match[1]) + ')', source)
        exports = re.findall(r'^export (?:async )?(?:function|const|class) (\w+)', source, re.M)
        for block in re.findall(r'^export\s*\{([^}]+)\};?', source, re.M):
            exports.extend(aliases(block, export=True))
        source = re.sub(r'^export\s*\{[^}]+\};?', '', source, flags=re.M)
        source = re.sub(r'^export (?=(?:async )?(?:function|const|class) )', '', source, flags=re.M)
        if re.search(r'^\s*(?:import\s|export\s)', source, re.M):
            raise ValueError(f'Unrecognized ESM syntax in {key}')
        source = source.replace("'/assets/station-avatars.svg#'", "'#'")
        if path.name == 'app.js':
            source = source.replace('new URLSearchParams(location.search)', "new URLSearchParams('page=stations')")
            source = source.replace('location.origin', "'https://preview.test'")
        compiled[key] = f'{slot} = (() => {{\n' + source + '\nreturn {' + ', '.join(exports) + '};\n})();'
        return slot

    compile_module(WEB / 'app.js')
    return compiled.values()


def bundle():
    shell = (WEB / 'index.html').read_text()
    shell = re.sub(r'<link rel="stylesheet" href="/assets/([^"]+)">', inline_css, shell)
    shell = shell.replace('<script type="module" src="/assets/app.js"></script>', '')
    shell = re.sub(r'(<a\b[^>]*class="brand"[^>]*href=")/"', r'\1#workspace"', shell)
    shell = re.sub(r'(<button\b[^>]*id="logout"[^>]*>)',
                   '<small class="demo-data-label">Автономное демо</small>'
                   + r'\1', shell).replace('id="logout"', 'hidden id="logout"')
    sprite = (WEB / 'station-avatars.svg').read_text().replace(
        '<svg ', '<svg style="position:absolute;width:0;height:0;overflow:hidden" aria-hidden="true" ', 1)
    shell = shell.replace('<body>', '<body>\n' + sprite)
    shell = re.sub(r'<img\b([^>]*?)src="/assets/([^"]+)"',
                   lambda tag: f'<img{tag[1]}src="{data_url(tag[2])}"', shell)
    modules = inline_modules()
    data = json.dumps(sampled_responses(), ensure_ascii=False, separators=(',', ':')).encode()
    packed = base64.b64encode(gzip.compress(data, mtime=0)).decode('ascii')
    unpack = ('const bytes = Uint8Array.from(atob("' + packed + '"), value => value.charCodeAt(0));\n'
              'const records = await new Response(new Response(bytes).body.pipeThrough('
              'new DecompressionStream("gzip"))).json();')
    mock = Path(__file__).with_name('station_demo_api.js').read_text()
    license_path = WEB / 'vendor/dataset-dialog.LICENSE.txt'
    if license_path.exists():
        shell = shell.replace('</head>', '<script type="text/plain" id="component-licenses">'
                             + license_path.read_text() + '</script>\n</head>')
    script = '\n'.join(['const modules = {};', unpack, mock, *modules])
    html = shell.replace('</body>', '<script>\n(async () => {\n' + script + '\n})();\n</script>\n</body>')
    return '\n'.join(line.rstrip() for line in html.splitlines()) + '\n'


if __name__ == '__main__':
    DESTINATION.write_text(bundle())
