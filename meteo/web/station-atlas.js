// The reserve bounds describe a monitoring area. Individual instruments can share it
// until a station supplies its own coordinates.
const reserveBounds = [32.70, 56.30, 33.15, 56.56];
const landscapes = ['reserve', 'clearing', 'field', 'wet-forest', 'dry-forest'];
const avatarHabitats = {AME60: 'reserve', KR1: 'clearing', KR2: 'field', 'RU-Fyo': 'wet-forest', 'RU-Fy2': 'dry-forest'};
const svgNamespace = 'http://www.w3.org/2000/svg';

function stationAvatar(station) {
  const code = station.code || station.id;
  // Hash the stable ID independently of catalog ordering or future additions.
  let hash = 0;
  for (const char of station.id) hash = (Math.imul(hash, 31) + char.codePointAt(0)) >>> 0;
  const group = avatarHabitats[code] || landscapes[Math.floor(hash / 3) % landscapes.length];
  const graphic = document.createElementNS(svgNamespace, 'svg');
  graphic.classList.add('atlas-avatar');
  graphic.setAttribute('viewBox', '0 0 600 240');
  graphic.setAttribute('preserveAspectRatio', 'xMidYMid slice');
  graphic.setAttribute('aria-hidden', 'true');
  const symbol = document.createElementNS(svgNamespace, 'use');
  symbol.setAttribute('href', '/assets/station-avatars.svg#' + group + '-' + (1 + hash % 3));
  graphic.append(symbol);
  return graphic;
}

function datasetTitle(dataset, list) {
  const sameName = list.filter(item => item.name === dataset.name && item.id !== dataset.id);
  if (!dataset.name || !sameName.length) return dataset.name || dataset.id;
  let length = 8;
  while (length < dataset.id.length && sameName.some(item => item.id.slice(0, length) === dataset.id.slice(0, length))) length++;
  return dataset.name + ' · ' + dataset.id.slice(0, length);
}
const numbers = new Intl.NumberFormat('ru-RU');
const plural = new Intl.PluralRules('ru');
const shortDate = new Intl.DateTimeFormat('ru-RU', {day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC'});

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

function dateLabel(value) {
  if (!value) return '';
  const date = new Date(String(value).slice(0, 10) + 'T00:00:00Z');
  return Number.isNaN(date.getTime()) ? '' : shortDate.format(date).replace(/\s*г\.$/, '');
}

function period(start, end) {
  const first = dateLabel(start);
  const last = dateLabel(end);
  return first && last && first !== last ? first + ' — ' + last : first || last || 'Период не указан';
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

function makeMap(stations, choose) {
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
    const pick = button('atlas-menu-choice', station.name || station.id, () => {
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

export function mountStationAtlas(root, stations, {onOpenDataset, initialStation, onSelectStation, loadPhoto, onUploadPhoto} = {}) {
  const atlas = el('section', 'station-atlas');
  root.replaceChildren(atlas);
  if (!stations.length) {
    atlas.append(el('p', 'atlas-empty', 'Здесь появятся станции из доступных вам наборов данных.'));
    return () => {};
  }
  const catalog = el('section', 'atlas-panel atlas-catalog');
  const dossier = el('article', 'atlas-panel atlas-dossier');
  const header = el('div', 'atlas-panel-heading');
  header.append(el('h2', '', 'Район наблюдений'), el('span', 'atlas-total', counted(stations.length, 'станция', 'станции', 'станций')));
  const list = el('div', 'atlas-stations');
  list.setAttribute('aria-label', 'Выбор станции');
  const spatial = makeMap(stations, choose);
  const stationButtons = new Map();
  let selected, previewRequest, previewURL;
  const hasArea = stations.some(station => station.location_kind === 'area' && !hasPoint(station));
  const mapNote = hasArea ? 'Контур обозначает район наблюдений. Точки отображаются для станций с известными координатами.' :
    'На карте показаны станции с опубликованными координатами.';
  const note = el('p', 'atlas-map-note', mapNote);
  for (const station of stations) {
    const btn = button('atlas-station', '', () => choose(station.id));
    const detail = el('span', 'atlas-station-text');
    const summary = station.location_kind === 'reference' ? 'Опорная станция' : counted(station.datasets?.length || 0, 'набор данных', 'набора данных', 'наборов данных');
    detail.append(el('strong', '', station.name || station.id), el('small', '', summary));
    const glyph = el('span', 'atlas-radio');
    glyph.setAttribute('aria-hidden', 'true');
    btn.append(glyph, detail);
    list.append(btn);
    stationButtons.set(station.id, btn);
  }
  catalog.append(header, spatial.map, note, list);
  atlas.append(catalog, dossier);

  function clearPhoto() {
    previewRequest?.abort();
    if (previewURL) URL.revokeObjectURL(previewURL);
    previewURL = null;
  }

  function stationImage(station) {
    const figure = el('figure', 'atlas-photo');
    const img = el('img');
    const avatar = stationAvatar(station);
    const caption = el('figcaption');
    function addLink(url, text) {
      const link = externalLink(url, 'atlas-photo-link');
      if (link) {
        link.textContent = text;
        caption.append(link);
      }
    }
    function fallback() {
      img.onload = null;
      img.onerror = null;
      img.hidden = true;
      avatar.style.display = '';
      caption.replaceChildren(el('span', '', 'Аватар станции'));
      addLink(station.photo_page_url, 'Фотогалерея ↗');
    }
    function showPhoto(url) {
      img.onerror = fallback;
      img.onload = () => {
        avatar.style.display = 'none';
        img.hidden = false;
        caption.replaceChildren(el('span', '', station.photo_credit ? 'Фото · ' + station.photo_credit : 'Фотография станции'));
        addLink(station.photo_source_url, 'Источник ↗');
        addLink(station.license_url || station.photo_license_url, station.photo_license || 'Лицензия ↗');
      };
      img.alt = 'Метеостанция ' + (station.name || station.id);
      img.src = url;
    }
    fallback();
    if (station.photo_url) {
      if (loadPhoto && station.photo_url.startsWith('/api/')) {
        previewRequest = new AbortController();
        const {signal} = previewRequest;
        Promise.resolve().then(() => loadPhoto(station.photo_url, {signal})).then(blob => {
          if (signal.aborted) return;
          previewURL = URL.createObjectURL(blob);
          showPhoto(previewURL);
        }).catch(() => { if (!signal.aborted) fallback(); });
      } else if (/^(?:https?:\/\/|\/(?!\/))/.test(station.photo_url)) showPhoto(station.photo_url);
    }
    figure.append(avatar, img, caption);
    return figure;
  }

  function photoForm(station, eligible) {
    const details = el('details', 'atlas-photo-upload');
    details.append(el('summary', '', station.photo_url ? 'Заменить фото' : 'Добавить фото'));
    const form = el('form');
    const file = el('input');
    file.type = 'file';
    file.required = true;
    file.accept = 'image/jpeg,image/png,image/webp';
    const fileLabel = el('label', '', 'Фото станции · JPG, PNG или WebP до 5 МиБ');
    fileLabel.append(file);
    const credit = el('input');
    credit.type = 'text';
    credit.maxLength = 160;
    const creditLabel = el('label', '', 'Автор или источник снимка');
    creditLabel.append(credit);
    const select = el('select');
    for (const d of eligible) {
      const option = el('option', '', datasetTitle(d, eligible));
      option.value = d.id;
      select.append(option);
    }
    if (eligible.length > 1) {
      const datasetLabel = el('label', '', 'Набор для размещения фото');
      datasetLabel.append(select);
      form.append(datasetLabel);
    }
    const submit = el('button', '', 'Сохранить фото');
    submit.type = 'submit';
    const feedback = el('span', 'atlas-upload-feedback');
    feedback.setAttribute('role', 'status');
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (submit.disabled) return;
      const selectedFile = file.files[0];
      if (!selectedFile) return;
      if (selectedFile.size > 5 * 1024 * 1024) {
        feedback.textContent = 'Выберите файл размером до 5 МиБ.';
        return;
      }
      submit.disabled = true;
      feedback.textContent = 'Загрузка…';
      try {
        await onUploadPhoto(select.value, station.id, selectedFile, credit.value.trim());
        feedback.textContent = 'Фото сохранено.';
      } catch (error) {
        feedback.textContent = error?.message || 'Не удалось загрузить фото.';
      } finally {
        submit.disabled = false;
      }
    });
    form.append(fileLabel, creditLabel, submit, feedback);
    details.append(form);
    return details;
  }

  function choose(id, notify = true) {
    const station = stations.find(item => item.id === id);
    if (!station) return;
    if (selected === id) {
      if (notify && window.matchMedia('(max-width: 840px)').matches) dossier.scrollIntoView({block: 'start'});
      return;
    }
    clearPhoto();
    selected = id;
    for (const [key, btn] of stationButtons) btn.setAttribute('aria-pressed', String(key === selected));
    spatial.select(station, notify);
    const info = el('div', 'atlas-info');
    const heading = el('div', 'atlas-identity');
    heading.append(el('h2', '', station.name || station.id));
    const code = station.code || station.id;
    if (code !== station.name) heading.append(el('span', 'atlas-id', code));
    info.append(heading);
    if (station.description) info.append(el('p', 'atlas-description', station.description));
    const location = hasPoint(station) ? [station.location_label, station.latitude.toFixed(4) + '°, ' + station.longitude.toFixed(4) + '°'].filter(Boolean).join(' · ') : station.location_label || 'Местоположение уточняется';
    info.append(el('p', 'atlas-location', location));
    const sets = station.datasets || [];
    const isReference = station.location_kind === 'reference';
    const facts = el('dl', 'atlas-facts');
    const starts = sets.map(d => d.start).filter(Boolean).sort();
    const ends = sets.map(d => d.end).filter(Boolean).sort();
    const rows = sets.reduce((sum, d) => sum + (Number(d.rows) || 0), 0);
    for (const [label, value] of [['Записи в наборах', numbers.format(rows)], ['Период наблюдений', period(starts[0], ends.at(-1))]]) {
      const fact = el('div');
      fact.append(el('dt', '', label), el('dd', '', value));
      facts.append(fact);
    }
    const related = el('section', 'atlas-related');
    const relatedHeading = el('div', 'atlas-related-heading');
    const publications = station.publications || [];
    relatedHeading.append(el('h3', '', isReference ? 'Опубликованные данные' : 'Наборы данных'), el('span', 'atlas-set-count', isReference ? publications.length : sets.length));
    related.append(relatedHeading);
    for (const d of sets) {
      const entry = button('atlas-dataset', '', () => onOpenDataset?.(d.id, station.id));
      const detail = el('span', 'atlas-dataset-text');
      detail.append(el('strong', '', datasetTitle(d, sets)));
      const sourceLabel = {station: 'Станционные наблюдения', stream: 'Поток наблюдений'}[d.source] || d.source;
      if (sourceLabel) detail.append(el('small', 'atlas-source', sourceLabel));
      detail.append(el('small', '', period(d.start, d.end) + ' · ' + counted(Number(d.rows) || 0, 'запись', 'записи', 'записей')));
      const arrow = el('span', 'atlas-open', '→');
      arrow.setAttribute('aria-hidden', 'true');
      entry.append(detail, arrow);
      related.append(entry);
    }
    if (isReference) {
      for (const publication of publications) {
        const link = externalLink(publication.url, 'atlas-dataset');
        if (!link) continue;
        const detail = el('span', 'atlas-dataset-text');
        detail.append(el('strong', '', publication.name), el('small', '', publication.period));
        link.append(detail, el('span', 'atlas-open', '↗'));
        related.append(link);
      }
      const passport = externalLink(station.source_url, 'atlas-passport');
      if (passport) {
        passport.textContent = 'Паспорт станции ↗';
        info.append(passport);
      }
    } else if (!sets.length) related.append(el('p', 'atlas-description', 'Нет доступных наборов для этой станции.'));
    dossier.replaceChildren(stationImage(station), info);
    if (!isReference || sets.length) dossier.append(facts);
    dossier.append(related);
    const eligible = sets.filter(d => d.can_upload_photo);
    if (onUploadPhoto && eligible.length) dossier.append(photoForm(station, eligible));
    if (notify) {
      onSelectStation?.(station.id);
      if (window.matchMedia('(max-width: 840px)').matches) dossier.scrollIntoView({block: 'start'});
    }
  }
  choose(stations.some(s => s.id === initialStation) ? initialStation : stations[0].id, false);
  return () => { spatial.dispose(); clearPhoto(); };
}
