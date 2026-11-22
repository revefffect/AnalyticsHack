const $ = (query, root = document) => root.querySelector(query);
const $$ = (query, root = document) => [...root.querySelectorAll(query)];
const content = $('#content');
const actions = $('#header-actions');
const notice = $('#notice');
const number = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 2});
const escape = (value) => String(value ?? '').replace(/[&<>"']/g, ch => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
})[ch]);
const fmt = (value, unit = '') => value == null ? '—' : [number.format(value), unit].filter(Boolean).join(' ');
const query = (values) => new URLSearchParams(values).toString();
let token = sessionStorage.getItem('meteo-token') || '';
let account, dict, datasets, selectedDataset, page, currentSlice, drawChart, streamCursor = 0;
const deepLink = new URLSearchParams(location.search);
let columns = [['period', 'Период'], ['value', 'Значение'], ['coverage', 'Полнота'],
  ['deviation', 'Отклонение'], ['quality', 'Качество']];

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (token) headers.set('Authorization', 'Bearer ' + token);
  if (options.json) headers.set('Content-Type', 'application/json');
  const response = await fetch(path, {...options, headers, body: options.json ? JSON.stringify(options.json) : options.body});
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : 'Проверьте введённые значения.');
  }
  return options.raw ? response : response.json();
}

function message(text = '') {
  notice.textContent = text;
  notice.hidden = !text;
}

function bind(node, event, handler) {
  node?.addEventListener(event, async (e) => {
    e.preventDefault();
    message();
    try { await handler(e); }
    catch (error) { message(error.message); }
  });
}

function options(entries, current) {
  return entries.map(([key, label]) => '<option value="' + escape(key) + '"' +
    (key === current ? ' selected' : '') + '>' + escape(label) + '</option>').join('');
}

async function signIn(value) {
  token = value;
  account = await api('/api/session');
  sessionStorage.setItem('meteo-token', token);
  dict = await api('/api/dictionary');
  const views = await api('/api/views');
  if (views.default?.length) columns = views.default;
  $('#login').hidden = $('#login-error').hidden = true;
  $('#workspace').hidden = $('#nav').hidden = $('#identity').hidden = false;
  $('#role').textContent = account.role === 'research' ? 'Исследователь' : 'Бизнес';
  $$('[data-research]').forEach(el => { el.hidden = account.role !== 'research'; });
  await navigate('analysis');
}

function signOut() {
  token = '';
  sessionStorage.removeItem('meteo-token');
  $('#login').hidden = false;
  $('#workspace').hidden = $('#nav').hidden = $('#identity').hidden = true;
}

async function navigate(next) {
  page = next;
  currentSlice = undefined;
  drawChart = undefined;
  message();
  actions.replaceChildren();
  content.innerHTML = '<p class="muted">Загрузка…</p>';
  $('#heading').textContent = {analysis: 'Наблюдения', import: 'Импорт данных', catalog: 'Каталог наборов',
    reports: 'Отчёты', alerts: 'Уведомления', satellite: 'Спутниковые данные'}[page];
  $('#eyebrow').textContent = account.role === 'research' ? 'Кабинет исследователя' : 'Кабинет бизнеса';
  $$('nav button').forEach(el => { el.setAttribute('aria-current', el.dataset.page === page ? 'page' : 'false'); });
  await {analysis: analysis, import: importPage, catalog: catalogPage, reports: reportsPage, alerts: alertsPage, satellite: satellitePage}[page]();
}

async function analysis() {
  datasets = await api('/api/datasets');
  if (!datasets.length) {
    content.innerHTML = '<section class="panel"><h2>Нет доступных наборов</h2><p>Откройте каталог или загрузите файл в кабинете исследователя.</p></section>';
    return;
  }
  selectedDataset = datasets.find(entry => entry.id === (selectedDataset || deepLink.get('dataset')))?.id || datasets[0].id;
  content.innerHTML = `<form class="panel" id="filter-form"><div class="filters">
    <label class="wide">Набор<select name="dataset">${options(datasets.map(d => [d.id, d.name]), selectedDataset)}</select></label>
    <label>Станция<select name="station"></select></label>
    <label>Параметр<select name="parameter"></select></label>
    <label>С начала<input name="start" required type="date"></label>
    <label>По конец<input name="end" required type="date"></label>
    <label>Агрегация<select name="step">${options(Object.entries(dict.periods), deepLink.get('step') || 'month')}</select></label>
  </div><div class="actions"><button class="primary">Показать</button><span id="examples"></span></div></form>
  <div id="slice"></div>`;
  const form = $('#filter-form');
  function setRange() {
    const dataset = datasets.find(item => item.id === form.elements.dataset.value);
    const station = dataset.stations.find(item => item.station === form.station.value);
    if (!station) return;
    form.start.value = station.start.slice(0, 10);
    form.end.value = station.end.slice(0, 10);
    if (['KR1', 'KR2'].includes(station.station) && dataset.metadata.territory === 'Урочище Красное') {
      form.start.value = '2024-04-01'; form.end.value = '2024-04-30';
    }
    if (dataset.source === 'stream' && !deepLink.get('step')) form.step.value = 'instant';
  }
  function setData() {
    selectedDataset = form.elements.dataset.value;
    const dataset = datasets.find(item => item.id === selectedDataset);
    form.station.innerHTML = options(dataset.stations.map(s => [s.station, dict.stations[s.station] || s.station]),
      deepLink.get('station'));
    form.step.innerHTML = options(Object.entries(dict.periods).filter(([key]) => key !== 'instant' || dataset.source === 'stream'),
      deepLink.get('step') || (dataset.source === 'stream' ? 'instant' : 'month'));
    form.parameter.innerHTML = options(dataset.parameters.map(key => [key, dict.parameters[key].name]),
      deepLink.get('parameter') || 't_air');
    setRange();
  }
  // Keep access to dataset explicit since HTMLElement.dataset also exists on the form.
  const setSelection = (station, parameter, start, end) => {
    form.station.value = station; form.parameter.value = parameter;
    form.start.value = start; form.end.value = end; form.step.value = 'month';
  };
  const run = async () => {
    const filters = Object.fromEntries(new FormData(form));
    const version = filters.dataset; delete filters.dataset;
    const results = await api('/api/datasets/' + version + '/series?' + query(filters));
    if (page !== 'analysis' || form !== $('#filter-form')) return;
    currentSlice = {version, filters, results};
    renderSlice(results);
  };
  setData();
  if (deepLink.get('start') && deepLink.get('end')) {
    form.start.value = deepLink.get('start'); form.end.value = deepLink.get('end');
  }
  if (datasets.some(d => d.stations.some(s => s.station === 'AME60') && d.stations.some(s => s.station === 'KR1'))) {
    $('#examples').innerHTML = '<button type="button" id="january">Январь АМЕ‑60</button> <button type="button" id="pressure">Давление Кр1</button>';
    const reserve = datasets.find(d => d.stations.some(s => s.station === 'AME60') && d.stations.some(s => s.station === 'KR1'));
    const example = async (args) => {
      form.elements.dataset.value = reserve.id; setData(); setSelection(...args); await run();
    };
    bind($('#january'), 'click', () => example(['AME60', 't_air', '1992-01-01', '2021-12-31']));
    bind($('#pressure'), 'click', () => example(['KR1', 'pressure_hpa', '2024-04-01', '2024-04-30']));
  }
  bind(form, 'submit', run);
  bind(form.elements.dataset, 'change', async () => { setData(); await run(); });
  bind(form.station, 'change', setRange);
  await run();
  if (deepLink.get('day') && page === 'analysis') await loadQuality(deepLink.get('day'));
}

const showCoverage = (row) => row.valid_days + ' / ' + row.expected_days + ' сут.';
const issue = (row) => row.partial ? 'Часть календарного периода' :
  row.reason ? (dict.flags[row.reason] || row.reason) : 'Пригоден';
const qcLink = row => '<button class="text" data-quality="' + escape(row.start.slice(0, 10)) + '">' + escape(row.start) +
  (row.start === row.end ? '' : ' — ' + escape(row.end)) + '</button>';

function table(data, configurable = false) {
  const available = {
    period: qcLink, value: row => fmt(row.value, data.unit),
    coverage: showCoverage, deviation: row => fmt(row.deviation, data.unit), quality: issue,
    gaps: row => row.longest_gap + ' сут.',
  };
  const shown = configurable ? columns : columns.filter(([key]) => key !== 'quality');
  return '<div class="table-wrap"><table><thead><tr>' + shown.map(([, label]) => '<th scope="col">' + escape(label) +
    '</th>').join('') + '</tr></thead><tbody>' + data.points.map(row => '<tr>' + shown.map(([key]) => '<td>' +
    (key === 'period' ? available[key](row) : escape(available[key]?.(row))) + '</td>').join('') + '</tr>').join('') +
    '</tbody></table></div>';
}

function displayChart(canvas, points) {
  const context = canvas.getContext('2d');
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  if (!width || !height) return;
  const ratio = devicePixelRatio || 1;
  canvas.width = width * ratio; canvas.height = height * ratio;
  context.scale(ratio, ratio);
  const values = points.filter(row => row.value !== null).map(row => row.value);
  const min = values.length ? Math.min(...values) : 0;
  const span = values.length ? Math.max(Math.max(...values) - min, 1) : 1;
  const left = 49, right = width - 15, top = 10, bottom = height - 36;
  const x = i => points.length === 1 ? (left + right) / 2 : left + i * (right - left) / Math.max(1, points.length - 1);
  const y = value => bottom - (value - min) * (bottom - top - 10) / span;
  context.font = '12px system-ui'; context.textAlign = 'right';
  for (let index = 0; index <= 4; index++) {
    const value = min + span * index / 4;
    const lineY = y(value);
    context.strokeStyle = '#d8e1d7'; context.lineWidth = 1; context.beginPath(); context.moveTo(left, lineY);
    context.lineTo(right, lineY); context.stroke();
    context.fillStyle = '#526761'; context.fillText(fmt(value), left - 8, lineY + 3);
  }
  context.strokeStyle = '#23634f'; context.lineWidth = 2; context.beginPath();
  let open = false;
  points.forEach((row, index) => {
    if (row.value === null) { open = false; return; }
    context[open ? 'lineTo' : 'moveTo'](x(index), y(row.value));
    open = true;
  });
  context.stroke();
  if (points.length <= 42) {
    context.fillStyle = '#23634f';
    points.forEach((row, index) => {
      if (row.value === null) return;
      context.beginPath(); context.arc(x(index), y(row.value), 3.1, 0, Math.PI * 2); context.fill();
    });
  }
  if (points.length) {
    context.fillStyle = '#526761'; context.textAlign = 'left'; context.fillText(points[0].start, left, height - 11);
    if (points.length > 1) {
      context.textAlign = 'right'; context.fillText(points.at(-1).end, right, height - 11);
    }
  }
  if (!values.length) {
    context.textAlign = 'center'; context.fillText('За этот период значения исключены правилами качества', width / 2, height / 2);
  }
  canvas.onclick = (event) => {
    if (!points.length || page !== 'analysis') return;
    const index = Math.max(0, Math.min(points.length - 1,
      Math.round((event.offsetX - left) * (points.length - 1) / Math.max(1, right - left))));
    loadQuality(points[index].start.slice(0, 10)).catch(error => message(error.message));
  };
}

function renderSlice(data) {
  const january = data.monthly_baseline[1];
  const point2010 = data.points.find(row => row.start === '2010-01-01' && row.value !== null);
  const headline = point2010 ? point2010.value : data.points.length === 1 ? data.points[0].value : data.summary.average;
  const days = data.coverage.valid_days + ' / ' + data.coverage.days;
  const summary = data.points.length === 1 ? issue(data.points[0]) : data.coverage.valid_periods + ' пригодных периодов';
  const selected = datasets.find(d => d.id === data.dataset);
  actions.innerHTML = '<button id="csv">CSV</button><button id="json">JSON</button><button id="snapshot" class="primary">Создать отчёт</button>';
  $('#slice').innerHTML = `
    <div class="cards">
      <section class="panel metric"><span>${point2010 ? 'Январь 2010' : 'Значение за период'}</span><strong>${fmt(headline, data.unit)}</strong><small>${escape(summary)}</small></section>
      <section class="panel metric"><span>Пригодные сутки</span><strong>${days}</strong><small>Покрытие выбранного параметра</small></section>
      <section class="panel metric"><span>${point2010 ? 'Среднее января за выбранный период' : 'Доступные агрегаты'}</span><strong>${point2010 ? fmt(january.value, data.unit) : data.coverage.valid_periods + ' / ' + data.coverage.periods}</strong><small>${point2010 ? january.year_count + ' лет · отклонение ' + fmt(point2010.deviation, data.unit) : escape(dict.periods[data.step])}</small></section>
    </div>
    <section class="panel"><h2>${escape(dict.parameters[data.parameter].name)} · ${escape(dict.stations[data.station] || data.station)}</h2>
      <p class="muted">${escape(data.start)} — ${escape(data.end)} · ${escape(data.unit)} · ${escape(dict.periods[data.step])}</p>
      <canvas class="chart" aria-label="График наблюдений. Та же выборка доступна в таблице."></canvas>
      <div class="actions"><span class="muted">Версия <code>${escape(data.dataset.slice(0, 12))}</code></span>
      <button id="grafana">Grafana</button><button id="satellite-at">Снимок на дату</button>${selected?.owner === account.name && account.role === 'research' ?
        '<button id="share">' + (selected.visibility === 'shared' ? 'Закрыть доступ' : 'Открыть доступ кабинету бизнеса') + '</button>' : ''}
      ${selected?.source !== 'stream' ? '<button id="raw">Исходный файл</button>' : ''}</div>
    </section>
    <div class="layout">
      <section class="panel"><h2>Таблица наблюдений</h2><div id="series-table">${table(data, true)}</div>
        <details><summary>Настроить столбцы</summary><p class="muted">Каждая строка — название поля и подпись через знак =. Доступные поля: period, value, coverage, deviation, quality, gaps.</p>
        <form id="view-form"><label>Вид таблицы<input name="columns" value="${escape(columns.map(x => x.join('=')).join(', '))}"></label>
        <button>Сохранить вид</button></form></details>
      </section>
      <section class="panel" id="quality"><h2>Контроль качества</h2><p>Выберите период или дату в таблице, чтобы открыть исходные строки.</p>
        <p class="muted">Длиннейшие серии неполных суток</p>
        ${data.missing_runs.slice(0, 4).map(r => '<p><button class="text" data-quality="' + escape(r.start) + '">' +
          escape(r.start) + ' — ' + escape(r.end) + '</button> · ' + r.days + ' сут.</p>').join('') || '<p>Серий нет.</p>'}
        <p class="muted">Шаг исходных наблюдений, мин</p><p>${data.intervals_minutes.slice(0, 5).map(([step, count]) => step + ': ' + fmt(count)).join(' · ') || '—'}</p>
      </section>
    </div>`;
  const canvas = $('.chart', $('#slice'));
  drawChart = () => displayChart(canvas, data.points);
  drawChart();
  function bindQuality() {
    $$('[data-quality]', $('#slice')).forEach(button => bind(button, 'click', () => loadQuality(button.dataset.quality)));
  }
  bindQuality();
  bind($('#csv'), 'click', () => download('export', 'csv'));
  bind($('#json'), 'click', () => download('export', 'json'));
  bind($('#raw'), 'click', () => download('raw'));
  bind($('#snapshot'), 'click', async () => {
    const {version, filters} = currentSlice;
    const report = await api('/api/datasets/' + version + '/reports', {
      method: 'POST', json: {...filters, title: 'Сводка · ' + dict.parameters[data.parameter].name,
        template: account.role === 'business' ? 'Операционная сводка' : 'Обзор наблюдений'}});
    await navigate('reports'); renderReport(report);
  });
  bind($('#satellite-at'), 'click', () => { satelliteDate = data.end.slice(0, 10); return navigate('satellite'); });
  bind($('#grafana'), 'click', async () => {
    const info = await api('/api/datasets/' + data.dataset + '/dashboard?' + query(currentSlice.filters));
    if (!info.url) throw new Error(info.detail);
    location.assign(info.url);
  });
  bind($('#share'), 'click', async () => {
    await api('/api/datasets/' + data.dataset + '/visibility?shared=' + (selected.visibility !== 'shared'), {method: 'PUT'});
    await navigate('analysis');
  });
  bind($('#view-form'), 'submit', async (event) => {
    const entries = event.currentTarget.columns.value.split(',').map(value => value.trim().split('='));
    const keys = new Set(['period', 'value', 'coverage', 'deviation', 'quality', 'gaps']);
    if (entries.some(([key, label]) => !keys.has(key) || !label)) throw new Error('Проверьте названия и подписи полей.');
    await api('/api/views/default', {method: 'PUT', json: {columns: entries}});
    columns = entries; $('#series-table').innerHTML = table(data, true); bindQuality();
  });
}

async function loadQuality(day) {
  if (!currentSlice) return;
  const {version, filters, results} = currentSlice;
  const row = results.points.find(point => point.start === day);
  const response = await api('/api/datasets/' + version + '/quality?' +
    query({station: filters.station, parameter: filters.parameter, day}));
  const panel = $('#quality');
  if (!panel) return;
  panel.innerHTML = `<h2>Контроль качества</h2>
    ${row ? '<p>' + showCoverage(row) + ' · серия до ' + row.longest_gap + ' сут.</p>' : ''}
    <label>Дата исходных наблюдений<input type="date" id="quality-date" value="${escape(day)}"></label>
    <p>${response.day.valid_slots} / ${response.day.expected_slots} ${escape(response.day.slot_unit)} · минимум ${response.day.minimum_slots}</p>
    <p class="muted">${escape(dict.flags[response.day.reason] || 'Достаточное покрытие')}</p>
    <p>${escape(Object.entries(row?.flags || response.day.flags).map(([key, count]) => key + ': ' + count).join(' · '))}</p>
    <div class="table-wrap"><table><thead><tr><th>Время / источник</th><th>Значение / QC</th></tr></thead><tbody>
    ${response.rows.map(r => '<tr><td>' + escape(r.original_stamp) + '<br><small>' + escape(r.source_file) + ':' +
      r.source_row + ' · ' + escape(r.zone === 'unknown' ? 'пояс неизвестен' : r.zone) + '</small></td><td>' +
      fmt(r.value, results.unit) + '<br><small>' + (r.received_at ? 'Принято: ' + escape(r.received_at) + '<br>' : '') + escape(r.reasons.map(v => v.label).join(', ')) + '</small></td></tr>').join('')}
    </tbody></table></div>`;
  bind($('#quality-date'), 'change', e => loadQuality(e.currentTarget.value));
}

async function download(kind, format) {
  const {version, filters} = currentSlice;
  const suffix = format ? '?' + query({...filters, format}) : '';
  const response = await api('/api/datasets/' + version + '/' + kind + suffix, {raw: true});
  const blob = URL.createObjectURL(await response.blob());
  const anchor = document.createElement('a');
  anchor.href = blob;
  anchor.download = format ? 'meteo.' + format : response.headers.get('Content-Disposition')?.match(/filename="([^"]+)"/)?.[1] || 'source';
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(blob), 500);
}

async function importPage() {
  content.innerHTML = `<section class="panel"><h2>Предпросмотр источника</h2>
    <form id="upload-form" class="filters">
      <label class="wide">Файл ZIP, CSV, XLSX, JSON или JSONL<input type="file" name="file" required accept=".zip,.csv,.xlsx,.json,.jsonl"></label>
      <button class="primary">Проверить файл</button>
    </form><div id="mapping"></div></section>`;
  bind($('#upload-form'), 'submit', async (event) => {
    const file = event.currentTarget.file.files[0];
    const {hash, tables} = await api('/api/uploads?' + query({filename: file.name}), {method: 'POST', body: file});
    const templates = await api('/api/templates');
    $('#mapping').innerHTML = `<form id="mapping-form"><p>${tables.length} таблиц. Проверьте соответствие колонок.</p>
      ${Object.keys(templates).length ? '<label>Шаблон маппинга<select id="templates"><option value="">По файлу</option>' +
        options(Object.keys(templates).map(key => [key, key])) + '</select></label>' : ''}
      ${tables.map((tab, index) => `<details ${index === 0 || Object.keys(tab.ambiguous).length ? 'open' : ''}>
        <summary>${escape(tab.name)} · ${tab.columns.length} полей ${Object.keys(tab.ambiguous).length ? '· требуется выбор' : ''}</summary>
        <p class="muted">${escape(tab.encoding || tab.sheet || 'JSON')} · ${escape(tab.conversions.join(' · '))}</p>
        <div class="filters"><label>Станция по умолчанию<input name="${index}-station" value="${escape(tab.station)}"></label>
        ${dict.fields.filter(key => key in tab.mapping || key in tab.ambiguous || ['datetime', 'station', 't_air', 'timezone'].includes(key)).map(key =>
         `<label>${escape(dict.parameters[key]?.name || key)} ${escape(dict.parameters[key]?.unit || '')}
          <select name="${index}-map-${key}"><option value="">Выберите колонку</option>${options(tab.columns.map(h => [h, h]), tab.mapping[key])}</select></label>`).join('')}
        <label class="check"><input type="checkbox" name="${index}-skip">Пропустить таблицу</label></div>
        <div class="table-wrap"><table><thead><tr>${tab.columns.map(h => '<th>' + escape(h) + '</th>').join('')}</tr></thead>
          <tbody>${tab.sample.map(row => '<tr>' + tab.columns.map(h => '<td>' + escape(row[h]) + '</td>').join('') + '</tr>').join('')}</tbody></table></div>
      </details>`).join('')}
      <div class="filters"><label>Название набора<input name="dataset-title" value="${escape(file.name)}"></label>
       <label>Сохранить шаблон под именем<input name="template"></label><button class="primary">Подтвердить импорт</button></div>
    </form>`;
    bind($('#templates'), 'change', (event) => {
      const template = templates[event.currentTarget.value];
      if (!template) return;
      const form = $('#mapping-form');
      tables.forEach((tab, i) => {
        const saved = template[tab.name] || (Object.keys(template).length === 1 && Object.values(template)[0]);
        if (!saved) return;
        if (saved.station) form.elements[i + '-station'].value = saved.station;
        Object.entries(saved.mapping || {}).forEach(([key, value]) => {
          const select = form.elements[i + '-map-' + key];
          if (select && tab.columns.includes(value)) select.value = value;
        });
      });
    });
    bind($('#mapping-form'), 'submit', async (e) => {
      const form = e.currentTarget;
      const data = new FormData(form);
      const button = $('button', form);
      button.disabled = true;
      message('Импорт выполняется…');
      try {
        const config = Object.fromEntries(tables.map((tab, index) => {
          const mapping = Object.fromEntries(dict.fields.flatMap(key => {
            const value = data.get(index + '-map-' + key);
            return value ? [[key, value]] : [];
          }));
          return [tab.name, {mapping, station: data.get(index + '-station'), skip: data.has(index + '-skip')}];
        }));
        const result = await api('/api/imports/' + hash, {method: 'POST', json: {options: config,
          name: data.get('dataset-title') || null, template: data.get('template') || null}});
        selectedDataset = result.id;
        await navigate('analysis');
        message(result.reused ? 'Такая версия уже существует.' : 'Импортировано строк: ' + fmt(result.row_count));
      } finally { button.disabled = false; }
    });
  });
}

async function catalogPage() {
  content.innerHTML = `<form class="panel" id="catalog-search"><div class="filters"><label>Поиск по каталогу
    <input name="search" type="search" placeholder="Поставщик, станция, параметр"></label><button class="primary">Найти</button></div></form>
    <div class="cards" id="catalog-list"></div>`;
  const render = async (q = '') => {
    const cards = await api('/api/catalog?' + query({q}));
    $('#catalog-list').innerHTML = cards.map(card => `<article class="panel metric">
      <span class="tag">${escape(card.type || card.metadata?.type || (card.source === 'stream' ? 'Поток' : 'Наблюдения'))}</span>
      <h3>${escape(card.name)}</h3><p>${escape(card.provider || card.metadata?.provider || card.owner)}</p>
      <p class="muted">${escape(card.territory || card.metadata?.territory)} · ${escape(card.cadence || card.metadata?.cadence || '—')} ·
        ${escape(card.format || card.metadata?.format || '—')}</p>
      <p>${escape(card.coverage || card.stations?.map(s => s.station + ' ' + s.start.slice(0, 10) + ' — ' + s.end.slice(0, 10)).join('; '))}</p>
      <p>${escape(typeof card.parameters === 'string' ? card.parameters : card.parameters?.map(key => dict.parameters[key]?.name).join(', '))}</p>
      <p class="muted">${escape(card.license || card.metadata?.license || 'Условия задаёт владелец')}</p>
      ${card.row_count ? '<small>Строк: ' + fmt(card.row_count) + ' · ' + escape(card.visibility) + ' · ' + escape(card.id.slice(0, 12)) +
          ' · ' + escape(card.created_at) + '</small>' : ''}
      ${card.url ? '<p>' + escape(card.status) + '</p><a href="' + escape(card.url) + '" target="_blank" rel="noopener">Источник</a>' : ''}
      ${card.instructions ? '<p>' + escape(card.instructions) + '</p>' : ''}
      ${card.id === 'open-meteo' && account.role === 'research' ? '<button id="external">Подключить Open-Meteo</button>' : ''}
      ${card.stations ? '<div class="actions"><button data-open="' + escape(card.id) + '">Открыть</button><button data-connect="' +
        escape(card.id) + '">' + (card.connected ? 'Подключён' : 'Подключить') + '</button></div>' : ''}
      </article>`).join('') || '<p>Ничего не найдено.</p>';
    $$('[data-open]').forEach(button => bind(button, 'click', () => {
      selectedDataset = button.dataset.open; return navigate('analysis');
    }));
    $$('[data-connect]').forEach(button => bind(button, 'click', async () => {
      await api('/api/datasets/' + button.dataset.connect + '/connect', {method: 'POST'}); button.textContent = 'Подключён';
    }));
    bind($('#external'), 'click', () => {
      $('#catalog-list').insertAdjacentHTML('afterbegin', `<form class="panel" id="external-form"><h2>Open-Meteo</h2>
        <div class="filters"><label>Широта<input name="latitude" type="number" required step="any"></label>
        <label>Долгота<input name="longitude" type="number" required step="any"></label>
        <label>С начала<input name="start" type="date" required></label><label>По конец<input name="end" type="date" required></label>
        <label>Тип<select name="kind"><option value="reanalysis">Реанализ ERA5</option><option value="forecast">Прогноз ECMWF IFS</option></select></label>
        <button class="primary">Импортировать</button></div></form>`);
      $('#external').disabled = true;
      bind($('#external-form'), 'submit', async (event) => {
        const data = Object.fromEntries(new FormData(event.currentTarget));
        const result = await api('/api/connectors/open-meteo', {method: 'POST', json: data});
        selectedDataset = result.id; await navigate('analysis');
      });
    });
  };
  bind($('#catalog-search'), 'submit', e => render(e.currentTarget.search.value));
  await render();
}

async function reportsPage() {
  const reports = await api('/api/reports');
  content.innerHTML = `<section class="panel no-print"><h2>Сохранённые отчёты</h2>
    ${reports.map(r => '<p><button class="text" data-report="' + escape(r.id) + '">' + escape(r.title) +
      '</button> · <small>' + escape(r.created_at) + '</small></p>').join('') ||
      '<p>Выберите срез в наблюдениях и нажмите «Создать отчёт».</p>'}</section><div id="report"></div>`;
  $$('[data-report]').forEach(button => bind(button, 'click', async () => renderReport(await api('/api/reports/' + button.dataset.report))));
}

function renderReport(data) {
  actions.innerHTML = '<button id="print" class="primary">Печать</button>';
  bind($('#print'), 'click', () => window.print());
  $('#report').innerHTML = `<section class="panel"><h2>${escape(data.title)}</h2>
    <p>${escape(data.template)} · ${escape(data.name)} · ${escape(dict.stations[data.station] || data.station)}</p>
    <p>${escape(data.start)} — ${escape(data.end)} · ${escape(data.unit)} · ${escape(dict.periods[data.step])}</p>
    <p>Среднее по доступным агрегатам <strong>${fmt(data.summary.average, data.unit)}</strong> · пригодных суток
    ${data.coverage.valid_days} из ${data.coverage.days}.</p>
    <p class="break">Источник ${escape(data.metadata?.provider || data.source)} · версия <code>${escape(data.dataset)}</code></p>
    <p>${escape(data.method)} · QC ${escape(data.qc)}. Пропуски остаются пустыми.</p>
    <canvas class="chart" aria-label="График отчёта"></canvas>${table(data).replaceAll(/<button class="text" data-quality="[^"]*">/g, '<span>').replaceAll('</button>', '</span>')}</section>`;
  const canvas = $('.chart', $('#report'));
  drawChart = () => displayChart(canvas, data.points); drawChart();
}

async function alertsPage() {
  const data = await api('/api/alerts');
  const events = {active: 'Порог превышен', recovered: 'Восстановление', stale: 'Данные устарели', insufficient: 'Недостаточно данных', normal: 'В норме'};
  content.innerHTML = `<section class="panel"><h2>Состояние потока</h2>
    <p>${data.feed ? 'Последний приём: ' + escape(data.feed.last_received) + ' · ' + escape(data.feed.mode) : 'Поток ещё не поступал.'}</p>
    <p>${data.rules.map(rule => escape(rule.body.name) + ' · ' + escape(events[rule.state] || rule.state)).join('<br>') || 'Нет правил для кабинета.'}</p>
    <button id="refresh">Обновить</button> ${data.demo && account.role === 'research' ? '<button class="primary" id="replay">Воспроизвести архив</button>' : ''}
    ${account.role === 'research' ? `<details><summary>Создать правило</summary><form id="rule-form" class="filters">
      <label>Название<input name="name" required value="Высокая температура"></label>
      <label>Станция<input name="station" required value="KR1"></label>
      <label>Параметр<select name="parameter">${options(Object.entries(dict.parameters).map(([k, v]) => [k,v.name]), 't_air')}</select></label>
      <label>Порог ≥<input name="threshold" type="number" step="any" required value="20"></label>
      <label>Длительность, мин<input name="minutes" type="number" min="0" required value="30"></label>
      <label>Покрытие от 0 до 1<input name="coverage" type="number" step=".01" min=".01" max="1" value=".75" required></label>
      <label>Получатель<input name="recipient" required value="business"></label>
      <label>Устаревание, мин<input name="stale_minutes" type="number" min="1" value="30" required></label>
      <button class="primary">Сохранить</button></form></details>` : ''}</section>
    <section class="panel"><h2>История событий</h2>${data.events.map(event => '<p><span class="tag warn">' +
      escape(events[event.state] || event.state) + '</span> ' + escape(event.body.name) + ' · ' + escape(event.happened_at) +
      ' · ' + fmt(event.body.value, event.body.unit) + ' · ' + escape(event.delivery) +
      (event.body.url ? ' · <a href="' + escape(event.body.url) + '">График</a>' : '') + '</p>').join('') ||
      '<p>Событий пока нет.</p>'}</section>`;
  bind($('#refresh'), 'click', alertsPage);
  bind($('#rule-form'), 'submit', async event => {
    await api('/api/alerts/rules', {method: 'POST', json: Object.fromEntries(new FormData(event.currentTarget))}); await alertsPage();
  });
  bind($('#replay'), 'click', async () => {
    const result = await api('/api/demo/replay', {method: 'POST', json: {cursor: streamCursor}});
    streamCursor = result.next_cursor;
    await alertsPage(); message('Принято: ' + result.accepted + ' · события: ' + result.events + ' · ' + result.elapsed_ms + ' мс');
  });
}

bind($('#login-form'), 'submit', e => signIn(e.currentTarget.token.value).catch(error => {
  $('#login-error').textContent = error.message; $('#login-error').hidden = false;
}));
$$('[data-token]').forEach(button => bind(button, 'click', () => signIn(button.dataset.token).catch(error => {
  $('#login-error').textContent = error.message; $('#login-error').hidden = false;
})));
bind($('#logout'), 'click', signOut);
$$('nav button').forEach(button => bind(button, 'click', () => navigate(button.dataset.page)));
window.addEventListener('resize', () => drawChart?.());
api('/health').then(config => {
  $('#demo-logins').hidden = !config.demo;
  if (!token) { signOut(); return; }
  signIn(token).catch(signOut);
}).catch(signOut);

let satelliteDate = '';
let satelliteRefresh;
async function satellitePage() {
  const config = await api('/api/satellite');
  const yesterday = new Date(Date.parse(config.today) - 86400000).toISOString().slice(0, 10);
  content.innerHTML = `<form class="panel" id="satellite-form"><div class="filters">
    <label class="wide">Область<select name="area">${options(Object.entries(config.areas).map(([k, v]) => [k, v.name]), 'reserve')}</select></label>
    <label class="wide">Слой<select name="layer">${options(Object.entries(config.layers).map(([k, v]) => [k, v.name]), 'truecolor')}</select></label>
    <label>Дата UTC<input name="day" type="date" required max="${escape(config.today)}" min="2012-01-01"
     value="${escape(satelliteDate >= '2012-01-01' && satelliteDate <= config.today ? satelliteDate : config.today)}"></label>
    <button class="primary">Обновить</button></div><div class="actions">
      <button type="button" id="sat-today">Сегодня UTC</button><button type="button" id="sat-yesterday">Вчера UTC</button>
      <label class="check"><input type="checkbox" name="auto" checked>Обновлять каждые 5 минут</label>
    </div></form><div id="satellite-result"></div>`;
  const form = $('#satellite-form');
  const render = async () => {
    if (page !== 'satellite' || form !== $('#satellite-form')) return;
    const fields = Object.fromEntries(new FormData(form));
    delete fields.auto;
    const data = await api('/api/satellite/imagery?' + query(fields));
    if (page !== 'satellite' || form !== $('#satellite-form')) return;
    $('#satellite-result').innerHTML = `<div class="layout"><section class="panel"><h2>${escape(data.area)}</h2>
      <p>${escape(data.product.name)} · ${escape(data.date)} UTC · ${escape(data.product.cadence)}</p>
      <img class="satellite-image" src="${escape(data.image_url)}" alt="${escape(data.product.name)} над выбранной областью"
        referrerpolicy="no-referrer">
      <p class="muted" id="sat-image-status" role="status">Получаем слой NASA GIBS…</p>
      <div class="actions"><a class="button" href="${escape(data.worldview)}" target="_blank" rel="noopener">Открыть в NASA Worldview</a>
        <a class="button" href="${escape(data.firms)}" target="_blank" rel="noopener">NASA FIRMS</a></div>
      <small>${escape(data.attribution)} · границы области приблизительные</small>
    </section><section class="panel"><h2>Обработанные продукты</h2><p>${escape(data.product.description)}</p>
      <p class="muted">Первичные наборы и поиск файлов по области и дате. Для части продуктов нужна учётная запись поставщика.</p>
      ${data.reference_products.map(item => `<article><h3>${escape(item.name)}</h3><p>${escape(item.resolution)}</p>
        <div class="actions"><a href="${escape(item.url)}" target="_blank" rel="noopener">Описание</a>
        ${item.granules ? '<a href="' + escape(item.granules) + '" target="_blank" rel="noopener">Файлы по области (JSON)</a>' : ''}</div></article>`).join('')}
    </section></div>`;
    const image = $('.satellite-image');
    const status = $('#sat-image-status');
    const loaded = () => { status.textContent = 'Дата слоя указана в UTC. Пустые пиксели возможны при облачности или отсутствии пролёта.'; };
    const failed = () => {
      image.hidden = true;
      status.textContent = 'Снимок не загрузился. Проверьте соседнюю дату или откройте NASA Worldview.';
    };
    image.addEventListener('load', loaded, {once: true});
    image.addEventListener('error', failed, {once: true});
    if (image.complete) (image.naturalWidth ? loaded : failed)();
  };
  bind(form, 'submit', render);
  bind($('#sat-today'), 'click', () => { form.day.value = new Date().toISOString().slice(0, 10); return render(); });
  bind($('#sat-yesterday'), 'click', () => { form.day.value = yesterday; return render(); });
  for (const name of ['area', 'layer', 'day']) bind(form.elements[name], 'change', render);
  clearInterval(satelliteRefresh);
  satelliteRefresh = setInterval(() => {
    if (page !== 'satellite') { clearInterval(satelliteRefresh); return; }
    if (form.auto.checked) render().catch(error => message(error.message));
  }, 5 * 60 * 1000);
  await render();
}
