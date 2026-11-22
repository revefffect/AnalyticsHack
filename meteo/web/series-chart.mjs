const number = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 2});
const show = value => number.format(value);
export const chartKinds = {line: 'Линия', bar: 'Столбцы', scatter: 'Точки', histogram: 'Гистограмма'};
export const chartMethods = {mad: 'Медиана и MAD', zscore: 'Среднее и σ', off: 'Выключен'};
export const chartDefaults = {kind: 'line', method: 'mad', window: 14, threshold: 3.5, reference: false};

export function pointKeys(points) {
  const seen = new Map();
  return points.map(row => {
    const occurrence = seen.get(row.start) || 0;
    seen.set(row.start, occurrence + 1);
    return row.start + '|' + occurrence;
  });
}

function median(values) {
  const sorted = values.slice().sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return (sorted[middle] + sorted[Math.floor((sorted.length - 1) / 2)]) / 2;
}

function modelFit(values, method) {
  if (method === 'mad') {
    const center = median(values);
    return {center, scale: 1.4826 * median(values.map(value => Math.abs(value - center)))};
  }
  const center = values.reduce((sum, value) => sum + value, 0) / values.length;
  const variance = values.reduce((sum, value) => sum + (value - center) ** 2, 0) / (values.length - 1);
  return {center, scale: Math.sqrt(variance)};
}

export function canApply(row, edit) {
  return row && row.value !== null && edit && row.value === edit.original;
}

export function inspectSeries(points, keys, settings, edits, circular = false) {
  const history = [];
  return points.map((row, index) => {
    const candidate = edits[keys[index]];
    const edit = canApply(row, candidate) ? candidate : undefined;
    const value = edit?.action === 'exclude' ? null : edit?.action === 'replace' ? edit.value : row.value;
    const count = history.length;
    const minimum = Math.max(3, Math.ceil(settings.window * 0.5));
    let result = {value, edit, count, minimum, center: null, scale: null, score: null, anomaly: false};
    if (settings.method !== 'off' && !circular && count >= minimum) {
      const fit = modelFit(history.map(point => point.value), settings.method);
      const score = row.value !== null && fit.scale > 1e-12 ? Math.abs(row.value - fit.center) / fit.scale : null;
      result = {...result, ...fit, score, anomaly: !edit && score !== null && score >= settings.threshold};
    }
    // Fit only on earlier positions in the series. The current value can affect later fits.
    if (value !== null) history.push({index, value});
    while (history.length && history[0].index <= index - settings.window) history.shift();
    return result;
  });
}

function limits(values, zero = false) {
  let min = zero ? 0 : Infinity, max = zero ? 0 : -Infinity;
  for (const value of values) if (Number.isFinite(value)) {
    min = Math.min(min, value);
    max = Math.max(max, value);
  }
  if (!Number.isFinite(min)) return [0, 1];
  if (min === max) return [min - 0.5, max + 0.5];
  return [min, max];
}

function histogram(values) {
  const [min, max] = limits(values);
  const count = Math.min(24, Math.max(1, Math.ceil(Math.sqrt(values.filter(Number.isFinite).length))));
  const bins = Array.from({length: count}, () => []);
  const width = (max - min) / count;
  values.forEach((value, index) => {
    if (Number.isFinite(value)) bins[Math.min(count - 1, Math.floor((value - min) / width))].push(index);
  });
  return {bins, min, max};
}

const timestamp = value => Date.parse(value.slice(0, 10) + 'T' + (value.slice(11, 19) || '00:00:00') + 'Z');
const dayLabel = value => new Date(value).toISOString().slice(0, 10).split('-').reverse().join('.');
const timeLabel = value => new Date(value).toISOString().slice(11, 16);

function line(ctx, coordinates) {
  ctx.beginPath();
  let open = false;
  for (const point of coordinates) {
    if (!point) { open = false; continue; }
    if (open) ctx.lineTo(...point);
    else ctx.moveTo(...point);
    open = true;
  }
  ctx.stroke();
}

function dot(ctx, x, y, radius, fill = true) {
  ctx.beginPath();
  ctx.arc(x, y, radius, 0, Math.PI * 2);
  if (fill) ctx.fill();
  else ctx.stroke();
}

export function drawSeries(canvas, points, config = {}) {
  const {kind = 'line', selected = -1, analysis, reference, select, unit = '', circular = false} = config;
  const width = canvas.clientWidth, height = canvas.clientHeight;
  const ctx = canvas.getContext('2d');
  if (!ctx || width < 100 || height < 80) return;
  const palette = canvas.ownerDocument.defaultView.getComputedStyle(canvas.ownerDocument.documentElement);
  const colors = {
    grid: palette.getPropertyValue('--border').trim() || '#e0e8df',
    ink: palette.getPropertyValue('--muted-foreground').trim() || '#526761',
    series: palette.getPropertyValue('--primary').trim() || '#23634f',
    reference: '#637f91', anomaly: '#a6511b', manual: '#68449b',
  };
  const ratio = devicePixelRatio || 1;
  canvas.width = width * ratio;
  canvas.height = height * ratio;
  ctx.scale(ratio, ratio);
  ctx.font = '11px system-ui';
  const values = analysis ? analysis.map(row => row.value) : points.map(row => row.value);
  const isHistogram = kind === 'histogram';
  const distribution = isHistogram ? histogram(values) : null;
  const times = isHistogram ? [] : points.map(point => (timestamp(point.start) + timestamp(point.end)) / 2);
  const bounds = points.length ? [timestamp(points[0].start), timestamp(points.at(-1).end)] : [0, 1];
  const [xMin, xMax] = isHistogram ? [distribution.min, distribution.max] : bounds;
  const samples = isHistogram ? distribution.bins.map(bin => bin.length) : values.slice();
  if (analysis && !isHistogram) for (const [index, row] of analysis.entries()) {
    if (row.edit && row.edit.action !== 'keep') samples.push(points[index].value);
    if (reference) samples.push(row.center);
  }
  let [yMin, yMax] = limits(samples, isHistogram || kind === 'bar');
  if (isHistogram) { yMin = 0; yMax = Math.max(1, yMax); }
  if (circular && !isHistogram) [yMin, yMax] = [0, 360];
  const left = Math.max(48, Math.ceil(ctx.measureText(show(yMin)).width) + 13, Math.ceil(ctx.measureText(show(yMax)).width) + 13);
  const right = width - 14, top = 27, bottom = height - 31;
  const px = value => xMin === xMax ? (left + right) / 2 : left + (right - left) * (value - xMin) / (xMax - xMin);
  const py = value => bottom - (bottom - top) * (value - yMin) / (yMax - yMin);

  ctx.fillStyle = colors.ink;
  ctx.textAlign = 'left';
  ctx.fillText(isHistogram ? 'Точек' : unit, left, 15);
  for (let i = 0; i <= 4; i++) {
    const value = yMin + (yMax - yMin) * i / 4;
    if (isHistogram && !Number.isInteger(value) && yMax < 5) continue;
    ctx.strokeStyle = colors.grid;
    ctx.lineWidth = 1;
    line(ctx, [[left, py(value)], [right, py(value)]]);
    ctx.textAlign = 'right';
    ctx.fillText(show(value), left - 8, py(value) + 4);
  }
  const ticks = xMin === xMax ? 1 : width < 510 ? 2 : 5;
  for (let i = 0; i < ticks; i++) {
    const value = xMin + (xMax - xMin) * i / Math.max(1, ticks - 1);
    ctx.textAlign = ticks === 1 ? 'center' : i === 0 ? 'left' : i === ticks - 1 ? 'right' : 'center';
    const label = isHistogram ? show(value) : xMax > xMin && xMax - xMin < 86400000 ? timeLabel(value) : dayLabel(value);
    if (points.length) ctx.fillText(label, px(value), height - 10);
  }
  if (!values.some(Number.isFinite)) {
    ctx.textAlign = 'center';
    ctx.fillText('Нет значений в этом представлении', (left + right) / 2, (top + bottom) / 2);
  }

  if (isHistogram) {
    const step = (right - left) / distribution.bins.length;
    distribution.bins.forEach((bin, i) => {
      if (!bin.length) return;
      ctx.fillStyle = bin.includes(selected) ? colors.manual : colors.series;
      ctx.fillRect(left + i * step + 2, py(bin.length), Math.max(1, step - 4), bottom - py(bin.length));
    });
    canvas.onclick = select ? (event) => {
      const index = distribution.bins[Math.floor((event.offsetX - left) / step)]?.[0];
      if (index !== undefined) select(index);
    } : null;
    return;
  }

  if (times[selected] !== undefined) {
    ctx.strokeStyle = '#a4b7ac';
    ctx.setLineDash([3, 4]);
    line(ctx, [[px(times[selected]), top], [px(times[selected]), bottom]]);
    ctx.setLineDash([]);
  }
  if (reference && analysis) {
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = colors.reference;
    ctx.setLineDash([5, 4]);
    line(ctx, analysis.map((row, i) => row.center === null ? null : [px(times[i]), py(row.center)]));
    ctx.setLineDash([]);
  }
  ctx.strokeStyle = ctx.fillStyle = colors.series;
  ctx.lineWidth = 1.8;
  if (kind === 'line') {
    const coordinates = [];
    values.forEach((value, index) => {
      if (circular && value !== null && values[index - 1] != null && Math.abs(value - values[index - 1]) > 180) {
        coordinates.push(null);
      }
      coordinates.push(value === null ? null : [px(times[index]), py(value)]);
    });
    line(ctx, coordinates);
  }
  const pointSize = values.length > 180 ? 1.6 : 2.8;
  const barWidth = Math.max(1, Math.min(26, (right - left) / Math.max(1, points.length) * 0.64));
  values.forEach((value, index) => {
    const x = px(times[index]);
    ctx.fillStyle = colors.series;
    if (value === null) return;
    const y = py(value);
    if (kind === 'bar') ctx.fillRect(x - barWidth / 2, Math.min(y, py(0)), barWidth, Math.max(1, Math.abs(y - py(0))));
    if (kind !== 'bar' && (kind === 'scatter' || values.length <= 80 || values[index - 1] == null && values[index + 1] == null)) {
      dot(ctx, x, y, pointSize);
    }
  });
  analysis?.forEach((row, index) => {
    const x = px(times[index]);
    if (row.edit && row.edit.action !== 'keep') {
      ctx.strokeStyle = ctx.fillStyle = colors.manual;
      ctx.lineWidth = 1.5;
      dot(ctx, x, py(points[index].value), 4, false);
      if (row.value !== null) dot(ctx, x, py(row.value), 3.5);
    } else if (row.anomaly) {
      ctx.fillStyle = colors.anomaly;
      dot(ctx, x, py(row.value), 4.5);
    }
  });
  if (values[selected] != null) {
    ctx.strokeStyle = colors.series;
    ctx.lineWidth = 1.5;
    dot(ctx, px(times[selected]), py(values[selected]), 6, false);
  }
  canvas.onclick = select ? (event) => {
    if (event.offsetX < left || event.offsetX > right || !times.length) return;
    let low = 0, high = times.length - 1;
    while (low < high) {
      const mid = Math.floor((low + high) / 2);
      if (px(times[mid]) < event.offsetX) low = mid + 1;
      else high = mid;
    }
    if (low && Math.abs(px(times[low - 1]) - event.offsetX) < Math.abs(px(times[low]) - event.offsetX)) low--;
    select(low);
  } : null;
}
