import {mountStationAtlas} from "./station-atlas.js";

const byId = id => document.getElementById(id);
const labels = {t_air:"Температура воздуха",pressure_hpa:"Давление",rh:"Влажность",wind_speed:"Скорость ветра",
  wind_dir:"Направление ветра",wind_gust:"Порывы ветра",soil_05:"Почва, 5 см",soil_10:"Почва, 10 см",soil_20:"Почва, 20 см"};
const questions = {kr2:"Где можно оценить пропуски Кр2?", kr1:"Почему в апреле почти нет давления?", ame60:"Насколько холодным был январь 2010 года?"};
const number = new Intl.NumberFormat("ru-RU",{maximumFractionDigits:2});
const format = v => v == null ? "Нет значения" : number.format(v);
const dateLabel = day => new Intl.DateTimeFormat("ru-RU",{day:"numeric", month:"long",year:"numeric",timeZone:"UTC"}).format(new Date(day+"T00:00:00Z"));
function el(tag,text) {
  const n=document.createElement(tag);
  if(text!=null)n.textContent=text;
  return n;
}
function link(label,url,primary=false) {
  const a=el("a",label);a.href=url;a.className=primary?"action-link primary":"action-link";
  return a;
}
function table(headers,rows) {
  const wrapper=el("div");wrapper.className="table-scroll";
  const t=el("table"),tr=el("tr");
  headers.forEach(h=>{const th=el("th",h);th.scope="col";tr.append(th);});
  t.createTHead().append(tr);
  const body=t.createTBody();
  rows.forEach(cells=>{const r=body.insertRow();cells.forEach(value=>r.insertCell().textContent=value);});
  wrapper.append(t);return wrapper;
}
function finding(code,r) {
  if(code==="kr2")return "За 2025 год для "+r.estimates.length+" пропущенных суточных значений на поле получены оценки по соседней станции. Они показаны отдельно от измерений.";
  if(code==="kr1")return "В апреле 2024 температура прошла проверку за "+r.valid_days+" суток, давление — за "+r.pressure_days+". Для среднемесячного давления данных недостаточно.";
  return "Январь 2010 года на АМЕ-60 был на "+format(r.baseline-r.mean)+" °C холоднее среднего января за "+r.baseline_years+" пригодных лет базового периода.";
}
function showStatistics(site,r,sites,reportView){
  const section=byId("statistics");
  section.hidden=!r;
  if(!r){byId("stats-plot").replaceChildren();return;}
  const month=new Intl.DateTimeFormat("ru-RU",{month:"long",year:"numeric",timeZone:"UTC"})
    .format(new Date(r.month+"-01T00:00:00Z"));
  byId("stats-title").textContent=site.name+" · "+(r.year_temperature?r.month.slice(0,4):month);
  const stations=byId("stats-stations");
  stations.replaceChildren();
  for(const item of sites.filter(s=>s.selection)){
    const url=new URLSearchParams({demo:reportView?"report":"atlas",station:item.id});
    const a=el("a",item.name);a.href="/portal?"+url+"#statistics";
    if(item.id===site.id)a.setAttribute("aria-current","true");
    stations.append(a);
  }
  const mean=format(r.mean)+" °C";
  let metrics;
  if(r.year_temperature){
    const y=r.year_temperature;
    metrics=[["Пригодные сутки",y.valid_days+" из "+y.days],["Оценки по Кр1",String(r.estimates.length)],
      ["Мин. / макс. по наблюдениям",format(y.min)+" / "+format(y.max)+" °C"]];
  }else if(site.code==="kr1"){
    metrics=[["Средняя температура",mean],["Сутки с температурой",r.valid_days+" из "+r.days.length],
      ["Сутки с давлением",r.pressure_days+" из "+r.days.length]];
  }else{
    metrics=[["Средняя за январь",mean],["Среднее за "+r.baseline_years+" пригодных январей",format(r.baseline)+" °C"],
      ["Отклонение",format(r.mean-r.baseline)+" °C"]];
  }
  byId("stats-values").replaceChildren(...metrics.map(([label,value])=>{
    const group=el("div");group.append(el("dt",label),el("dd",value));return group;
  }));
  const controls=byId("stats-controls");
  controls.replaceChildren();
  const buttons=[];
  function selectPanel(panel){
    for(const [btn,id] of buttons)btn.setAttribute("aria-pressed",String(id===panel.id));
    const url=new URL(site.grafana_url.replace("/d/","/d-solo/"),location.origin);
    url.search=new URLSearchParams({panelId:panel.id,theme:"light",timezone:"utc"});
    const prefix="/portal/static/panel-previews/"+site.code+"-"+panel.id+"-";
    const picture=el("picture"), source=el("source"), img=el("img");
    source.media="(max-width: 640px)";source.srcset=prefix+"narrow.png";
    img.src=prefix+"wide.png";img.alt=panel.title+" · "+byId("stats-title").textContent;
    picture.append(source,img);
    const frame=el("iframe");frame.title=img.alt;frame.tabIndex=-1;
    frame.setAttribute("aria-hidden","true");
    // The local Grafana image is available immediately, including if the live panel fails.
    frame.addEventListener("load",()=>{
      const doc=frame.contentDocument;
      if(!doc?.body || !frame.isConnected)return;
      function reveal(){
        if(!doc.querySelector(".uplot canvas"))return false;
        frame.classList.add("rendered");frame.removeAttribute("aria-hidden");frame.removeAttribute("tabindex");
        picture.hidden=true;
        return true;
      }
      if(reveal())return;
      const observer=new MutationObserver(()=>{if(reveal())observer.disconnect();});
      observer.observe(doc.body,{childList:true,subtree:true});
      doc.defaultView.addEventListener("pagehide",()=>observer.disconnect(),{once:true});
    },{once:true});
    frame.src=url.href;
    byId("stats-plot").replaceChildren(picture,frame);
  }
  if(r.chart_panels.length>1){
    for(const panel of r.chart_panels){
      const label=panel.id===1?"Температура":site.code==="kr2"?"Полнота":"Давление";
      const btn=el("button",label);btn.type="button";btn.title=panel.title;
      btn.addEventListener("click",()=>{if(btn.getAttribute("aria-pressed")!=="true")selectPanel(panel);});
      controls.append(btn);buttons.push([btn,panel.id]);
    }
  }
  selectPanel(r.chart_panels[0]);
}
function showReport(site,r) {
  const box=byId("report");box.hidden=false;
  byId("title").textContent=site.name;
  byId("finding").textContent=finding(site.code,r);
  byId("links").append(link("К атласу","/portal?demo=atlas&station="+site.code),link("Открыть Grafana",site.grafana_url,true));
  box.append(el("h2",questions[site.code]));
  if(site.code==="kr2"){
    box.append(el("p","Для каждой даты берём среднюю температуру на Кр1 и добавляем сезонную поправку по парным дням 2024 года. В основной статистике эти даты остаются пропусками."),
      table(["Дата","Пригодных часов Кр2","Кр1, °C","Поправка, °C","Оценка Кр2, °C"],r.estimates.map(p=>[dateLabel(p.date),p.valid_hours+" из 24",format(p.reference),format(p.offset),format(p.estimate)])));
  } else if(site.code==="kr1"){
    box.append(el("p","Средняя температура воздуха за апрель составила "+format(r.mean)+" °C. Полноту проверяем отдельно для каждого показателя: измерение температуры в тот же срок не восполняет пропуск давления."));
  } else {
    box.append(el("p","Среднее января 2010 года составило "+format(r.mean)+" °C. Среднее пригодных январей за 1992–2021 годы — "+format(r.baseline)+" °C. Каждый пригодный январь имеет одинаковый вес."));
  }
  box.append(el("h3","Суточные наблюдения · "+r.month));
  const pressure=site.code==="kr1";
  box.append(table(["Дата","Температура, °C","Пригодных сроков",...(pressure?["Давление, гПа","Пригодных сроков давления"]:[])],
    r.days.map(d=>[dateLabel(d.date),format(d.air),d.slots,...(pressure?[format(d.pressure),d.pressure_slots]:[])])));
}

async function main(){
  const response=await fetch("/portal/static/organizer-archive.json",{cache:"no-cache"});
  if(!response.ok)throw Error("Архив не загрузился. Обновите страницу.");
  const {sites,results}=await response.json();
  const params=new URLSearchParams(location.search);
  const focusStatistics=location.hash==="#statistics";
  const initial=sites.find(s=>s.id===(params.get("station") || params.get("site"))) || sites.find(s=>s.code==="kr2");
  if(params.get("demo")==="report" && results[initial.code]){
    showReport(initial,results[initial.code]);
    showStatistics(initial,results[initial.code],sites,true);
    if(focusStatistics)byId("statistics").scrollIntoView();
    return;
  }
  mountStationAtlas(byId("atlas"),sites,{initial:initial.id,parameterLabel:key=>labels[key] || key,
    questionButton:site=>link(questions[site.code],site.report_url,true),
    select:id=>{
      const site=sites.find(s=>s.id===id),r=results[site.code];
      showStatistics(site,r,sites,false);
      byId("finding").textContent=r?finding(site.code,r):site.description;
      byId("links").replaceChildren();
      if(r)byId("links").append(link(questions[site.code],site.report_url,true),link("График в Grafana",site.grafana_url));
      history.replaceState(null,"", "/portal?"+new URLSearchParams({demo:"atlas",station:id}));
    }});
  if(focusStatistics && !byId("statistics").hidden)byId("statistics").scrollIntoView();
}
main().catch(error=>byId("error").textContent=error.message);
