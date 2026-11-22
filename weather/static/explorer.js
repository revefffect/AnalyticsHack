const $ = id => document.getElementById(id);
const number = new Intl.NumberFormat("ru-RU", {maximumFractionDigits: 2});
const monthName = new Intl.DateTimeFormat("ru-RU", {month: "long", timeZone: "UTC"});
let archive;

function select(id, options, wanted) {
  const control = $(id);
  control.replaceChildren(...options.map(([value, text]) => new Option(text, value)));
  if (options.some(([value]) => value === wanted)) control.value = wanted;
  return control.value;
}

function statistic(label, value) {
  const group = document.createElement("div");
  const term = document.createElement("dt");
  const detail = document.createElement("dd");
  term.textContent = label;
  detail.textContent = value;
  group.append(term, detail);
  return group;
}

function render(wanted) {
  const sites = Object.entries(archive.sites).map(([id, site]) => [id, site.name]);
  const code = select("site", sites, wanted.station || "kr1");
  const site = archive.sites[code];
  const field = select("metric", site.fields.map(id => [id, archive.metrics[id].name]), wanted.field || "rh");
  const metric = archive.metrics[field];
  const months = Object.keys(site.months).sort();
  const years = [...new Set(months.map(m => m.slice(0, 4)))].reverse();
  const year = select("year", years.map(y => [y, y]), wanted.year || wanted.month?.slice(0, 4));
  const available = months.filter(m => m.startsWith(year));
  const month = select("month", available.map(m => [m, monthName.format(new Date(m + "-01T00:00:00Z"))]),
    year + "-" + (wanted.month?.slice(-2) || "06"));
  const stat = site.months[month][field];
  const start = Date.parse(month + "-01T00:00:00Z");
  const end = Date.UTC(Number(year), Number(month.slice(-2)), 1);
  let label = "Среднее за месяц";
  let value = stat.mean === null ? "Недостаточно данных" : number.format(stat.mean) + " " + metric.unit;
  if (field === "wind_dir") {
    const max = Math.max(...stat.counts);
    label = "Самое частое направление";
    value = max ? archive.sectors.filter((_, i) => stat.counts[i] === max).join(", ") : "Нет измерений";
    $("method").textContent = "График показывает число пригодных измерений в каждом из восьми направлений ветра за день, включая неполные дни.";
  } else {
    $("method").textContent = "Каждая точка — среднее по наблюдениям за сутки. Разрывы показывают дни с недостаточным покрытием. Оценки по соседней станции исключены.";
  }
  $("heading").textContent = metric.name + " · " + site.name;
  $("stats-values").replaceChildren(statistic(label, value),
    statistic("Пригодные сутки", stat.valid + " из " + (end - start) / 86400000));
  const path = "/charts/demo/d-solo/meteo-" + code + "-" + year + "/archive";
  const params = new URLSearchParams({panelId: site.fields.indexOf(field) + 1, theme: "light", timezone: "utc", from: start, to: end - 1});
  const src = path + "?" + params;
  if ($("graph").getAttribute("src") !== src) $("graph").src = src;
  $("graph").title = metric.name + " · " + site.name + " · " + month;
  $("standalone").href = src;
  const url = new URL(location.href);
  url.search = new URLSearchParams({demo: "explore", station: code, field, month});
  history.replaceState(null, "", url);
}

try {
  const response = await fetch("/portal/static/explorer-index.json");
  if (!response.ok) throw new Error();
  archive = await response.json();
  render(Object.fromEntries(new URLSearchParams(location.search)));
  for (const id of ["site", "metric", "year", "month"]) {
    $(id).addEventListener("change", () => render({
      station: $("site").value, field: $("metric").value, year: $("year").value, month: $("month").value
    }));
  }
  $("message").hidden = true;
  $("explorer").hidden = false;
} catch {
  $("message").textContent = "Не удалось загрузить архив. Обновите страницу, чтобы повторить попытку.";
}
