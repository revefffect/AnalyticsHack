"use strict";

const $ = (id) => document.getElementById(id);
const state = {user:null, catalog:[], external:[], preview:null, report:null, selection:null, reportRequest:0, eventsRequest:0};
const fields = {
  datetime:"Дата и время", t_air:"Температура воздуха", pressure_hpa:"Атмосферное давление",
  rh:"Влажность", wind_speed:"Скорость ветра", wind_gust:"Порыв ветра", wind_dir:"Направление ветра",
  soil_05:"Почва, 5 см", soil_10:"Почва, 10 см", soil_20:"Почва, 20 см", qc_flag:"Флаги качества"
};
const stations = {kr1:"Кр1 · Поляна", kr2:"Кр2 · Поле", ame60:"АМЕ-60"};
const names = {
  ...fields, date:"Дата", period:"Период", value:"Значение", valid:"Пригодно", expected:"Ожидается", flags:"Флаги",
  station:"Станция", stations:"Станции", parameter:"Показатель", parameters:"Показатели", unit:"Единица",
  title:"Название", name:"Название", source:"Источник", provider:"Поставщик", territory:"Территория", model:"Модель",
  station_valid_days:"Пригодных дней по станции", parameter_valid_days:"Пригодных дней по показателю",
  valid_days:"Полных дней", days_in_month:"Дней в месяце", mean:"Среднее", valid_slots:"Пригодных сроков",
  station_slots:"Сроков станции", required_slots:"Требуется сроков", status:"Статус", created:"Создано",
  observed_at:"Время наблюдения", received_at:"Время приёма", created_at:"Время события", version:"Версия",
  source_line:"Строка", line:"Строка", method:"Метод", method_version:"Версия методики", data_type:"Тип данных",
  resolution:"Разрешение", updated_at:"Обновление", coverage:"Полнота", license:"Условия использования",
  access:"Доступ", role:"Роль", rows:"Строки", count:"Количество", start:"Начало периода", end:"Конец периода",
  sha256:"SHA-256", timestamp:"Время наблюдения", threshold:"Порог", recipient:"Получатель", rule:"Правило",
  readings:"Наблюдения", valid_for_parameter:"Пригодно", usable_for_parameter:"Пригодно", raw:"Исходные значения",
  records:"Записи", received:"Получено", events:"События", valid_years:"Пригодных лет", dataset:"Набор",
  limitations:"Ограничения", date_from:"Начало", date_to:"Конец", updated:"Обновление",
  range:"Период", format:"Формат", attribution:"Атрибуция", terms:"Условия", url:"Адрес",
  timezone:"Часовой пояс", file:"Исходный файл", full:"Полные сутки", longest_gap:"Максимальная серия пропусков, дней", issues:"Отметки контроля", sectors:"Секторы ветра", interval_minutes:"Частота интервалов, мин",
  integration:"Подключение", completeness:"Полнота", revision:"Ревизия потока", min_slots:"Минимум сроков",duration_minutes:"Длительность, мин",stale_minutes:"Устаревание, мин", mode:"Режим"
};
const statuses = {
  ok:"Пригодно", insufficient_coverage:"Недостаточное покрытие", no_observations:"Нет наблюдений",
  unavailable_parameter:"Показатель отсутствует", insufficient_data:"Недостаточно данных", recovered:"Восстановлено",
  recovery:"Восстановление", stale:"Устаревшие наблюдения", active:"Активно", threshold:"Превышение порога",
  open:"Открыто", resolved:"Завершено", replay:"Воспроизведение", private:"Приватный", public:"Опубликован",
  research:"Исследователь", researcher:"Исследователь", business:"Бизнес", archive:"Архив", reanalysis:"Реанализ",
  external_file:"Внешний файл", manual:"Получение вручную", connector:"Коннектор", api:"API",
  station:"Наблюдения станции", station_observations:"Станционные наблюдения", upper_air_observations:"Аэрологические наблюдения",
  observed:"Пригодное наблюдение", absent_column:"Колонка отсутствует", empty:"Пустая ячейка", flagged:"Исключено по флагу",conflict:"Конфликт срока",
  direction_sectors:"Направления по секторам",unknown:"Неизвестен",live:"Живой поток",firing:"Превышение"
};
const titles = {catalog:"Каталог наборов", import:"Импорт наблюдений", report:"Отчёт по наблюдениям", events:"Уведомления"};
const esc = encodeURIComponent;
const isResearcher = () => ["research","researcher","owner","admin"].includes(state.user?.role);
const num = new Intl.NumberFormat("ru-RU", {maximumFractionDigits:2});
function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = String(text);
  if (cls) node.className = cls;
  return node;
}
function display(value) {
  if (value == null || value === "") return "—";
  if (typeof value === "number") return num.format(value);
  if (typeof value === "boolean") return value ? "Да" : "Нет";
  if (Array.isArray(value)) return value.map(display).join(", ") || "—";
  if (typeof value === "object") return Object.entries(value).map(([k,v]) => (names[k] || k) + " · " + display(v)).join(" / ") || "—";
  return statuses[value] || fields[value] || stations[String(value).toLowerCase()] || String(value);
}
function safeLink(label, href, css) {
  if (typeof href !== "string" || !href.trim()) return null;
  let url;
  try { url = new URL(href, location.href); } catch { return null; }
  if (!["http:","https:"].includes(url.protocol)) return null;
  const node = el("a",label,css);
  node.href = url.href;
  if (url.origin !== location.origin) { node.target="_blank"; node.rel="noopener noreferrer"; }
  return node;
}
function facts(entries) {
  const dl = el("dl",null,"facts");
  for (const [label,value] of entries) {
    if (value == null || value === "") continue;
    dl.append(el("dt",names[label] || label),el("dd",display(value)));
  }
  return dl;
}
function options(select, values, labels={}, desired) {
  select.replaceChildren();
  for (const value of values) {
    const key = typeof value === "object" ? value.id ?? value.code ?? value.value : value;
    if (key == null) continue;
    const text = typeof value === "object" ? value.title || value.name || key : labels[String(key).toLowerCase()] || key;
    const option = el("option",text); option.value = key; select.append(option);
  }
  if ([...select.options].some(o=>o.value===desired)) select.value = desired;
}
let noticeTimer;
function notify(message, error=false) {
  const node=$("notice"); clearTimeout(noticeTimer); node.textContent=message; node.dataset.error=String(error); node.hidden=false;
  noticeTimer=setTimeout(()=>{node.hidden=true;}, error?12000:6000);
}
async function api(path, {body,method="GET",raw,signal}={}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"]="application/json";
  const response = await fetch("/portal"+path,{method,headers,body:raw ?? (body === undefined ? undefined : JSON.stringify(body)),credentials:"same-origin",signal});
  const data = await response.json().catch(()=>null);
  if (!response.ok) {
    if (response.status===401 && state.user) signedOut();
    const detail=data?.detail;
    const message=typeof detail==="string"?detail:Array.isArray(detail)?detail.map(e=>e.msg).join(", "):"Ошибка запроса ("+response.status+").";
    const error=new Error(message); error.status=response.status; throw error;
  }
  return data;
}
async function busy(button,work) {
  if (button?.disabled) return;
  if (button) button.disabled=true;
  try { return await work(); } catch (error) { notify(error.message || "Не удалось выполнить действие.",true); }
  finally { if (button) button.disabled=false; }
}
function button(label, work, css) {
  const b=el("button",label,css); b.type="button"; b.addEventListener("click",()=>busy(b,()=>work(b))); return b;
}
function onForm(id,handler) {
  $(id).addEventListener("submit",event=>{event.preventDefault();void busy(event.submitter,handler);});
}
function openTab(tab) {
  if (!(tab in titles) || (tab==="import" && !isResearcher())) tab="catalog";
  for (const id of Object.keys(titles)) $(id).hidden=id!==tab;
  document.querySelectorAll("[data-tab]").forEach(b=>{if(b.dataset.tab===tab)b.setAttribute("aria-current","page");else b.removeAttribute("aria-current");});
  $("page-title").textContent=titles[tab];
  if (tab==="events") void busy(null,refreshEvents);
}
function signedOut() {
  state.user=null; state.catalog=[]; state.external=[]; state.preview=null; state.report=null; state.selection=null;
  state.reportRequest++; state.eventsRequest++;
  $("workspace").hidden=true; $("login").hidden=false; $("account").hidden=true;
  $("token").value=""; $("confirm-form").hidden=true; $("report-result").hidden=true; $("upload-form").reset();
  for (const id of ["datasets","external","event-list","report-meta","table","file-mappings","evidence-data","profile-data","saved-link","replay-result"]) $(id).replaceChildren();
}
async function loadWorkspace() {
  const user=await api("/me"); state.user=user; $("login").hidden=true; $("workspace").hidden=false; $("account").hidden=false;
  $("account-name").textContent=user.name || display(user.role);
  $("role").textContent=isResearcher()?"Кабинет исследователя":"Бизнес-кабинет";
  $("import-tab").hidden=!isResearcher(); $("event-tools").hidden=!isResearcher();
  $("recipient").value=user.name || ""; await refreshCatalog();
  const saved=new URL(location.href).searchParams.get("saved");
  const params=new URL(location.href).searchParams;
  if (saved) { openTab("report"); await openSaved(saved); }
  else if(params.has("dataset")) {openTab("report");await openReportLink(params);}
  else openTab("catalog");
}
onForm("login-form",async()=>{await api("/session",{method:"POST",body:{token:$("token").value}});$("token").value="";await loadWorkspace();});
$("logout").addEventListener("click",()=>busy($("logout"),async()=>{await api("/session",{method:"DELETE"});history.replaceState(null,"",location.pathname);signedOut();}));
$("tabs").addEventListener("click",e=>{const b=e.target.closest("[data-tab]");if(b) openTab(b.dataset.tab);});

function dataset(id) { return state.catalog.find(d=>d.id===id); }
function stationParameters(prefix) {
  const d=dataset($(prefix+"dataset").value);
  const st=$(prefix+"station"), p=$(prefix+"parameter");
  const defaultStation=d?.stations?.includes("kr1")?"kr1":undefined;
  const defaultParameter=d?.parameters?.includes("t_air")?"t_air":undefined;
  options(st,d?.stations || [],stations,st.value || defaultStation); options(p,d?.parameters || [],fields,p.value || defaultParameter);
  if(!prefix && Array.isArray(d?.period) && d.period.length===2) {
    const [first,last]=d.period, month=$("month");
    if(month.value<first || month.value>last) month.value=first;
  }
  if(!prefix) renderQuestions();
}

function renderQuestions() {
  const box=$("archive-questions");
  box.replaceChildren();
  const presets=[
    ["pressure","Почему у Кр1 за апрель 2024 есть температура, а давление отсутствует?"],
    ["january","Насколько холодным был январь 2010 года?"],
    ["neighbors","Где в 2025 году можно оценить пропуски Кр2?"]
  ];
  for(const [key,title] of presets) {
    const available=d=>d?.connected!==false && d?.archive_features?.includes(key);
    const current=dataset($("dataset").value);
    const source=available(current)?current:state.catalog.find(available);
    if(source) box.append(button(title,()=>answerQuestion(source,key),"text-btn"));
  }
  box.hidden=!box.childElementCount;
}

async function answerQuestion(source,key) {
  $("dataset").value=source.id;stationParameters("");
  const january=key==="january", gaps=key==="neighbors";
  periodMode(gaps?"range":"month");
  $("station").value=january?"ame60":gaps?"kr2":"kr1";
  $("parameter").value=key==="pressure"?"pressure_hpa":"t_air";
  $("step").value="day";$("report-template").value="weather";
  $("month").value=january?"2010-01":"2024-04";
  if(gaps){$("range-start").value="2025-01-01";$("range-end").value="2025-12-31";}
  const r=await loadReport({...reportFilters(),version:source.version});
  if(!r || state.report!==r)return;
  if(gaps){await showEstimates();return;}
  const sel=state.selection, box=$("question-answer");
  const unitValue=(value,unit)=>value==null?"Недостаточно данных":display(value)+" "+unit;
  if(january){
    const params=new URLSearchParams({station:sel.station,parameter:sel.parameter,month:1,first:1992,last:2021,version:r.version,revision:r.revision});
    const ref=await api("/datasets/"+esc(sel.dataset)+"/climatology?"+params);
    if(state.report!==r)return;
    const difference=r.summary.value==null || ref.value==null?null:r.summary.value-ref.value;
    box.replaceChildren(el("h3","Январь 2010 года на АМЕ-60"),facts([
      ["Среднее за январь 2010",unitValue(r.summary.value,r.unit)],["Среднее января за 1992–2021",unitValue(ref.value,r.unit)],
      ["Пригодных январей",ref.count],["Отклонение",unitValue(difference,r.unit)]]));
  } else {
    const air=await api(reportPath({...sel,parameter:"t_air"},"report"));
    if(state.report!==r)return;
    const line=v=>unitValue(v.summary.value,v.unit)+" · пригодных суток "+v.summary.valid+" из "+v.summary.expected;
    box.replaceChildren(el("h3","Кр1 · апрель 2024"),facts([["Температура воздуха",line(air)],["Давление",line(r)]]),
      el("p","Полнота проверяется отдельно для каждого показателя. Выберите дату в таблице, чтобы увидеть исключённые измерения."));
  }
  box.hidden=false;
}
async function refreshCatalog() {
  const result=await api("/catalog");
  state.catalog=result.datasets || []; state.external=result.external || [];
  const types=[...new Set([...state.catalog,...state.external].map(d=>d.data_type || d.kind || "station"))];
  options($("catalog-type"),[{id:"",title:"Все типы"},...types.map(id=>({id,title:display(id)}))],{},$("catalog-type").value);
  for (const prefix of ["","rule-"]) {
    const select=$(prefix+"dataset"); options(select,state.catalog.filter(d=>d.connected!==false),{},select.value); stationParameters(prefix);
  }
  renderCatalog();
}
function dataCard(d) {
  const card=el("article",null,"dataset"), bar=el("div",null,"actions");
  card.append(el("h3",d.title || d.name || d.id));
  card.append(facts([["Поставщик",d.provider || d.owner],["Территория",d.territory],["Станции",d.stations],
    ["Период",d.period || d.range || [d.start,d.end].filter(Boolean).join(" – ")],["Показатели",d.parameters],["Разрешение",d.resolution],
    ["Тип",d.data_type || d.kind],["Формат",d.format],["Полнота",d.completeness || d.coverage],["Обновление",d.updated_at],
    ["Версия",d.version],["Условия",d.license || d.terms],["Доступ",d.public?"Опубликован":"По разрешению"]]));
  if(d.connected!==false)bar.append(button("Открыть отчёт",async()=>{ $("dataset").value=d.id; stationParameters(""); openTab("report"); await loadReport(); },"primary"));
  if (!isResearcher() && !d.connected && d.public) bar.append(button("Подключить",async()=>{await api("/datasets/"+esc(d.id)+"/connect",{method:"POST"});await refreshCatalog();notify("Набор подключён.");},"primary"));
  if (isResearcher() && !d.public && d.can_publish!==false) bar.append(button("Опубликовать",async()=>{
    if(!window.confirm("Открыть этот набор для подключения другим участникам платформы?")) return;
    await api("/datasets/"+esc(d.id)+"/publish",{method:"POST"});await refreshCatalog();notify("Набор опубликован.");
  }));
  card.append(bar); return card;
}
function externalCard(d) {
  const card=el("article",null,"dataset");
  card.append(el("h3",d.title || d.name || d.provider),facts(Object.entries(d).filter(([k,v])=>
    !["id","title","name","url","link","homepage","source_url","instructions","access_url"].includes(k) && v!=null)));
  if(d.instructions)card.append(el("p",display(d.instructions),"hint"));
  const link=safeLink("Условия и получение данных",d.url || d.link || d.homepage || d.source_url || d.access_url);
  if(link) card.append(link);
  return card;
}
function renderCatalog() {
  const q=$("search").value.trim().toLocaleLowerCase("ru");
  const type=$("catalog-type").value;
  const matches=d=>(!type || type===(d.data_type || d.kind || "station")) && Object.values(d).map(display).join(" ").toLocaleLowerCase("ru").includes(q);
  const cards=state.catalog.filter(matches).map(dataCard);
  $("datasets").replaceChildren(...(cards.length?cards:[el("p","Доступных наборов по этому запросу пока нет.","empty")]));
  const ext=state.external.filter(matches).map(externalCard);
  $("external").replaceChildren(...(ext.length?ext:[el("p","Внешние источники по этому запросу не найдены.","empty")]));
  if(isResearcher() && !q && (!type || type==="reanalysis")) $("external").append(openMeteoForm());
}
$("search").addEventListener("input",renderCatalog);
$("catalog-type").addEventListener("change",renderCatalog);
for(const prefix of ["","rule-"]) $(prefix+"dataset").addEventListener("change",()=>stationParameters(prefix));
function openMeteoForm() {
  const details=el("details",null,"dataset"); details.append(el("summary","Получить ряд Open-Meteo"));
  const form=el("form"), grid=el("div",null,"form-grid");
  for(const [name,title,type,value,min,max] of [
    ["latitude","Широта","number","","-90","90"],["longitude","Долгота","number","","-180","180"],
    ["start","Дата начала","date","2024-04-01"],["end","Дата окончания","date","2024-04-02"]
  ]) { const label=el("label",title),input=el("input"); input.name=name;input.type=type;input.required=true;input.value=value;
    if(type==="number"){input.step="any";input.min=min;input.max=max;}label.append(input);grid.append(label);}
  const mode=el("select");mode.name="mode";options(mode,[{id:"research",title:"Исследовательский"},{id:"commercial",title:"Коммерческий"}]);
  const lbl=el("label","Режим доступа");lbl.append(mode);grid.append(lbl);
  const submit=el("button","Получить реанализ");submit.type="submit";form.append(grid,submit);details.append(form);
  form.addEventListener("submit",e=>{e.preventDefault();void busy(submit,async()=>{
    const f=new FormData(form); await api("/external/open-meteo",{method:"POST",body:{latitude:Number(f.get("latitude")),longitude:Number(f.get("longitude")),start:f.get("start"),end:f.get("end"),commercial:f.get("mode")==="commercial"}});
    await refreshCatalog();notify("Внешний источник подключён.");
  });});return details;
}

function gridTable(columns,records,formatter=(record,key)=>display(record[key])) {
  const box=el("div",null,"table-scroll"), table=el("table"), head=el("thead"), body=el("tbody"), tr=el("tr");
  for(const col of columns) {const th=el("th",col.title);th.scope="col";tr.append(th);}
  head.append(tr);
  for(const row of records) {
    const entry=el("tr");
    for(const col of columns) {
      const td=el("td"), content=formatter(row,col.key);
      if(content instanceof Node) td.append(content); else td.textContent=String(content);
      entry.append(td);
    } body.append(entry);
  }
  table.append(head,body);box.append(table);return box;
}
function renderMapping(file,index) {
  const details=el("details",null,"disclosure"), summary=el("summary",file.name), panel=el("div");
  const draw=()=>{
    if(!details.open || panel.childNodes.length)return;
    const mappings=el("div",null,"form-grid"), stationLabel=el("label","Станция"), select=el("select");
    options(select,[{id:"",title:"Выберите станцию"},...Object.entries(stations).map(([id,title])=>({id,title}))],{},file.station || "");
    select.onchange=()=>{file.station=select.value;};stationLabel.append(select);mappings.append(stationLabel);
    for(const [key,title] of Object.entries(fields)) {
      const lbl=el("label",title+(file.units?.[key]?" · "+file.units[key]:"")), input=el("select");
      options(input,[{id:"",title:"Пропустить"},...file.columns],{},file.mapping[key] || "");
      input.onchange=()=>{if(input.value) file.mapping[key]=input.value; else delete file.mapping[key];};
      lbl.append(input);mappings.append(lbl);
    }
    panel.append(facts([["Кодировка",file.encoding],["Разделитель",file.delimiter==="\t"?"Табуляция":file.delimiter],["Лист",file.sheet]]));
    if(file.sample?.length) panel.append(gridTable(file.columns.map(key=>({key,title:key})),file.sample));
    panel.append(mappings);
  };
  const include=el("input");include.type="checkbox";include.checked=file.included;include.addEventListener("change",()=>{file.included=include.checked;});
  const includeBox=el("div",null,"input-row"),includeLabel=el("label");includeLabel.append(include,document.createTextNode("Импортировать лист или файл"));includeBox.append(includeLabel);
  details.append(summary,includeBox,panel);details.ontoggle=draw;details.open=index===0 || !file.station || !file.mapping.datetime;draw();return details;
}
function previewTables() {
  const p=state.preview,mapping=$("file-mappings");mapping.replaceChildren();
  if(p.templates && Object.keys(p.templates).length) {
    const label=el("label","Применить сохранённый шаблон"), sel=el("select");
    options(sel,[{id:"",title:"Выберите шаблон"},...Object.keys(p.templates)]);
    sel.onchange=()=>{
      const tpl=p.templates[sel.value]; if(!tpl)return;
      let applied=0;
      for(const f of p.files) {
        const compatible=Object.values(tpl).filter(t=>Object.values(t.mapping).every(c=>f.columns.includes(c)));
        const match=tpl[f.name] || (compatible.length===1 ? compatible[0] : undefined);
        if(!match || !Object.values(match.mapping).every(c=>f.columns.includes(c))) continue;
        f.mapping={...match.mapping}; if(tpl[f.name])f.station=match.station; applied++;
      }
      previewTables();notify("Таблиц с применённым шаблоном: "+applied+".");
    };
    label.append(sel);mapping.append(label);
  }
  p.files.forEach((f,i)=>mapping.append(renderMapping(f,i)));
}
function showPreview(result) {
  state.preview=result; state.preview.files=state.preview.files.map(f=>({...f,mapping:{...f.mapping},included:true}));
  $("confirm-form").hidden=false;$("import-title").value=(result.filename || "Набор наблюдений").replace(/\.[^.]+$/,"");
  $("template").value="";$("preview-count").textContent="Таблиц в файле: "+result.files.length+". Проверьте станции и исходные колонки перед импортом.";
  previewTables();$("confirm-form").scrollIntoView({block:"start"});
}
onForm("upload-form",async()=>{
  const file=$("upload").files[0]; if(!file)return;
  if(file.size>64*1024*1024)throw Error("Файл превышает 64 МиБ.");
  $("confirm-form").hidden=true;state.preview=null;
  showPreview(await api("/imports/preview?"+new URLSearchParams({filename:file.name}),{method:"POST",raw:file}));
});
$("demo-preview").onclick=()=>busy($("demo-preview"),async()=>{showPreview(await api("/imports/demo-preview",{method:"POST"}));});
onForm("confirm-form",async()=>{
  const p=state.preview;if(!p)return;
  const files=p.files.filter(f=>f.included).map(f=>({name:f.name,station:f.station,mapping:f.mapping}));
  if(!files.length)throw Error("Выберите хотя бы одну таблицу.");
  for(const f of files) {
    if(!f.station || !f.mapping.datetime || !Object.keys(f.mapping).some(k=>k!=="datetime"&&k!=="qc_flag"))
      throw Error("Укажите станцию, время и хотя бы один показатель для "+f.name+".");
    if(new Set(Object.values(f.mapping)).size!==Object.keys(f.mapping).length)throw Error("Одна исходная колонка назначена несколько раз: "+f.name+".");
  }
  const template=$("template").value.trim();
  notify("Обрабатываем выбранные таблицы…");
  const result=await api("/imports/"+esc(p.id)+"/confirm",{method:"POST",body:{title:$("import-title").value.trim(),files,...(template?{template}:{})}});
  state.preview=null;$("confirm-form").hidden=true;await refreshCatalog();$("dataset").value=result.id;stationParameters("");
  openTab("report");notify(result.repeated?"Эта версия уже была импортирована.":"Версия сохранена.");await loadReport();
});

const reportColumns = ["period","value","valid","expected","flags"];
function preferenceKey() { return "krasnoe-columns-v1/"+(state.user?.role || ""); }
function getColumns() {
  const defaults=reportColumns.map(key=>({key,title:names[key],show:true}));
  try {
    const saved=JSON.parse(localStorage.getItem(preferenceKey()));
    if(!Array.isArray(saved) || !saved.some(c=>c.show) || saved.length!==defaults.length || new Set(saved.map(c=>c.key)).size!==defaults.length)return defaults;
    if(saved.some(c=>!reportColumns.includes(c.key) || typeof c.title!=="string"))return defaults;
    return saved;
  }catch{return defaults;}
}
let columns=[];
function writeColumns() {
  try{localStorage.setItem(preferenceKey(),JSON.stringify(columns));}catch{notify("Браузер ограничил сохранение представления.",true);}
  renderTable();
}
function settings() {
  const form=$("table-settings");form.replaceChildren();
  columns.forEach((col,i)=>{
    const row=el("div",null,"input-row"), lbl=el("label"), check=el("input");
    check.type="checkbox";check.checked=col.show;
    check.onchange=()=>{if(!check.checked&&columns.filter(c=>c.show).length===1){check.checked=true;notify("Оставьте хотя бы одну колонку.");return;}col.show=check.checked;writeColumns();};
    lbl.append(check,document.createTextNode(names[col.key]));
    const rename=el("input");rename.type="text";rename.value=col.title;rename.maxLength=50;rename.setAttribute("aria-label","Подпись колонки "+names[col.key]);
    rename.onchange=()=>{col.title=rename.value.trim() || names[col.key];rename.value=col.title;writeColumns();};
    function move(delta) {const other=i+delta; [columns[i],columns[other]]=[columns[other],columns[i]];writeColumns();settings();}
    const up=button("Выше",()=>move(-1)), down=button("Ниже",()=>move(1));up.disabled=i===0;down.disabled=i===columns.length-1;
    row.append(lbl,rename,up,down);form.append(row);
  });
}
function reportFilters() {
  const common={dataset:$("dataset").value,station:$("station").value,parameter:$("parameter").value,step:$("step").value};
  if($("period-mode").value==="range")return {...common,start:$("range-start").value,end:$("range-end").value};
  return {...common,month:$("month").value};
}
function periodMode(mode) {
  $("period-mode").value=mode;
  const range=mode==="range";$("month-field").hidden=range;$("month").disabled=range;
  for(const key of ["range-start","range-end"]){$(key+"-field").hidden=!range;$(key).disabled=!range;}
}
$("period-mode").onchange=()=>periodMode($("period-mode").value);
function reportPath(sel,resource) {
  const {dataset:id,station,parameter,month,start,end,step,version,revision}=sel;
  const qs=new URLSearchParams({station,parameter,step});
  if(start){qs.set("start",start);qs.set("end",end);}
  else qs.set("month",month);
  if(version)qs.set("version",version);
  if(revision!=null)qs.set("revision",revision);
  return "/datasets/"+esc(id)+"/"+resource+"?"+qs;
}
async function loadReport(selection) {
  const sel=selection || reportFilters();
  if(!sel.dataset || !sel.station || !sel.parameter)throw Error("Выберите набор, станцию и показатель.");
  if(sel.start){if(!sel.end || sel.start>sel.end)throw Error("Проверьте начало и окончание диапазона.");}
  else if(!/^\d{4}-\d{2}$/.test(sel.month))throw Error("Выберите месяц.");
  const ticket=++state.reportRequest;
  state.report=null;state.selection=null;$("report-result").hidden=true;$("report-empty").hidden=false;
  $("report-empty").textContent="Рассчитываем отчёт…";
  try {
    const data=await api(reportPath(sel,sel.start?"range":"report"));
    if(ticket!==state.reportRequest)return;
    state.report=data;state.selection={...sel,template:$("report-template").value,version:data.version,revision:data.revision};renderReport();
    const params=new URLSearchParams(Object.entries(state.selection).filter(([,v])=>v!=null));
    history.replaceState(null,"",location.pathname+"?"+params);
    return data;
  } catch(error) {
    if(ticket===state.reportRequest)$("report-empty").textContent="Отчёт не сформирован.";throw error;
  }
}
onForm("filters",loadReport);
async function openReportLink(params) {
  const id=params.get("dataset"), d=dataset(id);if(!d || d.connected===false)throw Error("Для открытия этой ссылки требуется доступ к версии набора.");
  $("dataset").value=id;stationParameters("");
  const mode=params.has("start")?"range":"month";periodMode(mode);
  for(const key of ["station","parameter","step"])if(params.has(key))$(key).value=params.get(key);
  if(mode==="range"){$("range-start").value=params.get("start");$("range-end").value=params.get("end")||"";}
  else if(params.has("month"))$("month").value=params.get("month");
  const sel=reportFilters();
  if(params.get("version"))sel.version=params.get("version");
  if(/^\d+$/.test(params.get("revision")||""))sel.revision=params.get("revision");
  const report=await loadReport(sel);if(!report || state.report!==report)return;
  const estimate=params.get("estimate");
  if(/^\d{4}-\d{2}-\d{2}$/.test(estimate || "") && !$("neighbor-layer").hidden)await showEstimates(estimate);
  const date=params.get("date");
  if(/^\d{4}-\d{2}-\d{2}$/.test(date || ""))await evidence(date);
}
function renderTable() {
  const rows=state.report?.rows || [];
  if(!rows.length){$("table").replaceChildren(el("p","За выбранный период нет записей.","empty"));return;}
  const cols=columns.filter(c=>c.show);
  const table=gridTable(cols,rows,(r,key)=>{
    if(key==="period" && /^\d{4}-\d{2}-\d{2}$/.test(r.period) && state.selection.step==="day") {
      const b=button(r.period,()=>evidence(r.period),"text-btn");b.title="Проверить исходные наблюдения";return b;
    }
    if(key==="value" && r.value==null){
      if(!$("neighbor-layer").hidden && r.period.startsWith("2025-")){
        const cell=el("span","Нет значения ");
        cell.append(button("Проверить оценку",()=>showEstimates(r.period),"text-btn estimate-action"));return cell;
      }
      return r.partial?"Выбран неполный период":"Нет значения";
    }
    return display(r[key]);
  });
  $("table").replaceChildren(table);
}
function renderReport(savedId) {
  const r=state.report,sel=state.selection, d=dataset(sel.dataset);
  $("question-answer").hidden=true;$("question-answer").replaceChildren();
  $("neighbor-details").hidden=true;$("neighbor-details").replaceChildren();
  $("neighbor-toggle").textContent="Показать оценки пропусков";$("neighbor-toggle").setAttribute("aria-expanded","false");
  const eligiblePeriod=sel.start?sel.start<="2025-12-31" && sel.end>="2025-01-01":sel.month?.startsWith("2025-");
  $("neighbor-layer").hidden=Boolean(savedId || !eligiblePeriod || sel.step!=="day" || sel.parameter!=="t_air"
    || !["kr1","kr2"].includes(sel.station) || !d?.archive_features?.includes("neighbors"));
  $("report-result").hidden=false;$("report-empty").hidden=true;$("evidence").hidden=true;$("quality").open=false;
  $("baseline").hidden=Boolean(savedId || sel.start);$("baseline").open=false;$("baseline-result").replaceChildren();
  $("quality").hidden=Boolean(savedId || sel.start);$("print").hidden=Boolean(sel.start);
  $("profile-data").replaceChildren();$("evidence-data").replaceChildren();$("saved-link").replaceChildren();
  $("report-title").textContent=fields[sel.parameter] || r.title || "Отчёт по наблюдениям";
  $("report-subtitle").textContent=[d?.title || r.title,display(sel.station),sel.start?sel.start+" — "+sel.end:sel.month].filter(Boolean).join(" · ");
  const s=r.summary || {};
  const chosen=["value","valid","expected","station_valid_days","status"];
  const qualityTemplate=(r.template || sel.template)==="quality" && !sel.start;
  if(qualityTemplate)$("report-title").textContent="Полнота и качество · "+(fields[sel.parameter] || sel.parameter);
  const stats=sel.start?[["Начало",sel.start],["Окончание",sel.end],["Полных дней по показателю",display(s.valid_days)+" из "+display(s.calendar_days)],["Строк в срезе",display(r.rows?.length || 0)]]:[["Среднее за месяц",s.value==null?"Нет значения":display(s.value)+" "+(r.unit||"")],
    ["Полных дней по показателю",display(s.valid)+" из "+display(s.expected)],["Полных дней по станции",display(s.station_valid_days)],["Статус",display(s.status)]];
  const visibleStats=qualityTemplate?[["Пригодных суток",display(s.valid)+" из "+display(s.expected)],["Дней подряд без полного покрытия",display(s.longest_gap)],["Исходных записей",display(s.records)],["Порог суток",display(s.required_slots)]]:stats;
  $("kpis").replaceChildren(...visibleStats.map(([label,value])=>{const block=el("div",null,"kpi");block.append(el("small",label),el("strong",value));return block;}));
  $("report-meta").replaceChildren(...facts([["Единица",r.unit],["Версия набора",r.version],["Источник",r.source],
    ["Версия потока",r.revision],["Метод",r.method || r.method_version],["Обновление",r.freshness || r.updated_at || d?.updated_at],["Ограничения",r.limitations],
    ...(sel.start?[]:Object.entries(s).filter(([k])=>!chosen.includes(k)))]).childNodes);
  const bar=$("report-actions");bar.replaceChildren();
  const snapshot=$("snapshot-events");snapshot.hidden=!Array.isArray(r.events);snapshot.replaceChildren();
  if(!snapshot.hidden) {
    snapshot.append(el("h3","События за период"));
    if(!r.events.length)snapshot.append(el("p","Событий за период сохранения нет.","hint"));
    for(const event of r.events)snapshot.append(facts([["Состояние",event.status],["Тип",event.kind],["Время измерения",event.measured],["Значение",event.value],["Режим",event.mode]]));
  }
  const graph=safeLink("Открыть Grafana",r.dashboard_url,"action-link primary");if(graph)bar.append(graph);
  const live=safeLink("Поток в Grafana",r.live_dashboard_url,"action-link");if(live && !savedId)bar.append(live);
  for(const format of ["csv","json"]) {
    const link=safeLink(format.toUpperCase(),"/portal"+reportPath(sel,sel.start?"range-export":"export")+"&format="+format,"action-link");bar.append(link);
  }
  if(savedId) savedLink(savedId);
  else if(!sel.start) bar.append(button("Сохранить отчёт",async()=>{
    const result=await api("/saved",{method:"POST",body:sel});
    if(state.report===r){state.report=result.report || r;renderReport(result.id || result.saved_id);}
    notify("Отчёт привязан к версии набора.");
  }));
  columns=getColumns();settings();renderTable();
}

async function showEstimates(focusDate) {
  const sel=state.selection, report=state.report;
  if(!report || $("neighbor-layer").hidden)return;
  const start=sel.start || sel.month+"-01";
  const end=sel.end || report.rows[report.rows.length-1].period;
  const params=new URLSearchParams({station:sel.station,parameter:sel.parameter,start,end,version:report.version,revision:report.revision});
  const data=await api("/datasets/"+esc(sel.dataset)+"/neighbor-estimates?"+params);
  if(state.report!==report)return;
  const box=$("neighbor-details"), points=focusDate?data.points.filter(p=>p.date===focusDate):data.points;
  box.replaceChildren(el("h3",focusDate?"Оценка за "+focusDate:"Суточные оценки по соседней станции"),
    el("p","Среднее и полнота отчёта рассчитаны по наблюдениям. Оценка использует температуру соседней станции за ту же дату и сезонную поправку по парным дням 2024 года."));
  if(points.length){
    const rows=points.map(p=>({...p,donor:stations[p.donor]}));
    const table=gridTable([{key:"date",title:"Дата"},{key:"estimate",title:"Оценка, °C"},{key:"valid_hours",title:"Часов из 24"},
      {key:"donor",title:"Станция-источник"},{key:"reference",title:"У соседа, °C"},{key:"offset",title:"Поправка, °C"},{key:"pairs",title:"Парных дней"}],rows,
      (r,key)=>key==="date"?button(r.date,()=>evidence(r.date),"text-btn"):display(r[key]));
    box.append(table);
    const link=new URL(report.dashboard_url,location.origin);
    link.searchParams.set("var-estimates","on");link.searchParams.set("viewPanel","panel-4");
    link.searchParams.set("from",Date.parse(start+"T00:00:00Z"));
    link.searchParams.set("to",Date.parse(end+"T23:59:59Z"));
    box.append(safeLink("Наблюдения и оценки в Grafana",link.href,"action-link"));
  } else box.append(el("p","Для выбранных дат доступных оценок нет."));
  $("neighbor-toggle").setAttribute("aria-expanded","true");$("neighbor-toggle").textContent="Скрыть оценки";
  box.hidden=false;
  if(focusDate)box.scrollIntoView({block:"center"});
}
$("neighbor-toggle").onclick=()=>busy($("neighbor-toggle"),async()=>{
  if($("neighbor-details").hidden){await showEstimates();return;}
  $("neighbor-details").hidden=true;$("neighbor-toggle").setAttribute("aria-expanded","false");
  $("neighbor-toggle").textContent="Показать оценки пропусков";
});
function savedLink(id) {
  if(!id)return;
  const url=new URL(location.pathname,location.origin);url.searchParams.set("saved",id);
  const link=safeLink("Ссылка на сохранённый отчёт",url.href);
  $("saved-link").replaceChildren(link);
}
async function openSaved(id) {
  const saved=await api("/saved/"+esc(id));
  const r=saved.report || saved, args=saved.filters || saved.selection || r.filters || saved;
  const sel={dataset:args.dataset || r.dataset || r.id,station:args.station || r.station,parameter:args.parameter || r.parameter,month:args.month || r.month,step:args.step || r.step || "day",version:r.version || saved.version, revision:r.revision ?? saved.revision};
  if(!sel.station || !sel.parameter || !sel.month)throw Error("В сохранённом отчёте отсутствуют фильтры.");
  periodMode("month");$("dataset").value=sel.dataset;stationParameters("");for(const key of ["station","parameter","month","step"])$(key).value=sel[key];
  state.selection=sel;state.report=Array.isArray(r.rows)?r:await api(reportPath(sel,"report"));renderReport(id);
}
$("print").onclick=()=>window.print();
let wasProvenanceOpen=false;
window.addEventListener("beforeprint",()=>{wasProvenanceOpen=$("provenance").open;$("provenance").open=true;});
window.addEventListener("afterprint",()=>{$("provenance").open=wasProvenanceOpen;});
onForm("baseline-form",async()=>{
  const sel=state.selection, ticket=state.reportRequest, first=Number($("first-year").value), last=Number($("last-year").value);
  if(first>last)throw Error("Первый год должен предшествовать последнему.");
  const p=new URLSearchParams({station:sel.station,parameter:sel.parameter,month:Number(sel.month.slice(5)),first,last});
  const data=await api("/datasets/"+esc(sel.dataset)+"/climatology?"+p);
  if(ticket!==state.reportRequest)return;
  $("baseline-result").replaceChildren(facts([["Среднее за выбранный период",data.value==null?"Нет значения":display(data.value)+" "+(data.unit||"")],["Пригодных лет",data.count],["Метод",data.method],["Версия",data.version]]));
  if(Object.keys(data.years||{}).length)$("baseline-result").append(gridTable([{key:"year",title:"Год"},{key:"value",title:"Среднее, "+data.unit}],Object.entries(data.years).map(([year,value])=>({year,value}))));
});
function structured(container,data) {
  container.replaceChildren();
  if(!data || typeof data!=="object"){container.append(el("p",display(data)));return;}
  const showRows=(list)=>{
    if(!list.length){container.append(el("p","Записей нет.","hint"));return;}
    if(list[0] && typeof list[0]==="object"){
      const keys=Array.from(new Set(list.flatMap(Object.keys)));
      container.append(gridTable(keys.map(k=>({key:k,title:names[k]||k})),list));
    }else container.append(el("p",display(list)));
  };
  if(Array.isArray(data)){showRows(data);return;}
  const scalar=Object.entries(data).filter(([,v])=>v==null || typeof v!=="object");container.append(facts(scalar));
  for(const [key,value] of Object.entries(data)){
    if(!value || typeof value!=="object")continue;
    const group=el("details",null,"disclosure"),body=el("div");
    group.append(el("summary",names[key]||key),body);
    group.open=["evidence","rows","readings","raw","examples"].includes(key);
    let drawn=false;const draw=()=>{if(group.open&&!drawn){drawn=true;structured(body,value);}};
    group.ontoggle=draw;draw();container.append(group);
  }
}
async function evidence(date) {
  const sel=state.selection,ticket=state.reportRequest;
  const p=new URLSearchParams({station:sel.station,parameter:sel.parameter,date});if(sel.version)p.set("version",sel.version);
  if(sel.revision!=null)p.set("revision",sel.revision);
  const data=await api("/datasets/"+esc(sel.dataset)+"/evidence?"+p);
  if(ticket!==state.reportRequest)return;
  $("evidence").hidden=false;structured($("evidence-data"),data);$("evidence").scrollIntoView({block:"start"});
  const url=new URL(location.href);if(!url.searchParams.has("saved")){url.searchParams.set("date",date);history.replaceState(null,"",url);}
}
$("quality").ontoggle=()=>{if($("quality").open && !$("profile-data").hasChildNodes()) void busy(null,async()=>{
  const ticket=state.reportRequest, data=await api(reportPath(state.selection,"profile"));
  if(ticket===state.reportRequest)structured($("profile-data"),data);
});};

async function refreshEvents() {
  const ticket=++state.eventsRequest;
  const response=await api("/events");if(ticket!==state.eventsRequest)return;
  const data=response.events || [];
  if(!data.length){$("event-list").replaceChildren(el("p","Для доступных наборов событий пока нет.","empty"));return;}
  const cards=data.map(e=>{
    const box=el("article",null,"event"),d=dataset(e.dataset || e.dataset_id);
    box.append(el("h3",e.title || e.message || display(e.type || e.kind || e.status)),
      facts([["Набор",d?.title || e.dataset || e.dataset_id],["Состояние",e.status || e.state],["Режим",e.mode],["Станция",e.station || e.rule?.station],
        ["Показатель",e.parameter || e.rule?.parameter],["Значение",e.value],["Единица",e.unit],["Время измерения",e.observed_at || e.measured_at || e.measured],
        ["Время приёма",e.received || e.received_at],["Время события",e.created_at || e.created || e.time],["Правило",e.rule || e.rule_id],["Версия",e.version],["Получатель",e.recipient]]));
    const link=safeLink("Проверить на графике",e.dashboard_url || e.graph_url,"action-link");if(link)box.append(link);
    return box;
  });
  $("event-list").replaceChildren(...cards);
}
$("refresh-events").onclick=()=>busy($("refresh-events"),refreshEvents);
onForm("rule-form",async()=>{
  const id=$("rule-dataset").value;if(!id)throw Error("Выберите набор.");
  const payload={station:$("rule-station").value,parameter:$("rule-parameter").value,threshold:Number($("threshold").value),
    duration_minutes:Number($("duration").value),min_slots:Number($("min-slots").value),stale_minutes:Number($("stale").value),recipient:$("recipient").value.trim()};
  await api("/datasets/"+esc(id)+"/rules",{method:"POST",body:payload});notify("Правило сохранено.");await refreshEvents();
});
onForm("replay-form",async()=>{
  const id=$("rule-dataset").value;if(!id)throw Error("Выберите набор.");
  const data=await api("/datasets/"+esc(id)+"/replay",{method:"POST",body:{station:$("rule-station").value,parameter:$("rule-parameter").value,date:$("replay-date").value}});
  structured($("replay-result"),data);await refreshEvents();notify("Воспроизведение завершено.");
});
void loadWorkspace().catch(error=>{
  if(!state.user || error.status===401) signedOut();
  if(error.status!==401) notify(error.message,true);
});
