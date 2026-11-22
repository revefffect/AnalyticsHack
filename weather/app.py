import calendar
import csv
import io
import json
import os
import time
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Literal
from zipfile import BadZipFile
import httpx

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import atlas, auth, charts, external, imports, neighbors, ranges, reports, store, stream

STATIC=Path(__file__).parent/"static"
ARCHIVE=store.ROOT/"data/raw/meteo_hack_dataset.zip"


@asynccontextmanager
async def lifespan(app):
    store.migrate()
    async with httpx.AsyncClient(timeout=30,trust_env=False) as client:
        app.state.grafana_http=client
        yield


app=FastAPI(title="Платформа метеонаблюдений",version="1.0",lifespan=lifespan)
app.mount("/portal/static",StaticFiles(directory=STATIC),name="static")


@app.exception_handler(LookupError)
async def absent(request,exc):
    return JSONResponse({"detail":"Ресурс недоступен."},status_code=404)


@app.exception_handler(ValueError)
@app.exception_handler(BadZipFile)
async def invalid(request,exc):
    return JSONResponse({"detail":str(exc)},status_code=422)


@app.get("/",include_in_schema=False)
@app.get("/portal",include_in_schema=False)
def page(request:Request):
    query=request.query_params
    private_link=any(k in query for k in ("login","dataset","saved"))
    public_page="demo" in query
    if not public_page and not private_link:
        try:
            auth.actor(request)
        except HTTPException:
            public_page=True
    template = "guest.html" if public_page else "index.html"
    if query.get("demo") == "explore":
        template = "explorer.html"
    return FileResponse(STATIC/template,
                        headers={"Cache-Control":"private, no-store","Vary":"Cookie, Authorization"})


@app.get("/health")
def health():
    with store.db().connect() as con:
        store.one(con,"SELECT version FROM schema_version")
    return {"status":"ok"}


class Login(BaseModel):
    token:str=Field(max_length=300)


@app.post("/portal/session")
def session(body:Login):
    if body.token.strip().upper() == "METEO":
        res=JSONResponse({"role":"organizer","location":"/portal?demo=explore"})
        res.delete_cookie("weather_session",path="/")
        return res
    for role in ("research","business"):
        key=auth.secret(role.upper()+"_TOKEN")
        if auth.equal(key,body.token):
            expiry=str(int(time.time())+8*3600)
            res=JSONResponse({"role":role})
            res.set_cookie("weather_session",f"{role}.{expiry}.{auth.sign(role,expiry,key)}",httponly=True,samesite="strict",
                           secure=os.getenv("COOKIE_SECURE")=="1",max_age=8*3600,path="/")
            return res
    raise HTTPException(401,"Ключ входа не принят.")


@app.delete("/portal/session")
def logout():
    res=JSONResponse({"ok":True})
    res.delete_cookie("weather_session",path="/")
    return res


@app.get("/portal/me")
def me(request:Request):
    role=auth.actor(request)
    return {"role":role,"name":"Исследователь" if role=="research" else "Сотрудник компании"}


@app.post("/portal/imports/preview")
async def preview(request:Request,filename:str=Query(min_length=1,max_length=240)):
    owner=auth.writer(request)
    buf=bytearray()
    async for chunk in request.stream():
        buf.extend(chunk)
        if len(buf)>imports.LIMIT:
            raise HTTPException(413,"Файл больше 64 МиБ.")
    return await run_in_threadpool(imports.prepare,owner,filename,bytes(buf))


@app.post("/portal/imports/demo-preview")
def demo(request:Request):
    return imports.prepare(auth.writer(request),ARCHIVE.name,ARCHIVE.read_bytes())


class Choice(BaseModel):
    name:str
    station:str
    mapping:dict[str,str]


class Confirmation(BaseModel):
    title:str=Field(min_length=1,max_length=150)
    files:list[Choice]=Field(min_length=1,max_length=500)
    template:str|None=Field(default=None,max_length=80)
    dataset:str|None=None
    combine_stations:list[str]=Field(default_factory=list,max_length=10)


@app.post("/portal/imports/{key}/confirm")
def confirm(request:Request,key:str,body:Confirmation):
    return imports.confirm(auth.writer(request),key,body.title,[f.model_dump() for f in body.files],body.template,body.dataset,body.combine_stations)


@app.get("/portal/catalog")
def catalog(request:Request,q:str="",station:str="",parameter:str=""):
    actor=auth.actor(request)
    items=[]
    station_cards=[]
    with store.db().connect() as con:
        for d in store.many(con,"SELECT * FROM datasets WHERE owner=:a OR public=1 ORDER BY title",a=actor):
            try:
                _,v,m,created=reports.visible(con,actor,d["id"])
                connected=True
            except LookupError:
                _,v,m,created=reports.visible(con,actor,d["id"],catalogue=True)
                connected=False
            stations=list(m["station_parameters"])
            params=sorted({p for group in m["station_parameters"].values() for p in group})
            periods=sorted(k.split("/")[1] for k in m["partitions"])
            features=neighbors.features(m)
            card={**d,"version":v,"update_available":connected and v!=d["version"],"public":bool(d["public"]),"connected":connected,"stations":stations,"parameters":params,
                  "station_labels":{s:atlas.station_label(m,s) for s in stations},"archive_features":features,
                  "period":[periods[0],periods[-1]],"provider":m["source"].get("provider","Владелец набора"),"updated_at":created,
                  "data_type":m["source"].get("data_type","station"),"format":m["input"].rsplit(".",1)[-1],"count":sum(m["counts"].values()),
                  "territory":m["source"].get("territory","Указана в исходнике" if m["source"].get("coordinates") else "Не указана"),
                  "resolution":"Сроки источника","completeness":"По станции и показателю в отчёте","terms":m["source"].get("license","По разрешению владельца")}
            if q.casefold() in card["title"].casefold() and (not station or station in stations) and (not parameter or parameter in params):
                items.append(card)
                if connected:
                    station_cards.extend(atlas.stations(actor,d,v,m,features))
    return {"datasets":items,"external":external.SOURCE_CARDS,"station_atlas":station_cards+atlas.references()}


@app.post("/portal/datasets/{ds}/publish")
def publish(request:Request,ds:str,public:bool=True):
    role=auth.writer(request)
    with store.db().begin() as con:
        reports.visible(con,role,ds)
        store.execute(con,"UPDATE datasets SET public=:p WHERE id=:d AND owner=:o",p=int(public),d=ds,o=role)
        store.execute(con,"UPDATE chart_sync SET due=1 WHERE dataset=:d",d=ds)
    return {"id":ds,"public":public}


@app.post("/portal/datasets/{ds}/connect")
def connect(request:Request,ds:str):
    role=auth.actor(request)
    with store.db().begin() as con:
        d,v,_,_=reports.visible(con,role,ds,catalogue=True)
        store.execute(con,"INSERT INTO connections VALUES (:d,:a,:v) ON CONFLICT DO NOTHING",d=ds,a=role,v=v)
    return {"id":ds,"version":v,"connected":True}


@app.get("/portal/datasets/{ds}/report")
def report(request:Request,ds:str,station:str,parameter:str,month:str,step:str="day",version:str|None=None,revision:int|None=Query(default=None,ge=0)):
    with store.db().connect() as con:
        return reports.report(con,auth.actor(request),ds,station,parameter,month,step,version,revision)


@app.get("/portal/datasets/{ds}/evidence")
def evidence(request:Request,ds:str,station:str,parameter:str,date:str,version:str|None=None,revision:int|None=None):
    with store.db().connect() as con:
        return reports.evidence(con,auth.actor(request),ds,station,parameter,date,version,revision)


@app.get("/portal/datasets/{ds}/neighbor-estimates")
def neighbor_estimates(request:Request,ds:str,station:str,parameter:str,start:date,end:date,
                       version:str|None=None, revision:int|None=Query(default=None,ge=0)):
    with store.db().connect() as con:
        return neighbors.layer(con,auth.actor(request),ds,station,parameter,start,end,version,revision)


@app.get("/portal/datasets/{ds}/profile")
def profile(request:Request,ds:str,station:str,parameter:str,month:str,version:str|None=None):
    with store.db().connect() as con:
        return reports.profile(con,auth.actor(request),ds,station,parameter,month,version)


@app.get("/portal/datasets/{ds}/climatology")
def climate(request:Request,ds:str,station:str,parameter:str,month:int=Query(ge=1,le=12),first:int=1992,last:int=2021,
            version:str|None=None,revision:int|None=Query(default=None,ge=0)):
    if not 1<=first<=last<=9999 or last-first>150:
        raise ValueError("Выберите базовый период до 151 года.")
    role=auth.actor(request)
    with store.db().connect() as con:
        dsrow,v,_,_=reports.visible(con,role,ds,version)
        cap=dsrow["revision"] if revision is None else revision
        rows={}
        for year in range(first,last+1):
            rep=reports.report(con,role,ds,station,parameter,f"{year:04}-{month:02}","month",v,cap)
            if rep["summary"]["value"] is not None:
                rows[year]=rep["summary"]["value"]
    return {"years":rows,"count":len(rows),"value":reports.average(list(rows.values()),parameter),"unit":imports.UNITS[parameter],"version":v,"method":"equal-valid-years-v1"}


@app.get("/portal/datasets/{ds}/export")
def export(request:Request,ds:str,station:str,parameter:str,month:str,step:str="day",format:Literal["csv","json"]="csv",version:str|None=None,revision:int|None=None):
    rep=report(request,ds,station,parameter,month,step,version,revision)
    return as_download(rep,format,month)


def as_download(rep,format,period):
    station,parameter=rep["station"],rep["parameter"]
    headers={"Content-Disposition":f'attachment; filename="{station}-{parameter}-{period}.{format}"'}
    if format=="json":
        return JSONResponse(rep,headers=headers)
    out=io.StringIO(newline="")
    names=["period","value","valid","expected","flags","unit","version","revision","source_sha256","method","station","parameter"]
    writer=csv.DictWriter(out,fieldnames=names)
    writer.writeheader()
    for r in rep["rows"]:
        writer.writerow({**{k:r.get(k) for k in names[:4]},"flags":store.packed(r["flags"]),"unit":rep["unit"],"version":rep["version"],
                          "revision":rep["revision"],"source_sha256":rep["source"]["sha256"],"method":rep["method"],"station":station,"parameter":parameter})
    return Response(out.getvalue().encode("utf-8-sig"),media_type="text/csv",headers=headers)


@app.get("/portal/datasets/{ds}/range")
def date_range(request:Request,ds:str,station:str,parameter:str,start:date,end:date,step:str="day",version:str|None=None,revision:int|None=None):
    with store.db().connect() as con:
        return ranges.view(con,auth.actor(request),ds,station,parameter,start.isoformat(),end.isoformat(),step,version,revision)


@app.get("/portal/datasets/{ds}/range-export")
def range_export(request:Request,ds:str,station:str,parameter:str,start:date,end:date,step:str="day",format:Literal["csv","json"]="csv",version:str|None=None,revision:int|None=None):
    return as_download(date_range(request,ds,station,parameter,start,end,step,version,revision),format,f"{start}_{end}")


class Selection(BaseModel):
    dataset:str
    station:str
    parameter:str
    month:str
    step:str="day"
    version:str|None=None
    revision:int|None=Field(default=None,ge=0)
    template:Literal["weather","quality"]="weather"


@app.post("/portal/saved")
def save_report(request:Request,body:Selection):
    role=auth.actor(request)
    with store.db().begin() as con:
        rep=reports.report(con,role,body.dataset,body.station,body.parameter,body.month,body.step,body.version,body.revision)
        rep["template"]=body.template
        rep["events"]=[json.loads(e["detail"]) for e in store.many(con,"SELECT detail FROM events WHERE dataset=:d AND version=:v ORDER BY created",d=body.dataset,v=rep["version"])
                       if json.loads(e["detail"]).get("revision",0)<=rep["revision"] and json.loads(e["detail"])["measured"].startswith(body.month) and json.loads(e["detail"])["rule"]["station"]==body.station and json.loads(e["detail"])["rule"]["parameter"]==body.parameter]
        rep["freshness"]=store.one(con,"SELECT max(received) AS latest FROM messages WHERE dataset=:d AND version=:v AND revision<=:r",d=body.dataset,v=rep["version"],r=rep["revision"])["latest"] or rep["source"]["created"]
        key=store.digest(store.packed([role,rep]).encode())[:24]
        store.execute(con,"INSERT INTO saved VALUES (:k,:a,:d,:data) ON CONFLICT DO NOTHING",k=key,a=role,d=body.dataset,data=store.packed(rep))
    return {"id":key,"report":rep}


@app.get("/portal/saved/{key}")
def saved(request:Request,key:str):
    actor=auth.actor(request)
    with store.db().connect() as con:
        row=store.one(con,"SELECT * FROM saved WHERE id=:k AND actor=:a",k=key,a=actor)
        if not row:
            raise LookupError()
        rep=json.loads(row["snapshot"])
        reports.visible(con,actor,row["dataset"],rep["version"])
    return rep


class Rule(BaseModel):
    station:str
    parameter:str
    threshold:float=Field(allow_inf_nan=False)
    duration_minutes:int=Field(ge=0,le=14400)
    min_slots:int=Field(ge=1,le=1000)
    stale_minutes:int=Field(ge=1,le=43200)
    recipient:str=Field(min_length=1,max_length=120)


@app.post("/portal/datasets/{ds}/rules")
def add_rule(request:Request,ds:str,body:Rule):
    with store.db().begin() as con:
        return stream.add_rule(con,auth.writer(request),ds,body.model_dump())


class Message(BaseModel):
    id:str=Field(min_length=1,max_length=160)
    station:str
    timestamp:str
    values:dict[str,float|None]
    flags:list[str]=Field(default_factory=list)


@app.post("/portal/stream/{ds}")
def receive(request:Request,ds:str,body:Message):
    if not auth.equal(request.headers.get("authorization","").removeprefix("Bearer "),auth.secret("STREAM_TOKEN")):
        raise HTTPException(401,"Требуется ключ потока.")
    with store.db().begin() as con:
        return stream.ingest(con,"research",ds,body.model_dump())


class Replay(BaseModel):
    station:str
    parameter:str
    date:date


@app.post("/portal/datasets/{ds}/replay")
def replay(request:Request,ds:str,body:Replay):
    role=auth.writer(request)
    stamp=body.date.isoformat()
    with store.db().begin() as con:
        d,v,m,_=reports.visible(con,role,ds)
        rows=reports.records(con,ds,v,m,body.station,stamp[:7],0)
        sent=0
        repeats=0
        for ts,file,line,vals,flags in rows:
            if ts[:10]!=stamp:
                continue
            msg={"id":"replay-"+store.digest(store.packed([v,file,line]).encode())[:30],"station":body.station,"values":vals,
                 "timestamp":ts,"flags":flags,"origin":{"file":file,"line":line}}
            res=stream.ingest(con,role,ds,msg,mode="replay")
            repeats+=int(res["duplicate"])
            sent+=1
        events=store.many(con,"SELECT detail FROM events WHERE dataset=:d AND version=:v ORDER BY created",d=ds,v=v)
    return {"mode":"replay","received":sent,"duplicates":repeats,"events":[json.loads(e["detail"]) for e in events]}


@app.get("/portal/events")
def events(request:Request):
    role=auth.actor(request)
    with store.db().connect() as con:
        sql="SELECT e.detail,o.delivered FROM events e JOIN outbox o ON e.id=o.id JOIN datasets d ON d.id=e.dataset WHERE d.owner=:a OR (d.public=1 AND EXISTS(SELECT 1 FROM connections c WHERE c.actor=:a AND c.dataset=d.id AND c.version=e.version)) ORDER BY e.created DESC"
        rows=store.many(con,sql,a=role)
    items=[]
    for r in rows:
        event=json.loads(r["detail"])
        event["dashboard_url"]=event["dashboard_url"].replace("/charts/business/",f"/charts/{role}/",1)
        items.append({**event,"delivered":r["delivered"]})
    return {"events":items}


class ExternalRequest(BaseModel):
    latitude:float
    longitude:float
    start:str
    end:str
    commercial:bool=False


@app.post("/portal/external/open-meteo")
def reanalysis(request:Request,body:ExternalRequest):
    role=auth.writer(request)
    try:
        response=external.fetch_open_meteo(**body.model_dump())
    except external.ExternalSourceError:
        raise HTTPException(502,"Провайдер не выдал ряд. Проверьте доступ и повторите запрос.") from None
    meta={**response["metadata"],"data_type":"reanalysis","timezone":"UTC"}
    raw=response["source_response"]
    meta["provider_blob"]=store.put(raw.encode() if isinstance(raw,str) else raw)
    draft=imports.prepare(role,"era5.json",store.packed(response["observations"]).encode(),meta)
    choice={"name":"era5.json","station":"era5","mapping":{"datetime":"datetime","t_air":"t_air","qc_flag":"qc_flag"}}
    dataset=store.digest(store.packed([role,"era5",body.latitude,body.longitude,body.start,body.end,body.commercial]).encode())[:20]
    return imports.confirm(role,draft["id"],"ERA5 · Open-Meteo",[choice],dataset=dataset)


@app.get("/portal/chart-data/{ds}")
def chart_data(request:Request,ds:str,station:str,parameter:str,month:str,step:str="day",version:str="",revision:str="",start:str="",end:str=""):
    role=chart_role(request)
    with store.db().connect() as con:
        if start or end:
            return ranges.view(con,role,ds,station,parameter,start,end,step,version or None,int(revision) if revision else None)["rows"]
        return reports.report(con,role,ds,station,parameter,month,step,version or None,int(revision) if revision else None)["rows"]


def chart_role(request):
    key=request.headers.get("x-weather-key","")
    for role in charts.ROLES:
        if auth.equal(key,auth.secret("GRAFANA_"+role.upper()+"_KEY")):
            return role
    raise HTTPException(401,"Требуется ключ источника Grafana.")


@app.get("/portal/chart-data/{ds}/neighbor-estimates")
def chart_estimates(request:Request,ds:str,station:str,parameter:str,month:str,step:str="day",
                    enabled:str="off",version:str="",revision:str="",start:str="",end:str=""):
    role=chart_role(request)
    with store.db().connect() as con:
        if enabled != "on" or step != "day":
            reports.visible(con,role,ds,version or None)
            return []
        if start or end:
            first, last = date.fromisoformat(start), date.fromisoformat(end)
        else:
            first = date.fromisoformat(month + "-01")
            last = first.replace(day=calendar.monthrange(first.year,first.month)[1])
        data = neighbors.layer(con,role,ds,station,parameter,first,last,version or None,
                               int(revision) if revision else None,include_observed=True)
        estimates = {p["date"]:p["estimate"] for p in data["points"]}
        return [{"time":day+"T00:00:00Z","observed":observed,"estimate":estimates.get(day)}
                for day,observed in data["observed"].items()]


@app.get("/portal/chart-data/{ds}/events")
def chart_events(request:Request,ds:str,station:str,parameter:str,month:str,version:str="",revision:str="",start:str="",end:str=""):
    role=chart_role(request)
    with store.db().connect() as con:
        d,v,_,_=reports.visible(con,role,ds,version or None)
        cap=int(revision) if revision else d["revision"]
        rows=store.many(con,"SELECT detail FROM events WHERE dataset=:d AND version=:v ORDER BY created",d=ds,v=v)
    events=[json.loads(e["detail"]) for e in rows]
    return [e for e in events if e.get("revision",0)<=cap and (start<=e["measured"][:10]<=end if start and end else e["measured"].startswith(month)) and e["rule"]["station"]==station and e["rule"]["parameter"]==parameter]


@app.api_route("/charts/{role}/{path:path}",methods=["GET","POST","HEAD"],include_in_schema=False)
async def grafana(request:Request,role:str,path:str):
    return await charts.proxy(request,role,path)
