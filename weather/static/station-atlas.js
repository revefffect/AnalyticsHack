// The reserve bounds describe a monitoring area. Individual instruments can share it
// until a station supplies its own coordinates.
const reserveBounds = [32.70, 56.30, 33.15, 56.56];
const avatarHabitats = {ame60: 'reserve', kr1: 'clearing', kr2: 'field', 'RU-Fyo': 'wet-forest', 'RU-Fy2': 'dry-forest'};
const svgNamespace = 'http://www.w3.org/2000/svg';

function stationAvatar(station) {
  const code = station.code || station.id;
  // Hash the stable ID independently of catalog ordering or future additions.
  let hash = 0;
  for (const char of station.id) hash = (Math.imul(hash, 31) + char.codePointAt(0)) >>> 0;
  const group = avatarHabitats[code];
  const graphic = document.createElementNS(svgNamespace, 'svg');
  graphic.classList.add('atlas-avatar');
  graphic.setAttribute('viewBox', '0 0 600 240');
  graphic.setAttribute('preserveAspectRatio', 'xMidYMid slice');
  graphic.setAttribute('aria-hidden', 'true');
  const symbol = document.createElementNS(svgNamespace, 'use');
  symbol.setAttribute('href', '/portal/static/station-avatars.svg#' + group + '-' + (1 + hash % 3));
  graphic.append(symbol);
  return graphic;
}

const numbers = new Intl.NumberFormat('ru-RU');
const plural = new Intl.PluralRules('ru');


function el(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined && text !== null) item.textContent = String(text);
  return item;
}

function button(className, text, action) {
  const item = el('button', className, text);
  item.type = 'button';
  item.addEventListener('click', action);
  return item;
}

function counted(count, one, few, many) {
  return numbers.format(count) + ' ' + ({one, few}[plural.select(count)] || many);
}

function externalLink(url, className) {
  try {
    if (new URL(url).protocol !== 'https:') return null;
  } catch { return null; }
  const link = el('a', className);
  link.href = url;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  return link;
}

function hasPoint(station) {
  return typeof station.longitude === 'number' && Math.abs(station.longitude) <= 180 &&
    typeof station.latitude === 'number' && Math.abs(station.latitude) <= 85.0511;
}

function project(lon, lat, zoom) {
  const size = 256 * 2 ** zoom;
  const phi = Math.max(-85.0511, Math.min(85.0511, lat)) * Math.PI / 180;
  return [(lon + 180) / 360 * size, (1 - Math.asinh(Math.tan(phi)) / Math.PI) / 2 * size];
}

function unproject(x, y, zoom) {
  const size = 256 * 2 ** zoom;
  return [x / size * 360 - 180, Math.atan(Math.sinh(Math.PI * (1 - 2 * y / size))) * 180 / Math.PI];
}

function makeMap(stations, choose, menuLabel) {
  const map = el('div', 'atlas-map');
  const viewport = el('div', 'atlas-viewport');
  viewport.tabIndex = 0;
  viewport.setAttribute('role', 'group');
  viewport.setAttribute('aria-label', 'Географическая карта. Клавиши со стрелками перемещают карту, плюс и минус меняют масштаб.');
  const grid = el('div', 'atlas-grid');
  const tiles = el('div', 'atlas-tiles');
  const overlay = el('div', 'atlas-overlay');
  grid.setAttribute('aria-hidden', 'true');
  tiles.setAttribute('aria-hidden', 'true');
  const control = el('div', 'atlas-controls');
  const [west, south, east, north] = reserveBounds;
  const area = stations.filter(station => !hasPoint(station) && station.location_kind === 'area');
  const points = stations.filter(hasPoint);
  const center = [(west + east) / 2, (south + north) / 2];
  let focus = center, zoom = 9, fitted = false, selected = null;
  let open = false, drag;
  const attribution = el('a', 'atlas-attribution', '© OpenStreetMap');
  attribution.href = 'https://www.openstreetmap.org/copyright';
  attribution.target = '_blank';
  attribution.rel = 'noopener';
  const status = el('div', 'atlas-map-status', 'Координатная сетка');
  const menu = el('div', 'atlas-map-menu');
  menu.hidden = true;
  const group = button('atlas-area', '', () => toggle(!open));
  group.setAttribute('aria-label', 'Выбрать станцию в районе наблюдений');
  group.setAttribute('aria-expanded', 'false');
  group.append(el('span', '', counted(area.length, 'станция', 'станции', 'станций') + ' ▾'));
  const menuHeading = el('strong', '', 'Станции в районе');
  const closeMenu = button('atlas-menu-close', '×', () => { toggle(false); group.focus(); });
  closeMenu.setAttribute('aria-label', 'Закрыть список станций');
  menu.append(menuHeading, closeMenu);
  for (const station of area) {
    const pick = button('atlas-menu-choice', menuLabel(station), () => {
      toggle(false);
      choose(station.id);
      group.focus({preventScroll: true});
    });
    pick.dataset.station = station.id;
    menu.append(pick);
  }
  const markers = points.map(station => {
    const marker = button('atlas-marker', '', () => choose(station.id));
    marker.setAttribute('aria-label', 'Выбрать станцию ' + (station.name || station.id));
    const label = el('span', '', station.code || station.name || station.id);
    marker.append(label);
    return {station, marker};
  });
  if (area.length) overlay.append(group);
  for (const {marker} of markers) overlay.append(marker);

  function toggle(value) {
    open = value;
    menu.hidden = !value;
    group.setAttribute('aria-expanded', String(value));
    if (value) menu.querySelector('.atlas-menu-choice')?.focus();
  }

  function fit() {
    const bounds = [];
    if (area.length || !points.length) bounds.push([west, south], [east, north]);
    for (const station of points) bounds.push([station.longitude, station.latitude]);
    const xs = bounds.map(point => project(...point, 0)[0]);
    const ys = bounds.map(point => project(...point, 0)[1]);
    const left = Math.min(...xs), right = Math.max(...xs), top = Math.min(...ys), bottom = Math.max(...ys);
    focus = unproject((left + right) / 2, (top + bottom) / 2, 0);
    const k = Math.min((viewport.clientWidth - 54) / (right - left || 0.1), (viewport.clientHeight - 64) / (bottom - top || 0.1));
    zoom = Math.max(1, Math.min(12, Math.floor(Math.log2(Math.max(k, 2)))));
    fitted = true;
  }

  function render() {
    const w = viewport.clientWidth, h = viewport.clientHeight;
    if (!w || !h) return;
    if (!fitted) fit();
    const [cx, cy] = project(...focus, zoom);
    const x0 = cx - w / 2, y0 = cy - h / 2;
    const size = 2 ** zoom;
    const wanted = new Set();
    for (let y = Math.max(0, Math.floor(y0 / 256)); y <= Math.min(size - 1, Math.floor((y0 + h) / 256)); y++) {
      for (let x = Math.floor(x0 / 256); x <= Math.floor((x0 + w) / 256); x++) {
        if (x < 0 || x >= size) continue;
        const key = zoom + '/' + x + '/' + y;
        wanted.add(key);
        let tile = tiles.querySelector('[data-key="' + key + '"]');
        if (!tile) {
          tile = el('img');
          tile.alt = '';
          tile.draggable = false;
          tile.dataset.key = key;
          tile.onload = () => { tile.dataset.loaded = '1'; tile.hidden = false; syncStatus(); };
          tile.onerror = () => { tile.hidden = true; syncStatus(); };
          tile.hidden = true;
          tile.src = 'https://tile.openstreetmap.org/' + key + '.png';
          tiles.append(tile);
        }
        tile.style.left = Math.round(x * 256 - x0) + 'px';
        tile.style.top = Math.round(y * 256 - y0) + 'px';
      }
    }
    for (const tile of [...tiles.children]) if (!wanted.has(tile.dataset.key)) tile.remove();
    syncStatus();
    grid.replaceChildren();
    for (let x = 50; x < w; x += 125) {
      const tick = el('span', 'atlas-meridian', unproject(x + x0, cy, zoom)[0].toFixed(2) + '°');
      tick.style.left = x + 'px';
      grid.append(tick);
    }
    for (let y = 52; y < h; y += 84) {
      const tick = el('span', 'atlas-parallel', unproject(cx, y + y0, zoom)[1].toFixed(2) + '°');
      tick.style.top = y + 'px';
      grid.append(tick);
    }
    if (area.length) {
      const [left, top] = project(west, north, zoom), [right, bottom] = project(east, south, zoom);
      Object.assign(group.style, {left: left - x0 + 'px', top: top - y0 + 'px', width: right - left + 'px', height: bottom - top + 'px'});
    }
    for (const {station, marker} of markers) {
      const [x, y] = project(station.longitude, station.latitude, zoom);
      marker.style.left = x - x0 + 'px';
      marker.style.top = y - y0 + 'px';
      marker.setAttribute('aria-pressed', String(station.id === selected));
      // Place nearby labels on opposite sides, retaining the exact points.
      const neighbors = points.filter(other => {
        if (station === other) return false;
        const [nx, ny] = project(other.longitude, other.latitude, zoom);
        return Math.abs(x - nx) < 115 && Math.abs(y - ny) < 46;
      });
      marker.classList.toggle('atlas-label-left', neighbors.some(other =>
        other.longitude > station.longitude || other.longitude === station.longitude && other.id > station.id));
    }
  }

  function syncStatus() {
    status.hidden = Boolean(tiles.querySelector('[data-loaded]'));
  }
  function moveBy(x, y) {
    const [cx, cy] = project(...focus, zoom);
    const max = 256 * 2 ** zoom;
    focus = unproject(Math.min(max, Math.max(0, cx + x)), Math.min(max, Math.max(0, cy + y)), zoom);
    render();
  }
  function changeZoom(delta) { zoom = Math.max(1, Math.min(18, zoom + delta)); render(); }
  for (const [symbol, label, action] of [
    ['+', 'Увеличить масштаб', () => changeZoom(1)],
    ['−', 'Уменьшить масштаб', () => changeZoom(-1)],
    ['⌖', 'Показать все станции', () => { fit(); render(); }]
  ]) {
    const btn = button('', symbol, action);
    btn.setAttribute('aria-label', label);
    btn.title = label;
    control.append(btn);
  }
  viewport.addEventListener('keydown', event => {
    if (event.key === 'Escape' && open) { toggle(false); group.focus(); return; }
    if (event.target !== viewport) return;
    const actions = {ArrowLeft: () => moveBy(-64, 0), ArrowRight: () => moveBy(64, 0), ArrowUp: () => moveBy(0, -64), ArrowDown: () => moveBy(0, 64), '+': () => changeZoom(1), '-': () => changeZoom(-1)};
    if (actions[event.key]) { event.preventDefault(); actions[event.key](); }
  });
  viewport.addEventListener('pointerdown', event => {
    if (event.button !== 0 || event.target.closest('button, a, .atlas-map-menu')) return;
    toggle(false);
    drag = [event.clientX, event.clientY];
    viewport.setPointerCapture(event.pointerId);
  });
  viewport.addEventListener('pointermove', event => {
    if (!drag) return;
    moveBy(drag[0] - event.clientX, drag[1] - event.clientY);
    drag = [event.clientX, event.clientY];
  });
  viewport.addEventListener('lostpointercapture', () => { drag = null; });
  viewport.append(grid, tiles, overlay, control, menu, status, attribution);
  map.append(viewport);
  const observer = new ResizeObserver(render);
  observer.observe(viewport);
  return {map, select(station, recenter) {
    selected = station.id;
    for (const pick of menu.querySelectorAll('[data-station]')) pick.setAttribute('aria-pressed', String(pick.dataset.station === selected));
    if (recenter && (hasPoint(station) || area.includes(station))) {
      focus = hasPoint(station) ? [station.longitude, station.latitude] : center;
      if (!hasPoint(station)) zoom = Math.min(zoom, 10);
    }
    render();
  }, dispose: () => observer.disconnect()};
}

function detailList(entries) {
  const list = el('dl', 'atlas-facts');
  for (const [label, value] of entries) {
    const row = el('div');
    row.append(el('dt', '', label), el('dd', '', value));
    list.append(row);
  }
  return list;
}

export function mountStationAtlas(root, stations, {initial, select, questionButton, parameterLabel}) {
  const atlas = el('div', 'station-atlas');
  const catalog = el('section', 'atlas-panel');
  const dossier = el('article', 'atlas-panel atlas-dossier');
  dossier.setAttribute('aria-label', 'Карточка станции');
  const header = el('div', 'atlas-panel-heading');
  header.append(el('h2', '', 'Район наблюдений'), el('span', 'atlas-total', counted(stations.length, 'станция', 'станции', 'станций')));
  const list = el('div', 'atlas-stations');
  const buttons = new Map();
  const titles = new Map(), names = new Map(), sources = new Map();
  for (const site of stations) {
    const title = site.selection ? site.title + ' · ' + (site.files.length === 1 ? site.files[0] : site.input) : 'Опорная станция FLUXNET';
    titles.set(site, title);
    names.set(site.name, (names.get(site.name) || 0) + 1);
    const key = JSON.stringify([site.code, title]);
    sources.set(key, (sources.get(key) || 0) + 1);
  }
  function sourceLabel(site) {
    const title = titles.get(site);
    return site.selection && sources.get(JSON.stringify([site.code, title])) > 1 ? title + ' · ' + site.selection.dataset : title;
  }
  const spatial = makeMap(stations, choose, site => names.get(site.name) > 1 ? site.name + ' · ' + sourceLabel(site) : site.name);
  for (const site of stations) {
    const pick = button('atlas-station', '', () => choose(site.id));
    const text = el('span', 'atlas-station-text');
    text.append(el('strong', '', site.name), el('small', '', sourceLabel(site)));
    pick.append(el('span', 'atlas-radio'), text);
    list.append(pick);
    buttons.set(site.id, pick);
  }
  catalog.append(header, spatial.map, el('p', 'atlas-map-note', 'Пунктир обозначает район наблюдений заповедника. Точки показывают площадки с опубликованными координатами.'), list);
  atlas.append(catalog, dossier);
  root.replaceChildren(atlas);

  function choose(id, recenter = true) {
    const site = stations.find(item => item.id === id);
    if (!site) return;
    for (const [key, pick] of buttons) pick.setAttribute('aria-pressed', String(key === id));
    spatial.select(site, recenter);
    dossier.replaceChildren();
    const reference = site.location_kind === 'reference';
    if (site.location_kind !== 'unknown') {
      const illustration = el('figure', 'atlas-photo');
      illustration.append(stationAvatar(site), el('figcaption', '', 'Иллюстрация ландшафта'));
      dossier.append(illustration);
    }
    const info = el('div', 'atlas-info'), identity = el('div', 'atlas-identity');
    const heading = el('h2', '', site.name);
    heading.tabIndex = -1;
    identity.append(heading);
    info.append(identity);
    if (site.description) info.append(el('p', 'atlas-description', site.description));
    info.append(el('p', 'atlas-location', reference ? site.latitude + '° с. ш. · ' + site.longitude + '° в. д.' :
      site.location_kind === 'area' ? 'Урочище Красное · район наблюдений' : 'Координаты в источнике не указаны'));
    dossier.append(info);
    const actions = el('div', 'atlas-related actions');
    if (reference) {
      const link = externalLink(site.source_url, 'action-link');
      if (link) { link.textContent = site.publication; actions.append(link); }
    } else {
      const [first, last] = site.months;
      dossier.append(detailList([['Набор', site.title], ['Источник', site.input],
        ['Месяцы наблюдений', first === last ? first : first + ' — ' + last],
        ['Строк в импорте', numbers.format(site.records)], ['Показатели', site.parameters.map(parameterLabel).join(', ')]]));
      if (site.files.length > 1 || site.files[0] !== site.input) {
        const details = el('details', 'atlas-related');
        details.append(el('summary', '', 'Таблицы станции (' + site.files.length + ')'));
        const files = el('ul');
        for (const name of site.files) files.append(el('li', '', name));
        details.append(files);
        dossier.append(details);
      }
      const question = questionButton(site);
      if (question) actions.append(question);
      for (const [label, href] of [['Открыть отчёт', site.report_url], ['Открыть Grafana', site.grafana_url]]) {
        const link = el('a', 'action-link', label);
        link.href = href;
        if (href === site.grafana_url) { link.target = '_blank'; link.rel = 'noopener'; }
        actions.append(link);
      }
    }
    dossier.append(actions);
    select(id);
    if (recenter && matchMedia('(max-width: 840px)').matches) heading.focus();
  }
  if (initial && !buttons.has(initial)) {
    dossier.append(el('p', 'atlas-info', 'Эта версия станции недоступна. Выберите станцию из списка.'));
  } else if (stations.length) choose(initial || stations[0].id, false);
  return spatial.dispose;
}
