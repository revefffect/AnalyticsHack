"""Idempotent HTTPS intake and persistent alert transitions."""
import json
import math
import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx

from . import store
from .imports import UNITS, normalized
from .reports import graph_url, visible


def add_rule(con,actor,ds,config):
    _,_,manifest,_=visible(con,actor,ds)
    if config["parameter"] not in manifest["station_parameters"].get(config["station"],[]):
        raise ValueError("Показатель отсутствует у выбранной станции.")
    key=store.digest(store.packed([ds,config]).encode())[:28]
    store.execute(con,"INSERT INTO rules VALUES (:k,:d,:s,:p,:c,:n) ON CONFLICT DO NOTHING",k=key,d=ds,s=config["station"],p=config["parameter"],c=store.packed(config),n=store.utcnow())
    return {"id":key,**config,"status":"insufficient_data"}


def emit(con,ds,v,r,kind,state,stamp,received,mode,value):
    # Threshold and staleness have independent state machines.
    history=store.many(con,"SELECT detail FROM events WHERE rule=:r AND version=:v ORDER BY created DESC,id DESC",r=r["id"],v=v)
    latest=next((json.loads(h["detail"]) for h in history if json.loads(h["detail"])["kind"]==kind and json.loads(h["detail"])["mode"]==mode),None)
    if (latest and latest["status"]==state) or (latest is None and state=="recovered"):
        return None
    key=store.digest(store.packed([r["id"],v,kind,mode,state,stamp,latest["id"] if latest else ""]).encode())[:32]
    cfg=json.loads(r["config"])
    revision=store.one(con,"SELECT revision FROM datasets WHERE id=:d",d=ds)["revision"]
    url=graph_url("business",ds,v,cfg["station"],cfg["parameter"],stamp[:7],"day",revision)
    detail={"id":key,"kind":kind,"status":state,"measured":stamp,"received":received,"mode":mode,"value":value,"rule":cfg,
            "rule_id":r["id"],"dataset":ds,"version":v,"revision":revision,"recipient":cfg["recipient"],"dashboard_url":url}
    store.execute(con,"INSERT INTO events VALUES (:k,:d,:v,:r,:t,:j) ON CONFLICT DO NOTHING",k=key,d=ds,v=v,r=r["id"],t=store.utcnow(),j=store.packed(detail))
    store.execute(con,"INSERT INTO outbox(id,retry_at) VALUES(:k,:t) ON CONFLICT DO NOTHING",k=key,t=store.utcnow())
    return detail


def evaluate(con,ds,version,r,mode,received):
    cfg=json.loads(r["config"])
    entries=store.many(con,"SELECT id,stamp,payload FROM messages WHERE dataset=:d AND version=:v AND station=:s AND mode=:mode ORDER BY stamp DESC,revision DESC",d=ds,v=version,s=r["station"],mode=mode)
    if not entries:
        return
    p=r["parameter"]
    latest=entries[0]["stamp"]
    latest_val=None
    slots=set()
    newest=datetime.fromisoformat(latest)
    previous=newest
    for i,entry in enumerate(entries):
        j=json.loads(entry["payload"])
        stamp=entry["stamp"]
        when=datetime.fromisoformat(stamp)
        val=j["values"].get(p)
        if i==0:
            latest_val=val
        if val is None or "dup_conflict" in j["flags"] or any(f.startswith(p+":") for f in j["flags"]):
            return
        if val<cfg["threshold"]:
            if i==0:
                emit(con,ds,version,r,"threshold","recovered",latest,received,mode,val)
            return
        if previous-when>timedelta(minutes=cfg["stale_minutes"]):
            return
        slots.add(stamp if r["station"]=="ame60" else stamp[:13])
        if (newest-when).total_seconds()>=60*cfg["duration_minutes"] and len(slots)>=cfg["min_slots"]:
            emit(con,ds,version,r,"threshold","active",latest,received,mode,latest_val)
            return
        previous=when


def ingest(con,actor,ds,message,mode="live"):
    store.execute(con,"UPDATE datasets SET revision=revision WHERE id=:d",d=ds)
    dataset,version,manifest,_=visible(con,actor,ds)
    station=message["station"]
    vals=message["values"]
    if station not in manifest["station_parameters"] or not vals or not set(vals)<=set(manifest["station_parameters"][station]):
        raise ValueError("Станция или показатели не соответствуют версии.")
    fields={**vals,"datetime":message["timestamp"],"qc_flag":";".join(message.get("flags",[]))}
    stamp,values,flags=normalized(fields,{k:k for k in fields})
    payload=store.packed({"values":values,"flags":flags,"origin":message.get("origin")})
    key=message["id"]
    old=store.one(con,"SELECT * FROM messages WHERE dataset=:d AND id=:k",d=ds,k=key)
    if old:
        if (old["payload"],old["station"],old["stamp"],old["mode"],old["version"])!=(payload,station,stamp,mode,version):
            raise ValueError("Идентификатор уже связан с другим сообщением.")
        return {"duplicate":True,"revision":old["revision"],"received":old["received"]}
    received=store.utcnow()
    rev=dataset["revision"]+1
    store.execute(con,"UPDATE datasets SET revision=:r WHERE id=:d",d=ds,r=rev)
    store.execute(con,"INSERT INTO messages VALUES (:d,:k,:v,:r,:s,:t,:n,:m,:p)",d=ds,k=key,v=version,r=rev,s=station,t=stamp,n=received,m=mode,p=payload)
    rules=store.many(con,"SELECT * FROM rules WHERE dataset=:d AND station=:s",d=ds,s=station)
    for rule in rules:
        evaluate(con,ds,version,rule,mode,received)
        emit(con,ds,version,rule,"stale","recovered",stamp,received,mode,None)
    return {"duplicate":False,"revision":rev,"received":received}


def stale_scan(con,now=None):
    now=now or datetime.now(timezone.utc)
    for r in store.many(con,"SELECT * FROM rules"):
        ds=r["dataset"]
        store.execute(con,"UPDATE datasets SET revision=revision WHERE id=:d",d=ds)
        row=store.one(con,"SELECT version FROM datasets WHERE id=:d",d=ds)
        version=row["version"]
        for mode in ("live","replay"):
            last=store.one(con,"SELECT received,stamp FROM messages WHERE dataset=:d AND version=:v AND station=:s AND mode=:m ORDER BY revision DESC LIMIT 1",d=ds,v=version,s=r["station"],m=mode)
            if not last and mode=="replay":
                continue
            received=last["received"] if last else r["created"]
            if now-datetime.fromisoformat(received)>=timedelta(minutes=json.loads(r["config"])["stale_minutes"]):
                emit(con,ds,version,r,"stale","active",last["stamp"] if last else received[:19],now.isoformat(),mode,None)


def deliver():
    url=os.getenv("ALERT_WEBHOOK_URL")
    if not url:
        return 0
    now=datetime.now(timezone.utc)
    with store.db().begin() as con:
        row=store.one(con,"SELECT o.id,e.detail FROM outbox o JOIN events e ON e.id=o.id WHERE delivered IS NULL AND retry_at<=:now ORDER BY retry_at LIMIT 1",now=now.isoformat())
        if not row:
            return 0
        lease=uuid.uuid4().hex
        result=store.execute(con,"UPDATE outbox SET lease=:lease,retry_at=:next,attempts=attempts+1 WHERE id=:id AND delivered IS NULL AND retry_at<=:now",
                             lease=lease,next=(now+timedelta(seconds=30)).isoformat(),id=row["id"],now=now.isoformat())
        if not result.rowcount:
            return 0
    try:
        httpx.post(url,json=json.loads(row["detail"]),headers={"Idempotency-Key":row["id"]},timeout=4).raise_for_status()
    except httpx.HTTPError:
        return 0
    with store.db().begin() as con:
        store.execute(con,"UPDATE outbox SET delivered=:t WHERE id=:id AND lease=:lease",t=store.utcnow(),id=row["id"],lease=lease)
    return 1
