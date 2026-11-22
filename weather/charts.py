import json
import os
import re
from urllib.parse import quote

import httpx
from fastapi import HTTPException, Request, Response

from . import store

ROLES=("research","business")


def endpoint(role):
    return os.getenv("GRAFANA_"+role.upper()+"_URL","").rstrip("/")


def dashboard(ds,title):
    variables = [{"type":"textbox","name":k,"label":label,"query":value,"current":{"text":value,"value":value}}
                 for k,label,value in (("dataset","Набор",ds),("version","Версия",""),("month","Месяц","2024-04"),("revision","Срез потока",""),("start","С даты",""),("end","По дату",""))]
    variables[0]["hide"]=2
    variables[1]["hide"]=2
    variables[3]["hide"]=2
    for name,label,values in (("station","Станция","ame60,kr1,kr2,era5"),("parameter","Показатель","t_air,pressure_hpa,rh,wind_speed,wind_gust,soil_05,soil_10,soil_20,wind_dir"),("step","Шаг","day,decade,month")):
        variables.append({"type":"custom","name":name,"label":label,"query":values,"current":{"text":values.split(",")[0],"value":values.split(",")[0]}})
    dsref={"type":"yesoreyeram-infinity-datasource","uid":"weather-rows"}
    panels=[]
    for i,(title_,columns) in enumerate([("Средние значения",["value"]),("Полнота наблюдений",["valid","expected"])]):
        cols=[{"selector":"period","text":"Период","type":"string"}]+[{"selector":c,"text":{"value":"Среднее","valid":"Пригодно","expected":"Ожидается"}[c],"type":"number"} for c in columns]
        query="/portal/chart-data/${dataset}?version=${version}&station=${station}&parameter=${parameter}&month=${month}&step=${step}&revision=${revision}&start=${start}&end=${end}"
        panels.append({"id":i+1,"title":title_,"type":"barchart","gridPos":{"x":0,"y":i*8,"w":24,"h":8},"datasource":dsref,
                       "targets":[{"refId":"A","datasource":dsref,"type":"json","source":"url","format":"table","parser":"backend","url":query,"url_options":{"method":"GET"},"columns":cols}],
                       "fieldConfig":{"defaults":{"color":{"mode":"palette-classic"}},"overrides":[]},"options":{"xField":"Период","showValue":"auto"}})
    drill="/portal?dataset=${dataset:percentencode}&station=${station:percentencode}&parameter=${parameter:percentencode}&month=${month:percentencode}&step=${step:percentencode}&version=${version:percentencode}&revision=${revision:percentencode}&date=${__data.fields[Период]}"
    for panel in panels:
        panel["fieldConfig"]["defaults"]["links"]=[{"title":"Проверить наблюдения","url":drill}]
    panels.append({"id":3,"title":"События потока","type":"table","gridPos":{"x":0,"y":16,"w":24,"h":6},"datasource":dsref,
                   "targets":[{"refId":"A","datasource":dsref,"type":"json","parser":"backend","source":"url","format":"table",
                               "url":"/portal/chart-data/${dataset}/events?version=${version}&station=${station}&parameter=${parameter}&month=${month}&revision=${revision}&start=${start}&end=${end}",
                               "url_options":{"method":"GET"},"columns":[{"selector":s,"text":t,"type":"string"} for s,t in (("measured","Измерение"),("received","Получено"),("kind","Событие"),("status","Состояние"),("mode","Источник"))]}]})
    return {"uid":"w-"+ds,"title":title+" · "+ds[:6],"schemaVersion":40,"version":1,"editable":False,"panels":panels,
            "refresh":"30s","time":{"from":"now-1d","to":"now"},"timepicker":{"hidden":True},"templating":{"list":variables}}


def synchronize():
    with store.db().connect() as con:
        rows=store.many(con,"SELECT d.* FROM datasets d JOIN chart_sync s ON s.dataset=d.id WHERE s.due=1")
    for d in rows:
        complete=True
        for role in ROLES:
            base=endpoint(role)
            if not base:
                complete=False
                continue
            try:
                with httpx.Client(base_url=base,auth=("admin",os.getenv("GRAFANA_"+role.upper()+"_ADMIN","")),timeout=7) as client:
                    prefix="/charts/"+role+"/api/dashboards/"
                    if role=="business" and not d["public"]:
                        response=client.delete(prefix+"uid/w-"+d["id"])
                        if response.status_code==404:
                            continue
                    else:
                        response=client.post(prefix+"db",json={"dashboard":dashboard(d["id"],d["title"]),"overwrite":True})
                    response.raise_for_status()
            except httpx.HTTPError:
                complete=False
        if complete:
            with store.db().begin() as con:
                store.execute(con,"UPDATE chart_sync SET due=0 WHERE dataset=:d AND EXISTS (SELECT 1 FROM datasets WHERE id=:d AND version=:v AND public=:p)",d=d["id"],v=d["version"],p=d["public"])


async def proxy(request: Request, role, path):
    from .auth import actor
    actual=actor(request)
    if actual!=role or role not in ROLES:
        raise HTTPException(403,"Кабинет Grafana недоступен.")
    match=re.search(r"(?:^|/)w-([0-9a-f]{20})(?:/|$)",path)
    if match:
        from .reports import visible
        with store.db().connect() as con:
            visible(con,role,match.group(1),request.query_params.get("var-version"))
    base=endpoint(role)
    if not base:
        raise HTTPException(503,"Сервер Grafana этого кабинета ещё не настроен.")
    # Each Grafana process is a separate workspace, with anonymous Viewer and a role-scoped data-source credential.
    # Never forward browser cookies or Authorization to Grafana. It authenticates the viewer locally.
    allowed_headers={k:v for k,v in request.headers.items() if k.lower() in {"accept","content-type","if-none-match","accept-language","user-agent"}}
    query=request.url.query
    target=base+"/charts/"+role+"/"+quote(path,safe="/-._~") + ("?"+query if query else "")
    upstream=httpx.Request(request.method,target,headers=allowed_headers,content=await request.body())
    resp=await request.app.state.grafana_http.send(upstream)
    headers={k:v for k,v in resp.headers.items() if k.lower() in {"content-type","cache-control","etag","location"}}
    return Response(resp.content,status_code=resp.status_code,headers=headers)
