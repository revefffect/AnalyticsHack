const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const VIEW_TITLES = {
  overview: "Обзор ряда",
  quality: "Качество данных",
  risks: "Риски и прогноз",
  annotations: "Экспертная разметка",
  report: "Отчёт для руководителя",
};

const state = {
  catalog: null,
  ai: null,
  spec: {
    station: "KR1",
    parameter: "t_air",
    start: "2024-01-01",
    end: "2025-12-31",
    granularity: "month",
    baseline: true,
    annotations: true,
    risk_focus: null,
    title: null,
  },
  series: null,
  analysis: null,
  quality: null,
  eda: null,
  annotations: [],
  annotationStats: {},
  actions: [],
  narrative: null,
  narrativeKey: null,
  view: "overview",
  quickDate: null,
  loading: false,
};

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

function fmt(value, unit = "", decimals = 1, signed = false) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  const number = Number(value);
  const text = Math.abs(number).toLocaleString("ru-RU", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  const sign = number < 0 ? "−" : signed && number > 0 ? "+" : "";
  return unit ? `${sign}${text} ${unit}` : `${sign}${text}`;
}

function pct(value, decimals = 0) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  return `${(Number(value) * 100).toLocaleString("ru-RU", { minimumFractionDigits: decimals, maximumFractionDigits: decimals })} %`;
}

function plural(n, one, few, many) {
  const abs = Math.abs(n) % 100;
  const last = abs % 10;
  if (abs > 10 && abs < 20) return many;
  if (last === 1) return one;
  if (last >= 2 && last <= 4) return few;
  return many;
}

function shortDate(iso) {
  if (!iso) return "—";
  const [y, m, d] = iso.split("-");
  return `${d}.${m}.${y}`;
}

function monthLabel(iso) {
  if (!iso) return "—";
  const [y, m] = iso.split("-");
  return `${m}.${y}`;
}

function toast(message, error = false) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.toggle("error", error);
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.hidden = true; }, error ? 6000 : 3200);
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const type = response.headers.get("content-type") || "";
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch { /* keep status text */ }
    throw new Error(detail);
  }
  return type.includes("application/json") ? response.json() : response;
}

function stationInfo(code = state.spec.station) {
  return state.catalog?.stations.find((station) => station.code === code);
}

function parameterInfo(code = state.spec.parameter) {
  return stationInfo()?.parameters.find((parameter) => parameter.code === code);
}

function decimals() {
  return state.series?.decimals ?? parameterInfo()?.decimals ?? 1;
}

function unit() {
  return state.series?.unit ?? parameterInfo()?.unit ?? "";
}

function setLoading(loading) {
  state.loading = loading;
  $("#applyFilters").disabled = loading;
  $("#aiApply").disabled = loading;
  $("#aiApply").textContent = loading ? "Собираю…" : "Применить";
}

async function init() {
  try {
    const [catalog, ai] = await Promise.all([api("/api/catalog"), api("/api/ai/status")]);
    state.catalog = catalog;
    state.ai = ai;
    populateControls();
    await loadData();
    renderAIStatus();
    const params = new URLSearchParams(location.hash.slice(1));
    if (params.get("station")) {
      state.spec.station = params.get("station");
      populateControls();
      await loadData();
    }
  } catch (error) {
    $("#finding").textContent = `Не удалось загрузить данные: ${error.message}`;
    toast(`Ошибка загрузки: ${error.message}`, true);
  }
}

function populateControls() {
  const stationSelect = $("#stationSelect");
  stationSelect.innerHTML = state.catalog.stations.map((station) => `<option value="${station.code}">${esc(station.label)} · ${esc(station.place)}</option>`).join("");
  stationSelect.value = state.spec.station;
  populateParameterSelect();
  populateAnnotationSelects();
  updateStationMeta();
  $("#fromInput").value = state.spec.start;
  $("#toInput").value = state.spec.end;
  $("#granSelect").value = state.spec.granularity;
  $("#baselineToggle").checked = state.spec.baseline;
  const info = stationInfo();
  if (info) {
    $("#fromInput").min = info.first;
    $("#fromInput").max = info.last;
    $("#toInput").min = info.first;
    $("#toInput").max = info.last;
  }
}

function populateParameterSelect() {
  const info = stationInfo();
  const select = $("#paramSelect");
  select.innerHTML = info.parameters.map((parameter) => `<option value="${parameter.code}">${esc(parameter.label)} · ${esc(parameter.unit)}</option>`).join("");
  if (!info.parameters.some((parameter) => parameter.code === state.spec.parameter)) {
    state.spec.parameter = info.parameters[0].code;
  }
  select.value = state.spec.parameter;
}

function populateAnnotationSelects() {
  $("#annStation").innerHTML = state.catalog.stations.map((station) => `<option value="${station.code}">${esc(station.label)}</option>`).join("");
  $("#annStation").value = state.spec.station;
  $("#annKind").innerHTML = Object.entries(state.catalog.annotation_kinds).map(([code, label]) => `<option value="${code}">${esc(label)}</option>`).join("");
  $("#annSeverity").innerHTML = Object.entries(state.catalog.severities).map(([code, label]) => `<option value="${code}">${esc(label)}</option>`).join("");
  $("#annSeverity").value = "warning";
  $("#quickKind").innerHTML = $("#annKind").innerHTML;
  $("#quickSeverity").innerHTML = $("#annSeverity").innerHTML;
  $("#quickSeverity").value = "warning";
  populateAnnotationParameters();
}

function populateAnnotationParameters() {
  const station = $("#annStation").value || state.spec.station;
  const info = stationInfo(station);
  $("#annParameter").innerHTML = `<option value="">Вся станция</option>` + info.parameters.map((parameter) => `<option value="${parameter.code}">${esc(parameter.label)}</option>`).join("");
  $("#annParameter").value = state.spec.parameter;
}

function updateStationMeta() {
  const info = stationInfo();
  if (!info) return;
  $("#stationMeta").textContent = `${info.instrument} · ${info.period} · ${info.place}`;
}

function clampSpecToStation() {
  const info = stationInfo();
  if (!info) return;
  const first = info.first;
  const last = info.last;
  if (state.spec.start < first || state.spec.start > last || state.spec.end < first || state.spec.end > last) {
    const end = new Date(`${last}T00:00:00Z`);
    const start = new Date(end);
    start.setUTCFullYear(start.getUTCFullYear() - 2);
    const startIso = start.toISOString().slice(0, 10);
    state.spec.start = startIso < first ? first : startIso;
    state.spec.end = last;
  }
  if (!info.parameters.some((parameter) => parameter.code === state.spec.parameter)) {
    state.spec.parameter = info.parameters[0].code;
  }
}

function specQuery() {
  return new URLSearchParams({
    station: state.spec.station,
    parameter: state.spec.parameter,
    start: state.spec.start,
    end: state.spec.end,
    granularity: state.spec.granularity,
  });
}

async function loadData() {
  setLoading(true);
  try {
    const query = specQuery();
    const analysisQuery = new URLSearchParams({
      station: state.spec.station,
      parameter: state.spec.parameter,
      start: state.spec.start,
      end: state.spec.end,
    });
    const [series, analysis, annotations] = await Promise.all([
      api(`/api/series?${query}`),
      api(`/api/analysis?${analysisQuery}`),
      api(`/api/annotations?station=${encodeURIComponent(state.spec.station)}`),
    ]);
    state.series = series;
    state.analysis = analysis;
    state.quality = analysis.eda?.quality || null;
    state.eda = analysis.eda;
    state.annotations = annotations.items;
    state.annotationStats = annotations.stats;
    state.actions = analysis.actions || [];
    state.narrative = analysis.narrative;
    state.narrativeKey = narrativeKey();
    renderAll();
  } catch (error) {
    toast(`Не удалось обновить ряд: ${error.message}`, true);
    $("#finding").textContent = `Ошибка: ${error.message}`;
  } finally {
    setLoading(false);
  }
}

function narrativeKey() {
  return JSON.stringify({ station: state.spec.station, parameter: state.spec.parameter, start: state.spec.start, end: state.spec.end });
}

function relevantAnnotations() {
  return state.annotations.filter((item) => !item.parameter || item.parameter === state.spec.parameter);
}

function renderAll() {
  if (!state.series) return;
  renderFinding();
  renderHero();
  renderKpis();
  renderChart($("#chart"), state.series);
  renderInsights();
  renderOverviewAnnotations();
  renderMonthlyTable();
  renderQuality();
  renderRisks();
  renderAnnotationList();
  renderReport();
  $("#pageTitle").textContent = VIEW_TITLES[state.view] || "Обзор ряда";
}

function renderFinding() {
  const analysis = state.analysis;
  const quality = state.quality;
  const period = analysis?.period_quality || { excluded_months: [], exclusion_reasons: {} };
  const relevant = relevantAnnotations();
  let text = "";
  if (state.view === "quality") {
    if (quality?.excluded_months?.length) {
      const reasons = Object.entries(quality.exclusion_reasons || {}).map(([key, value]) => `${key} — ${value}`).join(", ");
      text = `Весь архив: ${quality.excluded_months.length} ${plural(quality.excluded_months.length, "месяц", "месяца", "месяцев")} вне правила полноты${reasons ? ` (${reasons})` : ""}. В выбранном периоде — ${period.excluded_months.length}.`;
    } else {
      text = "Все месяцы архива проходят правило полноты.";
    }
  } else if (state.view === "risks") {
    const risks = analysis?.risks?.risks || [];
    const top = [...risks].sort((a, b) => b.score - a.score)[0];
    text = top ? `${top.label}: ${Math.round(top.score)}/100 по ${analysis.risks.year} году. ${top.evidence[0]}.` : "Риски не рассчитаны: мало валидных лет.";
  } else if (state.view === "annotations") {
    const critical = state.annotations.filter((item) => item.severity === "critical").length;
    text = `${state.annotations.length} ${plural(state.annotations.length, "отметка", "отметки", "отметок")} по станции ${stationInfo()?.label || state.spec.station}; ${critical} критичных. Для текущего ряда — ${relevant.length}.`;
  } else if (state.view === "report") {
    text = `PDF соберёт ряд, риски, прогноз, ${relevant.length} ${plural(relevant.length, "отметку", "отметки", "отметок")} и ограничения данных.`;
  } else {
    if (period.excluded_months.length) {
      const last = period.excluded_months[period.excluded_months.length - 1];
      text = `${parameterInfo()?.label || state.spec.parameter}: ${period.excluded_months.length} ${plural(period.excluded_months.length, "месяц", "месяца", "месяцев")} вне правила полноты в выбранном периоде. Последний — ${String(last.month).padStart(2, "0")}.${last.year}.`;
    }
    if (analysis?.insights?.length && !period.excluded_months.length) {
      const priority = analysis.insights.find((item) => item.severity === "critical") || analysis.insights.find((item) => item.severity === "warning") || analysis.insights[0];
      text = `${priority.title}: ${priority.text}`;
    }
    if (!text) text = "Ряд рассчитан по правилам полноты; пустые ячейки остаются пропусками.";
  }
  $("#finding").textContent = text;
}

function renderHero() {
  const series = state.series;
  const summary = series.summary;
  const dec = decimals();
  $("#heroLabel").textContent = "Среднее за выбранный период";
  $("#heroValue").textContent = fmt(summary.mean, series.unit, dec);
  if (summary.deviation !== null && summary.deviation !== undefined) {
    $("#heroDelta").innerHTML = `${esc(fmt(summary.deviation, series.unit, dec, true))} <span class="muted">к среднему за ${summary.baseline_years} ${plural(summary.baseline_years, "год", "года", "лет")}</span>`;
  } else {
    $("#heroDelta").innerHTML = `<span class="muted">База сравнения недоступна</span>`;
  }
  $("#heroNote").textContent = `${summary.valid_points} ${plural(summary.valid_points, "валидная точка", "валидные точки", "валидных точек")} · покрытие ${pct(summary.coverage)}`;
  const items = [
    { color: "var(--accent)", label: series.label },
    { color: "#8a8a8a", label: `Среднее для календарного месяца`, dash: true },
    { color: "var(--warning)", label: "Аномалия", dot: true },
    { color: "var(--negative)", label: "Экспертная отметка" },
  ];
  $("#chartLegend").innerHTML = items.map((item) => `<div class="legend-item"><span class="legend-swatch ${item.dot ? "dot" : ""} ${item.dash ? "dash" : ""}" style="background:${item.color}"></span>${esc(item.label)}</div>`).join("");
}

function renderKpis() {
  const summary = state.series.summary;
  const trend = summary.trend_per_decade;
  const cells = [
    ["Валидных точек", `${summary.valid_points} / ${summary.points}`, `${plural(summary.valid_points, "точка", "точки", "точек")} в расчёте`],
    ["Покрытие календаря", pct(summary.coverage), `из ${summary.points} ${plural(summary.points, "периода", "периодов", "периодов")}`],
    ["База сравнения", summary.baseline === null || summary.baseline === undefined ? "—" : fmt(summary.baseline, state.series.unit, decimals()), `${summary.baseline_years} ${plural(summary.baseline_years, "год", "года", "лет")} с валидными месяцами`],
    ["Тренд", trend ? `${fmt(trend.per_decade, `${state.series.unit}/10 лет`, 2, true)}` : "—", trend ? `R² ${trend.r2?.toFixed(2)} · ${trend.points} мес.` : "нужно ≥ 5 лет"],
  ];
  $("#kpiStrip").innerHTML = cells.map(([label, value, note]) => `<div class="kpi"><div class="kpi-label">${esc(label)}</div><div class="kpi-value num">${esc(value)}</div><div class="kpi-note">${esc(note)}</div></div>`).join("");
}

function renderInsights() {
  const items = state.analysis?.insights || [];
  $("#insightCount").textContent = `${items.length} ${plural(items.length, "наблюдение", "наблюдения", "наблюдений")}`;
  $("#insights").innerHTML = items.map((item) => `
    <div class="insight">
      <div class="insight-head"><span class="pill ${esc(item.severity)}">${esc(item.severity === "critical" ? "критично" : item.severity === "warning" ? "внимание" : "инфо")}</span><span class="insight-title">${esc(item.title)}</span></div>
      <div class="insight-text">${esc(item.text)}</div>
    </div>`).join("") || `<div class="empty">В выбранном периоде нет выраженных сигналов.</div>`;
}

function annotationRows(items, compact = false) {
  if (!items.length) return `<div class="empty">Экспертных отметок за выбранный период нет. Клик по графику создаёт первую.</div>`;
  const shown = compact ? items.slice(0, 4) : items;
  return shown.map((item) => {
    const period = item.start_date === item.end_date ? shortDate(item.start_date) : `${shortDate(item.start_date)} — ${shortDate(item.end_date)}`;
    return `<div class="annotation-row">
      <div class="annotation-date">${esc(period)}</div>
      <div>
        <div><span class="pill kind-${esc(item.kind)}">${esc(item.kind_label)}</span></div>
        <div class="annotation-title">${esc(item.title)}</div>
        ${item.note ? `<div class="annotation-note">${esc(item.note)}</div>` : ""}
        <div class="annotation-author">${esc(item.author)}${item.parameter_label ? ` · ${esc(item.parameter_label)}` : ""}</div>
      </div>
      <div class="annotation-actions"><button class="icon-button" type="button" data-delete-annotation="${item.id}" aria-label="Удалить отметку">Удалить</button></div>
    </div>`;
  }).join("");
}

function renderOverviewAnnotations() {
  $("#overviewAnnotations").innerHTML = annotationRows(relevantAnnotations(), true);
}

function renderAnnotationList() {
  const items = state.annotations;
  $("#annotationList").innerHTML = annotationRows(items);
  const critical = items.filter((item) => item.severity === "critical").length;
  $("#annotationCount").textContent = `${items.length} ${plural(items.length, "отметка", "отметки", "отметок")} по станции`;
  $("#annotationStats").textContent = `${items.length} всего · ${critical} ${plural(critical, "критичная", "критичных", "критичных")} · ${items.filter((item) => item.seed).length} ${plural(items.filter((item) => item.seed).length, "пример", "примера", "примеров")}`;
}

function renderMonthlyTable() {
  const rows = state.series?.months || [];
  if (!rows.length) {
    $("#monthlyTable").innerHTML = `<div class="empty">Нет месячных агрегатов за выбранный период.</div>`;
    return;
  }
  const body = rows.map((row) => `
    <tr>
      <td>${esc(row.label)}</td>
      <td class="num">${row.mean === null ? "—" : esc(fmt(row.mean, state.series.unit, decimals()))}</td>
      <td class="num">${row.valid_days} / ${row.days_in_month}</td>
      <td class="num">${row.incomplete_days}</td>
      <td class="num">${row.longest_gap}</td>
      <td><span class="status-dot ${row.month_valid ? "" : "bad"}"></span>${row.month_valid ? "валиден" : "исключён"}</td>
    </tr>`).join("");
  $("#monthlyTable").innerHTML = `<table><thead><tr><th>Месяц</th><th class="num">Среднее</th><th class="num">Валидных дней</th><th class="num">Пропущено</th><th class="num">Макс. серия</th><th>Правило</th></tr></thead><tbody>${body}</tbody></table>`;
}

function renderQuality() {
  const quality = state.quality;
  if (!quality) return;
  const months = quality.months || [];
  const years = [...new Set(months.map((item) => item.year))];
  const map = new Map(months.map((item) => [`${item.year}-${item.month}`, item]));
  let html = `<div class="heat-month"></div>` + Array.from({ length: 12 }, (_, index) => `<div class="heat-month">${String(index + 1).padStart(2, "0")}</div>`).join("");
  for (const year of years) {
    html += `<div class="heat-year">${year}</div>`;
    for (let month = 1; month <= 12; month += 1) {
      const item = map.get(`${year}-${month}`);
      if (!item) {
        html += `<div class="heat-cell" style="background:var(--raised);color:var(--muted)" title="${year}-${String(month).padStart(2, "0")}: нет данных">—</div>`;
      } else {
        const coverage = item.coverage || 0;
        const ink = coverage > 0.58 ? "#fff" : "var(--text)";
        html += `<div class="heat-cell" style="background:rgba(29,95,191,${(0.07 + coverage * 0.82).toFixed(3)});color:${ink}" title="${year}-${String(month).padStart(2, "0")}: ${item.valid_days} из ${item.days_in_month} дней, серия ${item.longest_gap}">${Math.round(coverage * 100)}</div>`;
      }
    }
  }
  $("#coverageHeatmap").innerHTML = html;
  $("#coverageNote").textContent = `весь архив · ${years.length} ${plural(years.length, "год", "года", "лет")} · ${quality.excluded_months.length} ${plural(quality.excluded_months.length, "месяц", "месяца", "месяцев")} вне правила`;
  const gapInput = $("#gapDate");
  const info = stationInfo();
  if (info) { gapInput.min = info.first; gapInput.max = info.last; }
  if (!gapInput.value || gapInput.value < state.spec.start || gapInput.value > state.spec.end) {
    const first = quality.excluded_months[0];
    gapInput.value = first ? `${first.year}-${String(first.month).padStart(2, "0")}-15` : state.spec.end;
  }

  const reasons = Object.entries(quality.exclusion_reasons || {});
  $("#exclusionReasons").innerHTML = reasons.length
    ? reasons.map(([reason, count]) => `<div class="reason"><span>${esc(reason)}</span><span>${count}</span></div>`).join("")
      + (quality.excluded_months.slice(-6).reverse().map((item) => `<div class="reason"><span>${String(item.month).padStart(2, "0")}.${item.year} · ${item.valid_days}/${item.days_in_month} дней</span><span>серия ${item.longest_gap}</span></div>`).join(""))
    : `<div class="empty">Все месяцы проходят правило полноты.</div>`;

  const flags = Object.entries(quality.flags || {});
  $("#flagTable").innerHTML = flags.length
    ? `<table><thead><tr><th>Флаг</th><th class="num">Записей</th></tr></thead><tbody>${flags.map(([flag, count]) => `<tr><td>${esc(flag)}</td><td class="num">${count.toLocaleString("ru-RU")}</td></tr>`).join("")}</tbody></table>`
    : `<div class="empty">Для этого параметра флагов нет.</div>`;

  renderEda();
}

async function estimateGap() {
  const day = $("#gapDate").value;
  if (!day) { toast("Выберите дату", true); return; }
  $("#gapStatus").textContent = "Считаю…";
  $("#gapEstimate").disabled = true;
  try {
    const data = await api(`/api/gap-fill?station=${encodeURIComponent(state.spec.station)}&parameter=${encodeURIComponent(state.spec.parameter)}&day=${encodeURIComponent(day)}`);
    renderGap(data);
    $("#gapStatus").textContent = "Готово";
  } catch (error) {
    $("#gapStatus").textContent = "";
    toast(`Не удалось оценить пропуск: ${error.message}`, true);
  } finally {
    $("#gapEstimate").disabled = false;
  }
}

function renderGap(data) {
  const unitLabel = data.unit || unit();
  const cards = [];
  if (data.observed !== null && data.observed !== undefined) {
    cards.push(`<div class="gap-card"><h3>Наблюдение</h3><div class="big num">${esc(fmt(data.observed, unitLabel, decimals()))}</div><p>Валидное измерение станции. Оценка не требуется.</p></div>`);
  } else {
    const clim = data.climatology;
    cards.push(`<div class="gap-card"><h3>Климатология</h3><div class="big num">${clim ? esc(fmt(clim.value, unitLabel, decimals())) : "—"}</div><p>${clim ? `± ${esc(fmt(clim.std, "", decimals()))} · ${clim.count} ${plural(clim.count, "наблюдение", "наблюдения", "наблюдений")} · ${esc(clim.basis)}` : "Мало валидных наблюдений для оценки."}</p></div>`);
    const neighbor = data.neighbor;
    cards.push(`<div class="gap-card"><h3>Соседняя точка</h3><div class="big num">${neighbor ? esc(fmt(neighbor.value, unitLabel, decimals())) : "—"}</div><p>${neighbor ? `${esc(neighbor.station_label)}. ${esc(neighbor.note)}` : "Нет валидного значения того же прибора в этот день."}</p></div>`);
    const external = data.external;
    cards.push(`<div class="gap-card"><h3>Внешний реанализ</h3><div class="big num">${external && external.value !== null && external.value !== undefined ? esc(fmt(external.value, unitLabel, decimals())) : "—"}</div><p>${esc(external?.source || "Недоступно")}${external?.note ? ` ${esc(external.note)}` : ""}</p></div>`);
  }
  cards.push(`<div class="gap-recommendation">${esc(data.recommendation)}</div>`);
  $("#gapResult").innerHTML = cards.join("");
}

function renderEda() {
  const eda = state.eda;
  if (!eda) return;
  const dist = eda.distribution || {};
  const correlations = eda.correlations || [];
  const anomalies = eda.anomalies || [];
  const quantiles = Object.entries(dist.quantiles || {});
  $("#edaContent").innerHTML = `
    <div class="eda-block">
      <h3>Распределение суточных средних</h3>
      <div class="big num">${fmt(dist.mean, unit(), decimals())}</div>
      <div class="meta">${dist.count} валидных суток · σ ${fmt(dist.std, unit(), decimals())}</div>
      <ul>
        <li><span>Минимум</span><span>${fmt(dist.min, unit(), decimals())}</span></li>
        <li><span>Максимум</span><span>${fmt(dist.max, unit(), decimals())}</span></li>
        ${quantiles.map(([q, value]) => `<li><span>Квантиль ${q}</span><span>${fmt(value, unit(), decimals())}</span></li>`).join("")}
      </ul>
    </div>
    <div class="eda-block">
      <h3>Связи с другими параметрами</h3>
      ${correlations.length ? `<ul>${correlations.map((item) => `<li><span>${esc(item.label)}</span><span>r = ${item.r.toFixed(2)} · n ${item.n}</span></li>`).join("")}</ul>` : `<div class="empty">Недостаточно общих валидных суток.</div>`}
    </div>
    <div class="eda-block">
      <h3>Аномалии месяцев</h3>
      ${anomalies.length ? `<ul>${anomalies.slice(0, 7).map((item) => `<li><span>${esc(item.label)} · ${fmt(item.value, unit(), decimals())}</span><span>${item.z > 0 ? "+" : ""}${item.z.toFixed(1)} σ</span></li>`).join("")}</ul>` : `<div class="empty">Аномалий выше 2,5 устойчивых σ нет.</div>`}
    </div>`;
}

function renderRisks() {
  const analysis = state.analysis;
  if (!analysis) return;
  const risks = analysis.risks?.risks || [];
  $("#riskMeta").textContent = analysis.risks?.year ? `Год ${analysis.risks.year} · сравнение с ${analysis.risks.years_compared} ${plural(analysis.risks.years_compared, "годом", "годами", "годами")} архива` : "";
  $("#riskCards").innerHTML = risks.sort((a, b) => b.score - a.score).map((risk) => `
    <article class="risk-card ${risk.level} ${state.spec.risk_focus === risk.key ? "focus" : ""}">
      <div class="risk-head"><span class="risk-title">${esc(risk.label)}</span><span class="risk-score num">${Math.round(risk.score)}<span class="meta">/100</span></span></div>
      <div class="risk-level">${esc(risk.level === "high" ? "высокий" : risk.level === "moderate" ? "умеренный" : "низкий")} · ${esc(risk.year)}</div>
      <div class="risk-bar"><i style="width:${Math.max(2, Math.min(100, risk.score))}%"></i></div>
      <ul class="risk-evidence">${risk.evidence.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>
      <p class="risk-note">${esc(risk.note)}</p>
    </article>`).join("") || `<div class="empty">Риски не рассчитаны: мало валидных лет.</div>`;

  const forecast = analysis.forecast?.statistical;
  $("#forecastMethod").textContent = forecast?.method ? `модель: ${forecast.method}` : "";
  $("#forecastTable").innerHTML = forecast?.points?.length
    ? `<table><thead><tr><th>Месяц</th><th class="num">Оценка</th><th class="num">Интервал</th></tr></thead><tbody>${forecast.points.map((point) => `<tr><td>${esc(point.label)}</td><td class="num">${esc(fmt(point.value, forecast.unit, decimals()))}</td><td class="num">${esc(fmt(point.lower, "", decimals()))} … ${esc(fmt(point.upper, forecast.unit, decimals()))}</td></tr>`).join("")}</tbody></table>`
    : `<div class="empty">Недостаточно валидных месяцев для статистического прогноза.</div>`;
  renderForecastChart(forecast);

  const external = analysis.forecast?.external;
  if (external?.available && external.points?.length) {
    $("#externalForecast").innerHTML = `<table><thead><tr><th>Дата</th><th class="num">T ср.</th><th class="num">T мин/макс</th><th class="num">RH</th><th class="num">Ветер</th></tr></thead><tbody>${external.points.map((point) => `<tr><td>${esc(point.label)}</td><td class="num">${point.t_mean} °C</td><td class="num">${point.t_min} / ${point.t_max}</td><td class="num">${Math.round(point.rh_mean)} %</td><td class="num">${point.wind_max} м/с</td></tr>`).join("")}</tbody></table>`;
    $("#externalNote").textContent = `${external.source}. ${external.note}`;
  } else {
    $("#externalForecast").innerHTML = `<div class="empty">Внешний прогноз недоступен.</div>`;
    $("#externalNote").textContent = external?.reason || "Проверьте сеть; статистический прогноз остаётся доступен.";
  }

  const automl = analysis.forecast?.statistical?.automl;
  $("#automlTable").innerHTML = automl?.candidates?.length
    ? `<table><thead><tr><th>Модель</th><th class="num">MAE</th><th class="num">Точек бэктеста</th><th>Выбор</th></tr></thead><tbody>${automl.candidates.map((candidate) => `<tr><td>${esc(candidate.label)}</td><td class="num">${candidate.mae}</td><td class="num">${candidate.n}</td><td>${candidate.model === automl.best ? '<span class="pill kind-event">лучшая</span>' : ""}</td></tr>`).join("")}</tbody></table><p class="meta" style="margin:8px 0 0">${esc(automl.note)}</p>`
    : `<div class="empty">Недостаточно данных для автовыбора модели.</div>`;
}

function renderForecastChart(forecast) {
  const container = $("#forecastChart");
  if (!forecast?.points?.length) {
    container.innerHTML = `<div class="empty">Прогноз не построен.</div>`;
    return;
  }
  const history = (state.analysis?.series?.points || []).filter((point) => point.value !== null).slice(-12);
  const future = forecast.points;
  const all = [
    ...history.map((point) => ({ label: point.label, value: point.value, lower: null, upper: null, kind: "history" })),
    ...future.map((point) => ({ label: point.label, value: point.value, lower: point.lower, upper: point.upper, kind: "forecast" })),
  ];
  if (!all.length) {
    container.innerHTML = `<div class="empty">Нет точек для графика.</div>`;
    return;
  }
  const W = 900, H = 230, m = { l: 14, r: 52, t: 14, b: 30 };
  const plotW = W - m.l - m.r, plotH = H - m.t - m.b;
  const values = all.flatMap((point) => [point.value, point.lower, point.upper].filter((value) => Number.isFinite(value)));
  let min = Math.min(...values), max = Math.max(...values);
  if (min === max) { min -= 1; max += 1; }
  const pad = (max - min) * 0.12;
  min -= pad; max += pad;
  const x = (index) => m.l + (all.length === 1 ? plotW / 2 : (index / (all.length - 1)) * plotW);
  const y = (value) => m.t + ((max - value) / (max - min)) * plotH;
  const historyPoints = all.map((point, index) => ({ ...point, index })).filter((point) => point.kind === "history");
  const futurePoints = all.map((point, index) => ({ ...point, index })).filter((point) => point.kind === "forecast");
  const historyPath = historyPoints.map((point, index) => `${index ? "L" : "M"}${x(point.index)},${y(point.value)}`).join("");
  const futurePath = futurePoints.map((point, index) => `${index ? "L" : "M"}${x(point.index)},${y(point.value)}`).join("");
  const band = futurePoints.length
    ? `M${x(futurePoints[0].index)},${y(futurePoints[0].upper)} ` + futurePoints.map((point) => `L${x(point.index)},${y(point.upper)}`).join(" ") + " " + [...futurePoints].reverse().map((point) => `L${x(point.index)},${y(point.lower)}`).join(" ") + " Z"
    : "";
  const ticks = 4;
  let grid = "";
  for (let index = 0; index <= ticks; index += 1) {
    const value = min + ((max - min) * index) / ticks;
    const yy = y(value);
    grid += `<line class="gridline" x1="${m.l}" x2="${W - m.r}" y1="${yy}" y2="${yy}"/><text class="axis-label" x="${W - 4}" y="${yy + 4}" text-anchor="end">${value.toFixed(1)}</text>`;
  }
  const labelIndexes = [...new Set([0, Math.floor(all.length / 3), Math.floor((2 * all.length) / 3), all.length - 1])];
  const labels = labelIndexes.map((index) => `<text class="axis-label" x="${x(index)}" y="${H - 6}" text-anchor="${index === 0 ? "start" : index === all.length - 1 ? "end" : "middle"}">${esc(all[index].label)}</text>`).join("");
  const divider = futurePoints.length ? `<line x1="${x(futurePoints[0].index)}" x2="${x(futurePoints[0].index)}" y1="${m.t}" y2="${H - m.b}" stroke="var(--border)" stroke-dasharray="3 3"/>` : "";
  container.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Статистический прогноз">
    ${grid}${divider}${band ? `<path d="${band}" fill="rgba(29,95,191,0.10)"/>` : ""}
    ${historyPath ? `<path class="baseline-line" d="${historyPath}" style="stroke:#8a8a8a;stroke-dasharray:none"/>` : ""}
    ${futurePath ? `<path class="series-line" d="${futurePath}" style="stroke-dasharray:5 4"/>` : ""}
    ${futurePoints.map((point) => `<circle cx="${x(point.index)}" cy="${y(point.value)}" r="2.6" fill="var(--accent)"/>`).join("")}
    ${labels}
  </svg>`;
}

function renderChart(container, series) {
  const points = series.points || [];
  if (!points.length || points.every((point) => point.value === null)) {
    container.innerHTML = `<div class="empty" style="padding:40px 0">Нет валидных значений за выбранный период. Пустая ячейка означает пропуск, а не ноль.</div>`;
    return;
  }
  const W = 900, H = 330, m = { l: 16, r: 56, t: 22, b: 38 };
  const plotW = W - m.l - m.r, plotH = H - m.t - m.b;
  const values = points.flatMap((point) => [point.value, state.spec.baseline ? point.baseline : null]).filter((value) => Number.isFinite(value));
  let min = Math.min(...values), max = Math.max(...values);
  if (min === max) { min -= 1; max += 1; }
  const pad = (max - min) * 0.1;
  min -= pad; max += pad;
  const x = (index) => m.l + (points.length === 1 ? plotW / 2 : (index / (points.length - 1)) * plotW);
  const y = (value) => m.t + ((max - value) / (max - min)) * plotH;

  let grid = "";
  for (let index = 0; index <= 4; index += 1) {
    const value = min + ((max - min) * index) / 4;
    const yy = y(value);
    grid += `<line class="gridline" x1="${m.l}" x2="${W - m.r}" y1="${yy}" y2="${yy}"/><text class="axis-label" x="${W - 4}" y="${yy + 4}" text-anchor="end">${value.toFixed(1)}</text>`;
  }
  if (min < 0 && max > 0) {
    grid += `<line x1="${m.l}" x2="${W - m.r}" y1="${y(0)}" y2="${y(0)}" stroke="var(--border)"/>`;
  }

  const segments = [];
  let path = "";
  let open = false;
  points.forEach((point, index) => {
    if (point.value === null || point.value === undefined) {
      open = false;
      if (path) { segments.push(path); path = ""; }
      return;
    }
    path += `${open ? "L" : "M"}${x(index)},${y(point.value)}`;
    open = true;
  });
  if (path) segments.push(path);

  let baseline = "";
  if (state.spec.baseline) {
    let baselineOpen = false;
    points.forEach((point, index) => {
      if (!Number.isFinite(point.baseline)) {
        baselineOpen = false;
        return;
      }
      baseline += `${baselineOpen ? "L" : "M"}${x(index)},${y(point.baseline)}`;
      baselineOpen = true;
    });
  }

  const anomalies = points
    .map((point, index) => ({ point, index }))
    .filter(({ point }) => Number.isFinite(point.anomaly));
  const anomalyMean = anomalies.length ? anomalies.reduce((sum, item) => sum + item.point.anomaly, 0) / anomalies.length : 0;
  const anomalyStd = anomalies.length ? Math.sqrt(anomalies.reduce((sum, item) => sum + (item.point.anomaly - anomalyMean) ** 2, 0) / anomalies.length) : 0;
  const anomalyThreshold = Math.max(1, anomalyStd * 2);
  const dots = anomalies.filter(({ point }) => Math.abs(point.anomaly) >= anomalyThreshold).map(({ point, index }) => `<circle class="anomaly-dot" cx="${x(index)}" cy="${y(point.value)}" r="3.2"><title>${esc(point.label)}: ${esc(fmt(point.value, series.unit, decimals()))}, отклонение ${esc(fmt(point.anomaly, series.unit, decimals(), true))}</title></circle>`).join("");

  const validIndexes = points.map((point, index) => point.value !== null ? index : -1).filter((index) => index >= 0);
  const lastIndex = validIndexes[validIndexes.length - 1];
  const lastDot = lastIndex !== undefined ? `<circle class="last-dot" cx="${x(lastIndex)}" cy="${y(points[lastIndex].value)}" r="4"/>` : "";

  const annotations = state.spec.annotations ? relevantAnnotations().filter((item) => overlaps(item, series.start, series.end)) : [];
  const annotationMarks = annotations.slice(0, 6).map((item, order) => {
    const index = nearestIndex(points, item.start_date);
    if (index < 0) return "";
    const color = item.severity === "critical" ? "var(--negative)" : item.severity === "warning" ? "var(--warning)" : "var(--accent)";
    const yy = m.t + 10 + (order % 3) * 12;
    return `<line class="annotation-line" x1="${x(index)}" x2="${x(index)}" y1="${m.t}" y2="${H - m.b}" stroke="${color}"/><text class="annotation-label" x="${x(index)}" y="${yy}" text-anchor="middle" fill="${color}">${esc(item.kind_label)}</text>`;
  }).join("");

  const labelIndexes = [...new Set([0, Math.floor(points.length / 5), Math.floor((2 * points.length) / 5), Math.floor((3 * points.length) / 5), Math.floor((4 * points.length) / 5), points.length - 1])];
  const labels = labelIndexes.map((index) => {
    const point = points[index];
    const text = state.spec.granularity === "day" ? point.label.slice(0, 5) : point.label;
    const anchor = index === 0 ? "start" : index === points.length - 1 ? "end" : "middle";
    return `<text class="axis-label" x="${x(index)}" y="${H - 8}" text-anchor="${anchor}">${esc(text)}</text>`;
  }).join("");

  container.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(series.label)} с ${esc(series.start)} по ${esc(series.end)}">
    ${grid}
    ${baseline ? `<path class="baseline-line" d="${baseline}"/>` : ""}
    ${segments.map((segment) => `<path class="series-line" d="${segment}"/>`).join("")}
    ${annotationMarks}${dots}${lastDot}${labels}
    <line class="hover-line" x1="0" x2="0" y1="${m.t}" y2="${H - m.b}" visibility="hidden"/>
    <circle class="hover-dot" cx="0" cy="0" r="4" visibility="hidden"/>
    <rect x="${m.l}" y="${m.t}" width="${plotW}" height="${plotH}" fill="transparent" data-chart-hover="1"/>
  </svg>`;

  const svg = container.querySelector("svg");
  const hoverLine = svg.querySelector(".hover-line");
  const hoverDot = svg.querySelector(".hover-dot");
  const tooltip = $("#tooltip");
  const plotRect = svg.querySelector("rect[data-chart-hover]");

  function locate(event) {
    const bounds = svg.getBoundingClientRect();
    const scale = W / bounds.width;
    const localX = (event.clientX - bounds.left) * scale;
    const ratio = Math.max(0, Math.min(1, (localX - m.l) / plotW));
    const index = Math.round(ratio * (points.length - 1));
    return { index, localX, bounds };
  }

  plotRect.addEventListener("mousemove", (event) => {
    const { index, localX, bounds } = locate(event);
    const point = points[index];
    hoverLine.setAttribute("x1", x(index));
    hoverLine.setAttribute("x2", x(index));
    hoverLine.setAttribute("visibility", "visible");
    if (point.value !== null) {
      hoverDot.setAttribute("cx", x(index));
      hoverDot.setAttribute("cy", y(point.value));
      hoverDot.setAttribute("visibility", "visible");
    } else {
      hoverDot.setAttribute("visibility", "hidden");
    }
    const parts = [`<b>${esc(point.label)}</b>`, `${esc(fmt(point.value, series.unit, decimals()))}`];
    if (Number.isFinite(point.baseline)) parts.push(`среднее: ${esc(fmt(point.baseline, series.unit, decimals()))}`);
    if (Number.isFinite(point.anomaly)) parts.push(`отклонение: ${esc(fmt(point.anomaly, series.unit, decimals(), true))}`);
    if (point.coverage !== undefined) parts.push(`покрытие: ${pct(point.coverage)}`);
    if (point.valid_days !== undefined) parts.push(`дней: ${point.valid_days}/${point.days_in_month}`);
    if (!point.valid) parts.push(`<span style="color:var(--muted)">пропуск / исключено</span>`);
    tooltip.innerHTML = parts.join("<br>");
    tooltip.hidden = false;
    const tooltipWidth = 240;
    const left = event.clientX + 14 + tooltipWidth > window.innerWidth ? event.clientX - tooltipWidth - 14 : event.clientX + 14;
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${event.clientY + 14}px`;
  });
  plotRect.addEventListener("mouseleave", () => {
    tooltip.hidden = true;
    hoverLine.setAttribute("visibility", "hidden");
    hoverDot.setAttribute("visibility", "hidden");
  });
  plotRect.addEventListener("click", (event) => {
    const { index } = locate(event);
    const point = points[index];
    if (!point || point.value === null) return;
    openQuickAnnotation(point.date);
  });
}

function overlaps(item, start, end) {
  return item.end_date >= start && item.start_date <= end;
}

function nearestIndex(points, iso) {
  if (!iso || !points.length) return -1;
  let best = 0;
  let bestDistance = Infinity;
  points.forEach((point, index) => {
    const distance = Math.abs(new Date(`${point.date}T00:00:00`) - new Date(`${iso}T00:00:00`));
    if (distance < bestDistance) { bestDistance = distance; best = index; }
  });
  return best;
}

function renderAIStatus() {
  const badge = $("#aiBadge");
  if (state.ai?.available) {
    badge.textContent = `DeepSeek: ${state.ai.model}`;
    badge.classList.add("online");
    badge.classList.remove("error");
    $("#aiNote").textContent = "DeepSeek подключён. Модель выбирает срез; числа и проверки считает сервер.";
  } else {
    badge.textContent = "DeepSeek: локальный режим";
    badge.classList.remove("online", "error");
    $("#aiNote").textContent = "Локальный разбор запроса. Ключ DeepSeek можно добавить в настройках.";
  }
}

function renderReport() {
  const analysis = state.analysis;
  if (!analysis) return;
  $("#reportMeta").textContent = `${stationInfo()?.label || state.spec.station} · ${parameterInfo()?.label || state.spec.parameter} · ${shortDate(state.spec.start)} — ${shortDate(state.spec.end)}`;
  $("#reportNarrative").textContent = state.narrative || analysis.narrative || "Текст отчёта появится после расчёта.";
  $("#reportContents").innerHTML = [
    "Ряд, KPI и проверка полноты",
    "Риски сезона с ограничениями",
    "Статистический прогноз и бэктест",
    `Экспертная разметка: ${relevantAnnotations().length} ${plural(relevantAnnotations().length, "отметка", "отметки", "отметок")}`,
    "Методика и контрольные числа",
  ].map((item) => `<li>${esc(item)}</li>`).join("");
  const actions = state.actions?.length ? state.actions : analysis.actions || [];
  $("#reportActions").innerHTML = actions.map((action) => `<div class="action"><b>${esc(action.title)}.</b> ${esc(action.text)} <span class="meta">${esc(action.risk)}</span></div>`).join("") || `<div class="empty">Действий выше порога нет.</div>`;
}

async function applyAI() {
  const prompt = $("#aiPrompt").value.trim();
  if (!prompt) {
    toast("Опишите, что показать.", true);
    return;
  }
  setLoading(true);
  try {
    const result = await api("/api/ai/dashboard", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, current: state.spec }),
    });
    state.spec = { ...state.spec, ...result.spec };
    if (!result.spec.title && result.explanation) state.spec.title = result.spec.title;
    populateControls();
    await loadData();
    const source = result.source === "deepseek" ? "DeepSeek" : "локальный разбор";
    $("#aiNote").textContent = `${source}: ${result.explanation}${result.error ? ` Ошибка API: ${result.error}` : ""}`;
    toast("Дашборд обновлён");
  } catch (error) {
    toast(`Не удалось применить запрос: ${error.message}`, true);
  } finally {
    setLoading(false);
  }
}

async function loadNarrative(force = false) {
  if (!force && state.narrative && state.narrativeKey === narrativeKey()) return;
  $("#reportStatus").textContent = "Считаю текст…";
  try {
    const result = await api("/api/ai/narrative", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ station: state.spec.station, parameter: state.spec.parameter, start: state.spec.start, end: state.spec.end, use_ai: true }),
    });
    state.narrative = result.text;
    state.actions = result.actions || state.actions;
    state.narrativeKey = narrativeKey();
    renderReport();
    $("#reportStatus").textContent = result.source === "deepseek" ? "Текст DeepSeek" : "Локальный текст";
  } catch (error) {
    $("#reportStatus").textContent = "";
    toast(`Не удалось обновить текст: ${error.message}`, true);
  }
}

async function buildReport() {
  const button = $("#buildReport");
  button.disabled = true;
  $("#reportStatus").textContent = "Собираю PDF…";
  try {
    const response = await fetch("/api/report", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ station: state.spec.station, parameter: state.spec.parameter, start: state.spec.start, end: state.spec.end, use_ai: true }),
    });
    if (!response.ok) {
      let detail = response.statusText;
      try { detail = (await response.json()).detail || detail; } catch { /* ignore */ }
      throw new Error(detail);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `krasnoe-${state.spec.station}-${state.spec.parameter}-${state.spec.start}-${state.spec.end}.pdf`;
    link.click();
    URL.revokeObjectURL(url);
    $("#reportStatus").textContent = "PDF готов";
    toast("PDF сформирован");
  } catch (error) {
    $("#reportStatus").textContent = "";
    toast(`Не удалось собрать PDF: ${error.message}`, true);
  } finally {
    button.disabled = false;
  }
}

function openQuickAnnotation(iso) {
  state.quickDate = iso;
  $("#quickAnnotationMeta").textContent = `${stationInfo()?.label} · ${parameterInfo()?.label} · ${shortDate(iso)}`;
  $("#quickTitle").value = "";
  $("#quickNote").value = "";
  $("#annotationDialog").showModal();
  $("#quickTitle").focus();
}

async function saveAnnotation(payload) {
  const result = await api("/api/annotations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  state.annotationStats = result.stats;
  await loadData();
  toast("Отметка добавлена");
}

async function deleteAnnotation(id) {
  if (!confirm("Удалить экспертную отметку?")) return;
  try {
    await api(`/api/annotations/${id}`, { method: "DELETE" });
    await loadData();
    toast("Отметка удалена");
  } catch (error) {
    toast(`Не удалось удалить: ${error.message}`, true);
  }
}

function switchView(view) {
  state.view = view;
  $$(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  $$(".view").forEach((element) => { element.hidden = element.id !== `view-${view}`; });
  $("#pageTitle").textContent = VIEW_TITLES[view] || "Обзор ряда";
  renderFinding();
  if (view === "report") loadNarrative(false);
  if (view === "risks") renderRisks();
  if (view === "quality") renderQuality();
}

function bindEvents() {
  $("#stationSelect").addEventListener("change", async (event) => {
    state.spec.station = event.target.value;
    state.narrative = null;
    clampSpecToStation();
    populateControls();
    await loadData();
  });
  $("#paramSelect").addEventListener("change", async (event) => {
    state.spec.parameter = event.target.value;
    state.narrative = null;
    await loadData();
  });
  $("#granSelect").addEventListener("change", async (event) => {
    state.spec.granularity = event.target.value;
    await loadData();
  });
  $("#baselineToggle").addEventListener("change", (event) => {
    state.spec.baseline = event.target.checked;
    renderChart($("#chart"), state.series);
    renderHero();
  });
  $("#applyFilters").addEventListener("click", async () => {
    state.spec.start = $("#fromInput").value || state.spec.start;
    state.spec.end = $("#toInput").value || state.spec.end;
    state.narrative = null;
    await loadData();
  });
  $$(".nav-item").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
  $$("[data-view-jump]").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.viewJump)));
  $$("#examples .chip").forEach((button) => button.addEventListener("click", () => {
    $("#aiPrompt").value = button.dataset.example;
    applyAI();
  }));
  $("#aiApply").addEventListener("click", applyAI);
  $("#aiPrompt").addEventListener("keydown", (event) => { if (event.key === "Enter") applyAI(); });
  $("#aiSettings").addEventListener("click", () => {
    $("#aiKey").value = "";
    $("#aiBase").value = state.ai?.base || "";
    $("#aiModel").value = state.ai?.model || "";
    $("#aiDialog").showModal();
  });
  $("#aiForm").addEventListener("submit", async (event) => {
    if (event.submitter?.value === "cancel") return;
    event.preventDefault();
    try {
      const status = await api("/api/ai/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: $("#aiKey").value.trim() || null, base: $("#aiBase").value.trim() || null, model: $("#aiModel").value.trim() || null }),
      });
      state.ai = status;
      renderAIStatus();
      $("#aiDialog").close();
      toast(status.available ? "DeepSeek подключён" : "Ключ очищен");
    } catch (error) {
      toast(`Не удалось сохранить ключ: ${error.message}`, true);
    }
  });
  $("#reportButton").addEventListener("click", buildReport);
  $("#buildReport").addEventListener("click", buildReport);
  $("#refreshNarrative").addEventListener("click", () => loadNarrative(true));
  $("#gapEstimate").addEventListener("click", estimateGap);
  $("#annStation").addEventListener("change", populateAnnotationParameters);
  $("#annotationForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {
      station: $("#annStation").value,
      parameter: $("#annParameter").value || null,
      start_date: $("#annStart").value,
      end_date: $("#annEnd").value || $("#annStart").value,
      kind: $("#annKind").value,
      severity: $("#annSeverity").value,
      title: $("#annTitle").value.trim(),
      note: $("#annNote").value.trim(),
      author: $("#annAuthor").value.trim(),
    };
    try {
      await saveAnnotation(payload);
      $("#annTitle").value = "";
      $("#annNote").value = "";
    } catch (error) {
      toast(`Не удалось добавить отметку: ${error.message}`, true);
    }
  });
  $("#quickAnnotationForm").addEventListener("submit", async (event) => {
    if (event.submitter?.value === "cancel") return;
    event.preventDefault();
    const payload = {
      station: state.spec.station,
      parameter: state.spec.parameter,
      start_date: state.quickDate,
      end_date: state.quickDate,
      kind: $("#quickKind").value,
      severity: $("#quickSeverity").value,
      title: $("#quickTitle").value.trim(),
      note: $("#quickNote").value.trim(),
      author: $("#quickAuthor").value.trim(),
    };
    try {
      await saveAnnotation(payload);
      $("#annotationDialog").close();
    } catch (error) {
      toast(`Не удалось добавить отметку: ${error.message}`, true);
    }
  });
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-delete-annotation]");
    if (button) deleteAnnotation(Number(button.dataset.deleteAnnotation));
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      $("#tooltip").hidden = true;
    }
  });
}

document.addEventListener("DOMContentLoaded", async () => {
  bindEvents();
  await init();
  // Prefill the annotation date with the latest valid point once data is loaded.
  if (state.series?.points?.length) {
    const last = [...state.series.points].reverse().find((point) => point.value !== null);
    if (last) {
      $("#annStart").value = last.date;
      $("#annEnd").value = last.date;
    }
  }
  switchView("overview");
});
