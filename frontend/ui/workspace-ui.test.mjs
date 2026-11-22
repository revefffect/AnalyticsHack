import assert from 'node:assert/strict';
import {after, before, test} from 'node:test';
import {setTimeout as wait} from 'node:timers/promises';
import {JSDOM} from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>', {
  url: 'https://workspace.test', pretendToBeVisual: true,
});
const win = dom.window;
let ui, installFormControls;
before(async () => {
  for (const name of ['window', 'document', 'navigator', 'HTMLElement', 'Element', 'Node', 'HTMLInputElement',
    'HTMLButtonElement', 'MutationObserver', 'NodeFilter', 'Event', 'CustomEvent', 'KeyboardEvent', 'MouseEvent', 'DOMRect']) {
    Object.defineProperty(globalThis, name, {configurable: true, value: win[name]});
  }
  for (const name of ['getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) globalThis[name] = win[name].bind(win);
  ui = await import('../../meteo/web/vendor/dataset-dialog.js');
  ({installFormControls} = await import('../../meteo/web/form-controls.js'));
});
after(() => win.close());

async function eventually(condition) {
  const deadline = Date.now() + 2000;
  while (!condition() && Date.now() < deadline) await wait(5);
  assert.ok(condition(), 'Timed out waiting for rendered state');
}

test('tabs link accessible panels, support arrows and preserve existing fields', async () => {
  document.body.innerHTML = '<main></main>';
  const host = document.querySelector('main');
  const first = document.createElement('input');
  first.value = 'Кр1';
  const second = document.createElement('div');
  const changed = [];
  const tabs = ui.mountAtlasTabs(host, {items: [
    {value: 'catalog', label: 'Каталог', panel: first}, {value: 'map', label: 'Карта', panel: second},
  ], initialValue: 'catalog', onChange: value => changed.push(value)});
  const [catalog, map] = host.querySelectorAll('[role=tab]');
  const list = host.querySelector('[role=tablist]');
  await eventually(() => list.tabIndex === 0);
  list.focus();
  await eventually(() => document.activeElement === catalog);
  assert.equal(catalog.getAttribute('aria-selected'), 'true');
  assert.equal(document.getElementById(catalog.getAttribute('aria-controls')).contains(first), true);
  catalog.dispatchEvent(new win.KeyboardEvent('keydown', {key: 'ArrowRight', bubbles: true}));
  await eventually(() => document.activeElement === map && map.getAttribute('aria-selected') === 'true');
  assert.equal(first.closest('[role=tabpanel]').hidden, true);
  assert.equal(second.closest('[role=tabpanel]').hidden, false);
  tabs.select('catalog');
  assert.equal(first.value, 'Кр1');
  assert.deepEqual(changed, ['map', 'catalog']);
  tabs.dispose();
  assert.equal(host.childElementCount, 0);
});

test('dataset dialog traps focus, supports Escape and navigation cleanup', async () => {
  document.body.innerHTML = '<button id="trigger">Настройки</button>';
  const trigger = document.querySelector('#trigger');
  trigger.focus();
  let mounts = 0;
  const open = () => ui.openDatasetDialog({trigger, title: 'Наблюдения Кр1', mount(host) {
    mounts++;
    host.innerHTML = '<button>Добавить</button><input name="name">';
  }});
  const close = open();
  const getDialog = () => document.querySelector('[role=dialog]');
  await eventually(() => getDialog()?.contains(document.activeElement));
  assert.equal(mounts, 1);
  assert.match(document.getElementById(getDialog().getAttribute('aria-labelledby')).textContent, /Наблюдения Кр1/);
  trigger.focus();
  await eventually(() => getDialog().contains(document.activeElement));
  document.activeElement.dispatchEvent(new win.KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
  await eventually(() => !getDialog() && document.activeElement === trigger);
  close();
  const dispose = open();
  await eventually(() => getDialog()?.contains(document.activeElement));
  dispose();
  await eventually(() => !getDialog() && document.body.style.pointerEvents === '');
});

test('native controls keep current options, labels, events, submission and reset', async () => {
  document.body.innerHTML = '<form><label>Станция<select name="site"><option>Кр1</option><option>Кр2</option></select></label>' +
    '<label for="flag">Пропустить</label><input id="flag" name="flag" type="checkbox">' +
    '<input type="checkbox" name="auto" role="switch" checked></form>';
  const form = document.querySelector('form');
  const {site, flag, auto} = form.elements;
  let changes = 0;
  site.addEventListener('change', () => changes++);
  flag.addEventListener('change', () => changes++);
  const cleanup = installFormControls(document.body);
  assert.equal(installFormControls(document.body), cleanup);
  assert.equal(site.dataset.slot, 'native-select');
  assert.equal(flag.dataset.slot, 'checkbox');
  assert.equal(auto.dataset.slot, 'switch');
  assert.equal(flag.labels[0].htmlFor, 'flag');
  assert.equal(site.labels[0].textContent.startsWith('Станция'), true);
  assert.equal(site, form.elements.site);
  flag.labels[0].click();
  assert.equal(flag.checked, true);
  site.value = 'Кр2';
  site.dispatchEvent(new win.Event('change', {bubbles: true}));
  assert.equal(changes, 2);
  assert.equal(new win.FormData(form).get('site'), 'Кр2');
  auto.disabled = true;
  assert.equal(new win.FormData(form).has('auto'), false);
  flag.hidden = true;
  assert.equal(flag.hidden, true);
  form.reset();
  assert.equal(flag.checked, false);
  assert.equal(site.value, 'Кр1');
  site.innerHTML = '<optgroup label="Соседи"><option>RU-Fyo</option></optgroup>';
  form.insertAdjacentHTML('beforeend', '<select name="new"><option>Тверь</option></select>');
  await eventually(() => form.elements.new.dataset.slot === 'native-select');
  assert.equal(site.options[0].dataset.slot, 'native-select-option');
  assert.equal(site.querySelector('optgroup').dataset.slot, 'native-select-optgroup');
  assert.equal(site.value, 'RU-Fyo');
  cleanup();
});

test('observation quality filter and column settings remain interactive under native enhancement', async () => {
  document.body.innerHTML = '<section id="quality"></section><section id="columns"></section>';
  const cleanup = installFormControls(document.body);
  const quality = document.querySelector('#quality');
  const columns = document.querySelector('#columns');
  const offQuality = ui.mountObservationTools(quality, 'quality', {
    rows: [
      {id: 1, status: 'usable', source_file: 'station.csv', source_row: 1, zone: 'unknown', value: 12, reasons: []},
      {id: 2, status: 'flagged', source_file: 'station.csv', source_row: 2, zone: 'unknown', value: 99,
        reasons: [{code: 'range', label: 'Выход за диапазон'}]},
    ], formatValue: value => String(value),
  });
  const available = [['date', 'Дата'], ['temp', 'Температура'], ['wind', 'Ветер']];
  let saved;
  const offColumns = ui.mountObservationTools(columns, 'columns', {available, columns: available,
    onSave: async rows => { saved = rows; }});
  const filter = quality.querySelector('select');
  await eventually(() => filter.parentElement.dataset.slot === 'native-select-wrapper');
  assert.equal(quality.querySelectorAll('tbody tr').length, 2);
  for (const [value, label] of [['flagged', 'Выход за диапазон'], ['usable', 'Пригодно']]) {
    filter.value = value;
    filter.dispatchEvent(new win.Event('change', {bubbles: true}));
    await eventually(() => quality.querySelector('tbody [data-slot="badge"]').textContent === label);
    assert.equal(quality.querySelectorAll('tbody tr').length, 1);
  }
  const [date, , wind] = columns.querySelectorAll('.column-choice');
  const windLabel = wind.querySelector('input[type=text]');
  const windCheck = wind.querySelector('[data-slot=checkbox]');
  windCheck.click();
  await eventually(() => windLabel.disabled);
  windCheck.click();
  await eventually(() => !windLabel.disabled);
  // Drive the native setter as an actual user agent would; React observes the input event.
  Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, 'value').set.call(windLabel, 'Ветер, м/с');
  windLabel.dispatchEvent(new win.Event('input', {bubbles: true}));
  await eventually(() => Boolean(wind.querySelector('[aria-label="Переместить выше: Ветер, м/с"]')));
  wind.querySelector('[aria-label^="Переместить выше"]').click();
  await eventually(() => columns.querySelectorAll('.column-choice')[1] === wind);
  date.querySelector('input[type=checkbox]').click();
  await eventually(() => date.querySelector('input[type=text]').disabled);
  columns.querySelector('button[type=submit]').click();
  await eventually(() => Boolean(saved));
  assert.deepEqual(saved, [['wind', 'Ветер, м/с'], ['temp', 'Температура']]);
  offColumns(); offQuality(); cleanup();
});
