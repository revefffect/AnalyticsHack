import assert from 'node:assert/strict';

// Exercise production navigation and presentation under both the file adapter and FastAPI.
export async function journey(ui, {offline, mobile}) {
  const {w, d, $, all, wait, fire, go} = ui;
  await wait(() => $('[role=tab][aria-selected=true]') && $('table .atlas-sort'), 'Station registry tabs and data');
  assert.deepEqual(all('#nav [data-page]').map(button => button.dataset.page).sort(),
    ['alerts','analysis','catalog','import','reports','satellite','stations']);
  const registry = $('.atlas-sort').closest('table');
  const rowCount = registry.querySelectorAll('tbody tr').length;
  assert.ok(offline ? rowCount === 5 : rowCount >= 4);
  const search = $('#workspace-search');
  search.value = 'RU-Fy2'; fire(search, 'input');
  assert.equal(registry.querySelectorAll('tbody tr').length, 1);
  search.value = ''; fire(search, 'input');
  assert.equal(registry.querySelectorAll('tbody tr').length, rowCount);
  const sort = $('.atlas-sort');
  sort.click();
  assert.equal(sort.closest('th').getAttribute('aria-sort'), 'ascending');
  for (const value of ['ink','violet','forest']) {
    $('#workspace-theme').value = value; fire($('#workspace-theme'), 'change');
    assert.equal(d.documentElement.dataset.workspaceTheme, value);
  }
  if (mobile) {
    const hamburger = $('.menu-toggle');
    assert.equal(hamburger.getAttribute('aria-expanded'),'false');
    hamburger.click();
    assert.equal(hamburger.getAttribute('aria-expanded'),'true');
    d.dispatchEvent(new w.KeyboardEvent('keydown', {key:'Escape',bubbles:true}));
    assert.equal(hamburger.getAttribute('aria-expanded'),'false');
    assert.equal(d.activeElement, hamburger);
  }
  all('table button').find(button => button.textContent.includes('Кр2')).click();
  await wait(() => $('.atlas-id')?.textContent === 'KR2','Map and selected station');
  const avatar = $('.atlas-dossier .atlas-avatar use').getAttribute('href');
  assert.ok(offline ? avatar.startsWith('#') && d.getElementById(avatar.slice(1)) : avatar.includes('station-avatars.svg#'));
  $('.atlas-data-table .atlas-dataset').click();
  await wait(() => $('#series-table tbody tr') && $('#view-form'), 'Actual KR2 observations and React column chooser');
  assert.equal($('#filter-form').elements.station.value,'KR2');
  if (mobile) assert.equal($('.menu-toggle').getAttribute('aria-expanded'),'false');
  const initialColumns = all('#series-table th').length;
  const additional = all('#view-form input[type=checkbox]').find(item => !item.checked);
  additional.click(); fire($('#view-form'), 'submit');
  await wait(() => all('#series-table th').length === initialColumns + 1, 'Saved extra analysis column');
  $('#series-table [data-quality]').click();
  await wait(() => $('#quality-rows table tbody tr') && $('#quality-rows select'), 'Quality flags and source rows');
  const qualityFilter = $('#quality-rows select');
  const filteredCount = Number(qualityFilter.options[1].textContent.match(/\d+$/)[0]);
  qualityFilter.value = qualityFilter.options[1].value; fire(qualityFilter,'change');
  await wait(() => all('#quality-rows tbody tr').length === filteredCount, 'Filtered source rows');
  assert.ok($('#quality-rows tbody [data-slot=badge]'));
  $('#snapshot').click();
  await wait(() => $('#report table tbody tr'), 'Created report with real observations');
  const savedTitle = $('#report h2').textContent;
  const saved = await go('reports','[data-report]');
  saved.click();
  await wait(() => $('#report h2')?.textContent === savedTitle, 'Opened saved report');
  await go('satellite', '#satellite-result .satellite-image');
  const satForm = $('#satellite-form');
  satForm.elements.layer.value = 'surface'; fire(satForm.elements.layer,'change');
  await wait(() => $('.satellite-image')?.src.includes('MODIS_Terra_Land_Surface'), 'Surface temperature imagery');
  satForm.elements.area.value = 'west'; fire(satForm.elements.area,'change');
  await wait(() => $('#satellite-result h2')?.textContent.includes('Западные'), 'Neighboring area');
  const image = $('.satellite-image');
  fire(image, 'error');
  assert.ok(image.hidden && $('#sat-image-status').textContent.includes('не загрузился'));
  const alerts = await go('alerts','#rule-form');
  alerts.elements.name.value = 'Граница температуры';
  fire(alerts,'submit');
  await wait(() => $('#content').textContent.includes('Граница температуры'), 'Saved threshold rule');
  await go('import','#upload-form');
  const upload = $('#upload-form');
  if (offline) {
    await wait(() => upload.dataset.archiveExample && upload.elements.file.disabled, 'Bundled archive selection');
  } else {
    const bytes = await ui.archiveBytes();
    Object.defineProperty(upload.elements.file,'files',{value:[new File([bytes], 'kr2_2024-04-sample.zip')]});
  }
  fire(upload,'submit');
  const mapping = await wait(() => $('#mapping-form'), 'Source mapping and preview');
  assert.ok(mapping.querySelector('tbody tr'));
  fire(mapping,'submit');
  await wait(() => $('#series-table table') && /Импортировано|существует/.test($('#notice').textContent),'Imported archive');
  await go('catalog','[data-settings]');
  $('[data-settings]').click();
  const dialog = await wait(() => $('[role=dialog] .dataset-invite'), 'Radix access dialog and actual member invite');
  const before = all('.dataset-person').length;
  fire(dialog,'submit');
  await wait(() => all('.dataset-person').length === before + 1, 'Granted permission');
  const esc = new w.KeyboardEvent('keydown',{key:'Escape', bubbles:true});
  d.dispatchEvent(esc);
  await wait(() => !$('[role=dialog]'), 'Dismissed permission dialog');
  assert.deepEqual(ui.errors, []);
}
