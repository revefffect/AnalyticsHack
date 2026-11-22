// This adapter serves the FastAPI samples embedded by the demo builder.
// Updates affect the current tab only. The deployed app always uses the server.
const {fixtures, sampleSeries, sampleQuality, csvExports, satelliteLayers, rawArchives, sampleUpload} = records;
const photos = new Map();
const utcDay = () => new Date().toISOString().slice(0, 10);
const reply = (detail, status = 422) => Response.json({detail}, {status});
const decoded = base64 => Uint8Array.from(atob(base64), character => character.charCodeAt(0));
const uploadPath = '/api/datasets/' + sampleUpload.dataset;
const participants = new Set(fixtures[uploadPath + '/members'].candidates);
participants.add(fixtures['/api/session'].name);
const archivedFile = () => new File([decoded(rawArchives[uploadPath])], sampleUpload.name, {type:'application/zip'});

function prepareDemoForms() {
  const form = document.querySelector('#upload-form');
  if (form && !form.dataset.archiveExample) {
    form.dataset.archiveExample = 'true';
    const input = form.elements.file;
    input.required = false;
    input.disabled = true;
    // Run the live upload/mapping UI with the exact archive used to capture its API responses.
    Object.defineProperty(input, 'files', {value: [archivedFile()]});
    const note = document.createElement('p');
    note.className = 'muted';
    note.textContent = 'Архивный пример: ' + sampleUpload.name + '. Для импорта своих файлов откройте серверную версию.';
    form.prepend(note);
    form.querySelector('button').textContent = 'Проверить архивный пример';
  }
  const rule = document.querySelector('#rule-form');
  if (rule && !rule.dataset.localExample) {
    rule.dataset.localExample = 'true';
    rule.elements.recipient.value = 'Анна';
    const hint = document.createElement('p');
    hint.className = 'muted';
    hint.textContent = 'Правила демо хранятся в этой вкладке. Получение событий и доставка уведомлений работают в серверной версии.';
    rule.before(hint);
  }
  const mapping = document.querySelector('#mapping-form');
  if (mapping && !mapping.dataset.archiveExample) {
    mapping.dataset.archiveExample = 'true';
    // The snapshot captures the original mapping. Changing it requires the importer.
    for (const input of mapping.querySelectorAll('input,select')) {
      input.setAttribute('aria-disabled', 'true');
      if (input.matches('select,input[type=checkbox]')) input.disabled = true;
      else input.readOnly = true;
    }
    mapping.querySelector('button').textContent = 'Открыть архивный набор';
  }
}
new MutationObserver(prepareDemoForms).observe(document.body, {childList: true, subtree: true});

function satelliteImage(params) {
  const day = params.get('day');
  const frame = satelliteLayers[[params.get('area'), params.get('layer')].join('|')];
  if (!frame || !/^\d{4}-\d{2}-\d{2}$/.test(day) || day < '2012-01-01' || day > utcDay())
    return reply('Выберите область, спутниковый слой и дату вплоть до сегодняшнего дня по UTC.');
  // Preserve each API-provided product lookback and shift its UTC links to the requested date.
  const offset = Date.parse(day) - Date.parse(frame.date);
  const shift = url => url.replace(/\d{4}-\d{2}-\d{2}/g, value =>
    new Date(Date.parse(value) + offset).toISOString().slice(0, 10));
  return Response.json({...frame, date: day, image_url: shift(frame.image_url), worldview: shift(frame.worldview),
    reference_products: frame.reference_products.map(product => product.granules ?
      {...product, granules: shift(product.granules)} : product)});
}
const sessionValues = new Map([['meteo-token', 'fixture0']]);
const sessionStorage = {
  getItem: key => sessionValues.get(key) ?? null,
  setItem: (key, value) => sessionValues.set(key, String(value)),
  removeItem: key => sessionValues.delete(key),
};

async function fetch(input, options = {}) {
  const url = new URL(input, 'https://preview.test');
  const path = url.pathname;
  const segments = path.split('/').map(decodeURIComponent);
  const method = options.method || 'GET';
  const id = segments[3];
  const base = '/api/datasets/' + id;
  const cards = [
    ...fixtures['/api/catalog'],
    ...fixtures['/api/datasets'],
    ...fixtures['/api/stations'].flatMap(station => station.datasets),
  ].filter(card => card.id === id);

  if (path.endsWith('/photo')) {
    if (method === 'PUT') {
      photos.set(path, options.body);
      const station = fixtures['/api/stations'].find(row => row.id === segments[5]);
      const dataset = station.datasets.find(row => row.id === id);
      dataset.photo_url = station.photo_url = path;
      dataset.photo_credit = station.photo_credit = url.searchParams.get('credit');
      return Response.json({photo_url: path, photo_credit: dataset.photo_credit});
    }
    return photos.has(path) ? new Response(photos.get(path)) : new Response(null, {status: 404});
  }

  if (segments[4] === 'members' && segments[5]) {
    const username = segments[5];
    const acl = fixtures[base + '/members'];
    acl.members = acl.members.filter(member => member.username !== username);
    if (method === 'PUT') {
      const member = {username, role: JSON.parse(options.body).role};
      acl.members.push(member);
      return Response.json(member);
    }
    return new Response(null, {status: 204});
  }

  if (path === base && method === 'PATCH' && cards.length) {
    const name = JSON.parse(options.body).name.trim();
    for (const card of cards) card.name = name;
    return Response.json(cards[0]);
  }
  if (path === base + '/visibility' && method === 'PUT') {
    const visibility = url.searchParams.get('shared') === 'true' ? 'shared' : 'private';
    for (const card of cards) card.visibility = visibility;
    return Response.json({visibility});
  }
  if (path === base + '/connect' && method === 'POST') {
    for (const card of cards) card.connected = true;
    return Response.json({connected: true});
  }
  if (path === '/api/catalog') {
    const search = (url.searchParams.get('q') || '').toLocaleLowerCase('ru');
    return Response.json(fixtures[path].filter(card => JSON.stringify(card).toLocaleLowerCase('ru').includes(search)));
  }
  if (path === '/api/satellite') return Response.json({...fixtures[path], today: utcDay()});
  if (path === '/api/satellite/imagery') return satelliteImage(url.searchParams);
  if (path === '/api/uploads' && method === 'POST') {
    return options.body.name === sampleUpload.name ? Response.json(sampleUpload.response) :
      reply('В автономном режиме доступен поставляемый архивный пример.');
  }
  if (path === '/api/imports/' + sampleUpload.response.hash && method === 'POST') {
    return Response.json({id: sampleUpload.dataset, reused: true});
  }
  if (path === '/api/views/default' && method === 'PUT') {
    fixtures['/api/views'].default = JSON.parse(options.body).columns;
    return Response.json({saved: true});
  }
  if (path === '/api/alerts/rules' && method === 'POST') {
    const body = JSON.parse(options.body);
    const list = fixtures['/api/alerts'].rules;
    if (!body.name?.trim() || !body.station?.trim() || !Object.hasOwn(fixtures['/api/dictionary'].parameters, body.parameter) ||
        !participants.has(body.recipient) || !(Number(body.coverage) > 0 && Number(body.coverage) <= 1))
      return reply('Проверьте имя, станцию, параметр, получателя и полноту правила.');
    const existing = list.find(item => JSON.stringify(item.body) === JSON.stringify(body));
    if (existing) return Response.json({id: existing.id});
    const id = 'local-rule-' + (list.length + 1);
    list.push({id, body, state: 'insufficient'});
    return Response.json({id});
  }
  if (path === base + '/quality') {
    const row = sampleQuality[[base, url.searchParams.get('station'), url.searchParams.get('day')].join('|')];
    return row && url.searchParams.get('parameter') === 't_air' ? Response.json(row) :
      reply('По выбранной станции и дате в архивной выборке нет данных.');
  }
  if (path === base + '/raw' && rawArchives[base]) {
    return new Response(decoded(rawArchives[base]), {headers: {'Content-Type': 'application/zip',
      'Content-Disposition': 'attachment; filename="station-sample.zip"'}});
  }
  if (path === base + '/dashboard') {
    return Response.json({url: null, detail: 'Grafana подключается администратором в серверной версии.'});
  }
  if (path === '/api/connectors/open-meteo') return reply('Получение новых наблюдений доступно в серверной версии.', 503);
  if ([base + '/series', base + '/export', base + '/reports'].includes(path)) {
    const fields = method === 'POST' ? JSON.parse(options.body) : Object.fromEntries(url.searchParams);
    const key = [base + '/series', fields.station, fields.step].join('|');
    const result = sampleSeries[key];
    if (!result || ['parameter', 'start', 'end'].some(key => fields[key] !== result[key]))
      return reply('Выберите полный период станции, указанный в карточке набора.');
    if (path.endsWith('/reports')) {
      const list = fixtures['/api/reports'];
      const reportId = 'local-report-' + (list.length + 1);
      const report = {...result, id: reportId, title: fields.title, template: fields.template,
        metadata: fixtures['/api/datasets'].find(row => row.id === id).metadata};
      fixtures['/api/reports/' + reportId] = report;
      list.unshift({id: reportId, title: report.title, created_at: new Date().toISOString(), dataset: id});
      return Response.json(report);
    }
    if (path.endsWith('/export') && fields.format === 'csv')
      return new Response(csvExports[key], {headers: {'Content-Type': 'text/csv'}});
    return Response.json(result);
  }
  if (Object.hasOwn(fixtures, path)) return Response.json(fixtures[path]);
  return reply('Для этого действия требуется серверная версия приложения.', 404);
}
