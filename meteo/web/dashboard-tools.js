import {chartDefaults, chartKinds, chartMethods, drawSeries, inspectSeries, pointKeys} from './series-chart.mjs';

const panels = {
  chart: 'График', metrics: 'Сводные показатели', table: 'Таблица наблюдений',
  quality: 'Контроль качества', insights: 'Анализ ряда', annotations: 'Экспертные отметки',
};
const markNames = {
  anomaly: 'Аномалия', sensor_issue: 'Сбой датчика', weather_event: 'Погодное событие',
  normal: 'Подтверждённое наблюдение', gap: 'Пропуск',
};
const numbers = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 3});
const format = (value, unit = '') => Number.isFinite(value) ? [numbers.format(value), unit].filter(Boolean).join(' ') : '—';
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function options(select, choices) {
  for (const [key, name] of Object.entries(choices)) {
    const option = element('option', name);
    option.value = key;
    select.append(option);
  }
}
function heading(title, explanation) {
  const box = element('header', null, 'dashboard-section-head');
  box.append(element('h2', title));
  if (explanation) box.append(element('p', explanation, 'muted'));
  return box;
}
function showError(node, error) { node.textContent = error?.message || 'Не удалось получить данные.'; }
function listRows(items, describe) {
  const list = element('ul', null, 'dashboard-items');
  for (const item of items) list.append(describe(item));
  return list;
}
function validPanels(names) {
  return [...new Set((Array.isArray(names) ? names : []).filter(name => Object.hasOwn(panels, name)))];
}
function previewRows(patch) {
  const values = [];
  if (chartKinds[patch.kind]) values.push('График — ' + chartKinds[patch.kind]);
  if (chartMethods[patch.method]) values.push('Детектор — ' + chartMethods[patch.method]);
  if (patch.window != null) values.push('Окно — ' + patch.window);
  if (patch.threshold != null) values.push('Порог — ' + patch.threshold);
  if (typeof patch.reference === 'boolean') values.push('Локальный ориентир — ' + (patch.reference ? 'показать' : 'скрыть'));
  if (patch.panels) values.push('Порядок разделов — ' + validPanels(patch.panels).map(key => panels[key]).join(' → '));
  return values;
}

function renderInsights(host, result) {
  const {unit, coverage, distribution, trend, anomalies, automl, management} = result;
  const facts = element('dl', null, 'dashboard-metrics');
  for (const [name, value] of [
    ['Пригодные сутки', coverage.valid_days + ' / ' + coverage.days],
    ['Медиана', format(distribution.median, unit)],
    ['Минимум', format(distribution.min, unit)], ['Максимум', format(distribution.max, unit)],
  ]) {
    const fact = element('div');
    fact.append(element('dt', name), element('dd', value)); facts.append(fact);
  }
  host.replaceChildren(facts);
  if (trend.description) host.append(element('p', trend.description));
  const anomalyHeading = element('h3', 'Кандидаты в аномалии · ' + (result.anomaly_count ?? anomalies.length));
  host.append(anomalyHeading);
  if (result.anomaly_method) host.append(element('p', result.anomaly_method, 'muted'));
  if (anomalies.length) {
    host.append(listRows(anomalies, item => {
      const row = element('li');
      row.append(element('strong', item.date + ' · ' + format(item.value, unit)), element('p', item.explanation));
      return row;
    }));
  } else host.append(element('p', 'По пригодным значениям среза алгоритм не выделил кандидатов.', 'muted'));
  host.append(element('h3', 'Сравнение прогнозов'));
  if (automl.forecast) {
    const f = automl.forecast;
    host.append(element('p', 'После выбранного периода, на ' + f.date + ' — ' + format(f.value, unit) + ' (' + f.label + ').'));
  }
  if (automl.reason) host.append(element('p', automl.reason));
  if (automl.candidates.length) {
    const container = element('div', null, 'table-wrap');
    const table = element('table');
    table.setAttribute('aria-label', 'Проверка прогноза на отложенных датах');
    const head = table.createTHead().insertRow();
    for (const name of ['Метод', 'Средняя абсолютная ошибка', 'Дат проверки']) {
      const cell = element('th', name); cell.scope = 'col'; head.append(cell);
    }
    const body = table.createTBody();
    for (const model of automl.candidates) {
      const row = body.insertRow();
      for (const value of [model.label, format(model.mae, unit), model.n]) row.insertCell().textContent = value;
    }
    container.append(table); host.append(container);
  }
  if (management.length) {
    host.append(element('h3', 'Для управленческого решения'), listRows(management, item => {
      const row = element('li'); row.append(element('strong', item.title), element('p', item.detail)); return row;
    }));
  }
}

function annotationPanel(section, api, version, selection, alive) {
  section.append(heading('Экспертные отметки', 'Отметки относятся к выбранной версии набора, станции и параметру.'));
  const status = element('p', 'Загружаем отметки…', 'muted'); status.setAttribute('role', 'status');
  const list = element('div'); list.setAttribute('data-anno-list', '');
  const form = element('form'); form.hidden = true; form.setAttribute('data-anno-form', '');
  form.innerHTML = '<div class="dash-form-grid"><label>Начало<input required type="date" name="start"></label>' +
    '<label>Конец<input required type="date" name="end"></label><label>Тип отметки<select name="label"></select></label></div>' +
    '<label>Комментарий эксперта<textarea name="note" rows="2" required maxlength="1000" placeholder="Что видно в данных и чем это подтверждается?"></textarea></label>' +
    '<button class="primary" type="submit">Добавить отметку</button>';
  const fields = form.elements;
  options(fields.label, markNames);
  for (const name of ['start', 'end']) {
    fields[name].min = selection.start; fields[name].max = selection.end; fields[name].value = selection.start;
  }
  fields.start.onchange = () => {
    fields.end.min = fields.start.value || selection.start;
    if (fields.end.value < fields.start.value) fields.end.value = fields.start.value;
  };
  section.append(status, form, list);
  const {station, parameter, start, end} = selection;
  const endpoint = version + '/annotations';
  const params = new URLSearchParams({station, parameter, start, end});
  let items = [];
  function refresh() {
    if (items.length) list.replaceChildren(listRows(items, mark => {
      const row = element('li');
      const title = element('strong', markNames[mark.label] || mark.label);
      const date = mark.start === mark.end ? mark.start : mark.start + ' — ' + mark.end;
      row.append(title, element('small', date + ' · ' + mark.author, 'dash-mark-meta'), element('p', mark.note));
      return row;
    }));
    else list.replaceChildren(element('p', 'В этом срезе пока нет экспертных отметок.', 'muted'));
  }
  api(endpoint + '?' + params).then(response => {
    if (!alive()) return;
    items = response.items; refresh(); form.hidden = !response.can_annotate;
    status.textContent = response.scope === 'local' ? 'Демо сохраняет отметки в текущей вкладке.' :
      response.can_annotate ? '' : 'Доступен просмотр отметок экспертов.';
  }).catch(error => { if (alive()) showError(status, error); });
  form.onsubmit = async event => {
    event.preventDefault();
    if (!form.reportValidity() || form.hidden) return;
    const submit = form.querySelector('button'); submit.disabled = true;
    try {
      const item = await api(endpoint, {method: 'POST', json: {
        station, parameter, start: fields.start.value, end: fields.end.value, label: fields.label.value, note: fields.note.value.trim(),
      }});
      if (!alive()) return;
      items = [item, ...items.filter(existing => existing.id !== item.id)];
      fields.note.value = ''; refresh(); status.textContent = 'Экспертная отметка добавлена.';
    } catch (error) { if (alive()) showError(status, error); }
    finally { submit.disabled = false; }
  };
}

function researchTools(section, {api, data, version, selection, canvas, applyPanels, onQuality, alive}) {
  section.append(heading('Конструктор дашборда'));
  section.insertAdjacentHTML('beforeend',
    '<form data-dash-chat><label>Что показать на дашборде?<textarea name="prompt" required rows="2" maxlength="2000" placeholder="Покажи распределение температуры, а разметку подними выше таблицы"></textarea></label>' +
    '<div class="actions"><button type="submit" class="primary">Предложить настройки</button><small>Сообщение и настройки передаются настроенному помощнику.</small></div></form>' +
    '<div class="dashboard-chat-log" role="log" aria-live="polite"></div><div data-dash-preview hidden></div>' +
    '<details data-dash-controls><summary>Настроить вручную</summary><fieldset class="dash-model">' +
    '<div class="dash-form-grid"><label>Вид графика<select data-dash-kind name="kind"></select></label><label>Детектор<select name="method"></select></label>' +
    '<label>Окно, точек<input type="number" name="window" min="3" max="90" step="1"></label><label>Порог отклонения<input type="number" name="threshold" min=".5" max="12" step="any"></label></div>' +
    '<label class="check"><input type="checkbox" name="reference"> Показывать локальный ориентир</label>' +
    '<h3>Разделы и порядок</h3><div data-dash-panels></div></fieldset></details>' +
    '<div class="actions"><button type="button" data-dash-save disabled>Сохранить дашборд</button><span data-dash-status role="status" class="muted">Загружаем настройки…</span></div>');
  const find = selector => section.querySelector(selector);
  const form = find('[data-dash-chat]'), controls = find('.dash-model'), status = find('[data-dash-status]');
  const log = find('.dashboard-chat-log'), proposal = find('[data-dash-preview]'), save = find('[data-dash-save]');
  const model = Object.fromEntries([...controls.querySelectorAll('[name]')].map(control => [control.name, control]));
  options(model.kind, chartKinds); options(model.method, chartMethods);
  const keys = pointKeys(data.points), circular = data.parameter === 'wind_dir';
  const legend = element('p', '', 'dashboard-chart-key');
  if (canvas) canvas.insertAdjacentElement('afterend', legend);
  const selectionQuery = new URLSearchParams(selection);
  let settings = {...chartDefaults, panels: Object.keys(panels)}, edits = {}, revision = 0, loaded = false;
  let dirty = false, saving = false, analysis = [], local = false;
  const conversation = [];
  function draw() {
    if (!alive() || !canvas) return;
    canvas.dataset.chartKind = settings.kind;
    canvas.setAttribute('aria-label', chartKinds[settings.kind] + ' · ' + data.station + ' · ' + data.parameter);
    drawSeries(canvas, data.points, {...settings, analysis: analysis.length ? analysis : undefined, reference: settings.reference && !circular && settings.method !== 'off',
      unit: data.unit, circular, select(index) { onQuality?.(data.points[index].start.slice(0, 10)); }});
  }
  function sync() {
    for (const name of ['kind', 'method', 'window', 'threshold']) model[name].value = settings[name];
    model.reference.checked = settings.reference;
    model.method.disabled = circular;
    model.window.disabled = model.threshold.disabled = circular || settings.method === 'off';
    model.reference.disabled = model.window.disabled || settings.kind === 'histogram';
    const visible = validPanels(settings.panels);
    const order = [...visible, ...Object.keys(panels).filter(name => !visible.includes(name))];
    const list = find('[data-dash-panels]');
    list.replaceChildren();
    for (const key of order) {
      const row = element('div', null, 'dash-panel-row'), label = element('label', null, 'check');
      const checkbox = element('input'); checkbox.type = 'checkbox'; checkbox.checked = visible.includes(key);
      checkbox.disabled = checkbox.checked && visible.length === 1;
      checkbox.onchange = () => change({panels: checkbox.checked ? [...visible, key] : visible.filter(value => value !== key)});
      label.append(checkbox, document.createTextNode(panels[key])); row.append(label);
      for (const [direction, glyph, description] of [[-1, '↑', 'выше'], [1, '↓', 'ниже']]) {
        const shift = element('button', glyph), index = visible.indexOf(key);
        shift.type = 'button'; shift.setAttribute('aria-label', panels[key] + ' — ' + description);
        shift.disabled = index < 0 || index + direction < 0 || index + direction >= visible.length;
        shift.onclick = () => {
          const next = visible.slice();
          [next[index], next[index + direction]] = [next[index + direction], next[index]];
          change({panels: next});
        };
        row.append(shift);
      }
      list.append(row);
    }
    analysis = inspectSeries(data.points, keys, settings, edits, circular);
    const legendItems = [settings.kind === 'histogram' ? 'Число наблюдений по интервалам значений' : 'Ряд наблюдений'];
    if (!circular && settings.method !== 'off' && settings.kind !== 'histogram') {
      legendItems.push('Оранжевые точки — кандидаты в выбросы по ' + chartMethods[settings.method] +
        ' (' + analysis.filter(point => point.anomaly).length + ')');
      if (settings.reference) legendItems.push('Пунктир — ориентир по предыдущим точкам');
    }
    if (Object.keys(edits).length && settings.kind !== 'histogram') legendItems.push('Фиолетовая метка — ручная правка исследовательского слоя');
    legend.textContent = legendItems.join(' · ');
    applyPanels?.(visible);
    draw();
  }
  function change(patch) {
    settings = {...settings, ...patch}; dirty = true; revision++;
    save.disabled = !loaded || saving;
    status.textContent = 'Изменения применены к этому виду. Сохраните, чтобы вернуться к нему позже.';
    sync();
  }
  controls.onchange = event => {
    const target = event.target;
    if (!Object.hasOwn(model, target.name) || !target.reportValidity()) return;
    const value = target.type === 'checkbox' ? target.checked : target.type === 'number' ? target.valueAsNumber : target.value;
    change({[target.name]: value});
  };
  controls.disabled = true; form.querySelector('button').disabled = true;
  api(version + '/chart-workspace?' + selectionQuery).then(response => {
    if (!alive()) return;
    edits = response.edits || {}; local = response.scope === 'local';
    settings = {...settings, ...(response.settings || {})};
    settings.panels = validPanels(settings.panels);
    if (!settings.panels.length) settings.panels = Object.keys(panels);
    loaded = true; controls.disabled = false; form.querySelector('button').disabled = false;
    status.textContent = local ? 'Настройки автономного демо действуют в текущей вкладке.' : '';
    sync();
  }).catch(error => { if (alive()) showError(status, error); });
  function appendTurn(author, message) {
    const row = element('div'); row.append(element('strong', author), element('p', message)); log.append(row);
  }
  form.onsubmit = async event => {
    event.preventDefault();
    const input = form.elements.prompt, submit = form.querySelector('button'), prompt = input.value.trim();
    if (!prompt || submit.disabled) return;
    submit.disabled = true; proposal.hidden = true; proposal.replaceChildren();
    appendTurn('Вы', prompt); conversation.push({role: 'user', content: prompt}); input.value = '';
    status.textContent = 'Готовим предложение…';
    try {
      const answer = await api(version + '/chart-assistant', {method: 'POST', json: {
        selection, settings, messages: conversation.slice(-8),
      }});
      if (!alive()) return;
      const source = {deepseek: 'DeepSeek API', yandex: 'DeepSeek · Yandex AI Studio'}[answer.source] || 'Локальный помощник · без вызова DeepSeek';
      const responseText = [answer.reply, ...(answer.questions || [])].filter(Boolean).join('\n');
      conversation.push({role: 'assistant', content: responseText.slice(0, 2000)});
      appendTurn(source, responseText);
      const patch = Object.fromEntries(Object.entries(answer.patch || {}).filter(([, value]) => value != null));
      const details = previewRows(patch);
      status.textContent = '';
      if (details.length) {
        const apply = element('button', 'Применить предложение'); apply.type = 'button'; apply.setAttribute('data-dash-apply', '');
        proposal.append(element('strong', 'Предлагаемые изменения'), listRows(details, text => element('li', text)), apply);
        proposal.hidden = false;
        apply.onclick = () => { change(patch); proposal.hidden = true; input.focus(); };
      }
    } catch (error) { if (alive()) { showError(status, error); input.value = prompt; conversation.pop(); } }
    finally { submit.disabled = false; }
  };
  save.onclick = async () => {
    if (!loaded || saving || !dirty) return;
    const savedRevision = revision; saving = true; save.disabled = true;
    try {
      await api(version + '/chart-workspace', {method: 'PUT', json: {selection, settings, edits}});
      if (!alive()) return;
      if (savedRevision === revision) {
        dirty = false; status.textContent = local ? 'Сохранено в текущей вкладке.' : 'Дашборд сохранён для этого среза.';
      }
    } catch (error) { if (alive()) showError(status, error); }
    finally { saving = false; save.disabled = !dirty; }
  };
  return draw;
}

export function mountDashboardTools(host, {api, data, account, canvas, applyPanels, onQuality}) {
  let disposed = false;
  const alive = () => !disposed && host.isConnected;
  const version = '/api/datasets/' + encodeURIComponent(data.dataset);
  const selection = Object.fromEntries(['station', 'parameter', 'start', 'end', 'step'].map(key => [key, data[key]]));
  const insightSection = element('section', null, 'panel dashboard-tool');
  const annotationSection = element('section', null, 'panel dashboard-tool');
  insightSection.dataset.dashboardPanel = 'insights'; annotationSection.dataset.dashboardPanel = 'annotations';
  insightSection.append(heading('Анализ ряда', 'Статистика и сравнение прогнозов по выбранному срезу.'));
  const results = element('div', 'Вычисляем показатели…'); results.setAttribute('data-insight-results', '');
  insightSection.append(results);
  let draw = () => {};
  if (account.role === 'research') {
    const section = element('section', null, 'panel dashboard-tool dashboard-constructor');
    host.append(section);
    draw = researchTools(section, {api, data, version, selection, canvas, applyPanels, onQuality, alive});
  }
  host.append(insightSection, annotationSection);
  annotationPanel(annotationSection, api, version, selection, alive);
  api(version + '/insights?' + new URLSearchParams(selection)).then(result => {
    if (alive()) renderInsights(results, result);
  }).catch(error => { if (alive()) showError(results, error); });
  function dispose() { disposed = true; }
  dispose.redraw = draw;
  return dispose;
}
