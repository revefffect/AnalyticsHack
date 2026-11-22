// Reference points for the offline map. WGS84 coordinates identify settlements
// and one wetland, independently of the locations of individual instruments.
// Sources are the linked Wikidata records (CC0) and Russian Wikipedia articles
// (contributors to each linked page, CC BY-SA 4.0). DMS converted to decimal.
export const localLandmarks = [
  {name: 'Заповедный', kind: 'settlement', latitude: 56.45189, longitude: 32.96839,
    source: 'https://www.wikidata.org/wiki/Q4186740'},
  {name: 'Старосельский мох', kind: 'landmark', latitude: 56.47580, longitude: 33.03610,
    source: 'https://www.wikidata.org/wiki/Q4440641'},
  {name: 'Высокое', kind: 'settlement', latitude: 56.39250, longitude: 32.87694,
    source: 'https://ru.wikipedia.org/wiki/Высокое_(Высокинское_сельское_поселение)'},
  {name: 'Сосноватка', kind: 'settlement', latitude: 56.34444, longitude: 32.91611,
    source: 'https://ru.wikipedia.org/wiki/Сосноватка_(Нелидовский_район)'},
  {name: 'Жердовка', kind: 'settlement', latitude: 56.51028, longitude: 33.08917,
    source: 'https://ru.wikipedia.org/wiki/Жердовка_(Тверская_область)'},
  {name: 'Большие Ясновицы', kind: 'settlement', latitude: 56.51444, longitude: 33.03250,
    source: 'https://ru.wikipedia.org/wiki/Большие_Ясновицы'},
  {name: 'Нелидово', kind: 'settlement', latitude: 56.21667, longitude: 32.78333,
    source: 'https://www.wikidata.org/wiki/Q172366'},
];

const layers = new WeakMap();

function createLayer(container) {
  const items = localLandmarks.map(place => {
    const marker = document.createElement('span');
    marker.className = 'atlas-place atlas-place--' + place.kind;
    marker.title = place.name + ' · источник координат: ' + place.source;
    const dot = document.createElement('i');
    const label = document.createElement('span');
    label.textContent = place.name;
    marker.append(dot, label);
    container.append(marker);
    return {place, marker, label};
  });
  layers.set(container, items);
  return items;
}

function overlaps(box, other) {
  return box.left < other.right + 3 && box.right > other.left - 3 &&
    box.top < other.bottom + 2 && box.bottom > other.top - 2;
}

// position(longitude, latitude) returns CSS pixels relative to the map viewport.
// Occupied boxes describe interactive markers and map controls in the same frame.
export function renderLocalLandmarks(container, position, width, height, occupied = []) {
  const items = layers.get(container) || createLayer(container);
  const taken = [...occupied];
  for (const {place, marker, label} of items) {
    const [x, y] = position(place.longitude, place.latitude).map(Math.round);
    const point = {left: x - 2, right: x + 2, top: y - 2, bottom: y + 2};
    marker.hidden = x < 2 || x > width - 2 || y < 2 || y > height - 2 ||
      taken.some(box => overlaps(point, box));
    if (marker.hidden) continue;
    marker.style.left = x + 'px';
    marker.style.top = y + 'px';
    label.hidden = false;
    const w = label.offsetWidth;
    const h = label.offsetHeight;
    const offsets = [[5, -h / 2], [-w - 5, -h / 2], [-w / 2, 5], [-w / 2, -h - 5]];
    const box = offsets.map(([dx, dy]) => ({
      left: x + dx, top: y + dy, right: x + dx + w, bottom: y + dy + h,
    })).find(candidate => candidate.left >= 3 && candidate.top >= 3 &&
      candidate.right <= width - 3 && candidate.bottom <= height - 3 &&
      !taken.some(other => overlaps(candidate, other)));
    taken.push(point);
    if (box) {
      label.style.left = Math.round(box.left - x) + 'px';
      label.style.top = Math.round(box.top - y) + 'px';
      taken.push(box);
    } else {
      label.hidden = true;
    }
  }
}
