import calendar
import gzip
import json
from collections import Counter, defaultdict
from datetime import datetime
from functools import lru_cache
from statistics import mean
from urllib.parse import urlencode

from tools.audit_meteo import valid_month
from . import store
from .imports import UNITS


def visible(con, actor, ds, version=None, *, catalogue=False):
    dataset = store.one(con,"SELECT * FROM datasets WHERE id=:id",id=ds)
    if not dataset:
        raise LookupError("Набор недоступен.")
    allowed = dataset["owner"]==actor
    if not allowed and dataset["public"]:
        grant = store.one(con,"SELECT c.version FROM connections c JOIN versions v ON c.version=v.id WHERE c.dataset=:d AND c.actor=:a AND (:anyver=1 OR c.version=:v) ORDER BY v.created DESC LIMIT 1",d=ds,a=actor,anyver=int(version is None),v=version or "")
        allowed = bool(grant) or catalogue
        if grant and not catalogue:
            if version and version!=grant["version"]:
                raise LookupError("Версия недоступна.")
            version = grant["version"]
    if not allowed:
        raise LookupError("Набор недоступен.")
    row = store.one(con,"SELECT * FROM versions WHERE id=:v AND dataset=:d",v=version or dataset["version"],d=ds)
    if not row:
        raise LookupError("Версия недоступна.")
    return dataset,row["id"],json.loads(row["manifest"]),row["created"]


@lru_cache(maxsize=18)
def partition(blob):
    return [json.loads(line) for line in gzip.decompress(store.get(blob)).splitlines()]


def records(con, ds, version, manifest, station, month, revision):
    if station not in manifest["station_parameters"]:
        raise ValueError("Станция отсутствует в версии набора.")
    data = []
    for part in manifest["partitions"].get(station+"/"+month,[]):
        for stamp,line,values,flags in partition(part["blob"]):
            data.append((stamp,part["file"],line,values,flags))
    # Replay is a copy of a real archive row. It exercises delivery and alerts without counting it twice.
    live = store.many(con,"SELECT * FROM messages WHERE dataset=:d AND version=:v AND station=:s AND stamp LIKE :m AND revision<=:r AND mode='live' ORDER BY revision",
                      d=ds,v=version,s=station,m=month+"%",r=revision)
    for row in live:
        p=json.loads(row["payload"])
        data.append((row["stamp"],"https-stream",row["id"],p["values"],p["flags"]))
    data.sort(key=lambda x:x[0])
    by_stamp = defaultdict(set)
    for stamp,_,_,vals,_ in data:
        by_stamp[stamp].add(tuple(sorted(vals.items())))
    return [(s,f,l,v,sorted(set(flags)|{"dup_conflict"}) if len(by_stamp[s])>1 else flags) for s,f,l,v,flags in data]


def graph_url(actor, ds, version, station, param, month, step, revision):
    args = urlencode({"var-dataset":ds,"var-version":version,"var-station":station,"var-parameter":param,
                      "var-month":month,"var-step":step,"var-revision":revision})
    return f"/charts/{actor}/d/w-{ds}/weather?{args}"


def average(values, parameter):
    if not values:
        return None
    if parameter=="wind_dir":
        # Wind direction is shown as sectors in evidence; a scalar arithmetic mean is misleading.
        return None
    return mean(values)


def report(con, actor, ds, station, parameter, month, step="day", version=None, revision=None):
    dataset,version,manifest,created=visible(con,actor,ds,version)
    if parameter not in UNITS or step not in {"day","decade","month"}:
        raise ValueError("Неизвестный показатель или шаг.")
    base=datetime.strptime(month,"%Y-%m")
    if base.strftime("%Y-%m")!=month:
        raise ValueError("Месяц в формате ГГГГ-ММ.")
    revision=dataset["revision"] if revision is None else min(revision,dataset["revision"])
    data=records(con,ds,version,manifest,station,month,revision)
    days=calendar.monthrange(base.year,base.month)[1]
    threshold=72 if station=="ame60" else 18
    expected=96 if station=="ame60" else 24
    per_day=defaultdict(list)
    for row in data:
        per_day[row[0][:10]].append(row)
    daily=[]
    for number in range(1,days+1):
        date=f"{month}-{number:02}"
        slots,station_slots=set(),set()
        vals=[]
        qc=Counter()
        for stamp,_,_,values,flags in per_day[date]:
            qc.update(flags)
            if "dup_conflict" in flags:
                continue
            slot=stamp if station=="ame60" else stamp[:13]
            station_slots.add(slot)
            if values.get(parameter) is not None and not any(f.startswith(parameter+":") for f in flags):
                slots.add(slot)
                vals.append(values[parameter])
        full=len(slots)>=threshold
        daily.append({"period":date,"value":average(vals,parameter) if full else None,"valid":len(slots),"expected":expected,
                      "threshold":threshold,"station_slots":len(station_slots),"full":full,"flags":dict(qc)})
    good={i+1 for i,row in enumerate(daily) if row["full"]}
    accepted,gap=valid_month(good,base.year,base.month)
    def group(rows,period,allowed):
        vals=[row["value"] for row in rows if row["value"] is not None]
        flags=Counter()
        for row in rows:
            flags.update(row["flags"])
        return {"period":period,"value":average(vals,parameter) if allowed else None,"valid":sum(r["full"] for r in rows),
                "expected":len(rows),"flags":dict(flags)}
    monthly=group(daily,month,accepted)
    if step=="day":
        output=daily
    elif step=="decade":
        output=[group(daily[start:end],f"{month}-{start+1:02}…{end:02}",True) for start,end in ((0,10),(10,20),(20,days))]
    else:
        output=[monthly]
    present=parameter in manifest["station_parameters"][station] or any(parameter in r[3] for r in data)
    status=("unavailable_parameter" if not present else "no_observations" if not data else "direction_sectors" if parameter=="wind_dir"
            else "ok" if accepted else "insufficient_coverage")
    return {"id":ds,"version":version,"revision":revision,"title":dataset["title"],"station":station,"parameter":parameter,"month":month,
            "step":step,"unit":UNITS[parameter],"rows":output,"summary":{**monthly,"status":status,"longest_gap":gap,
            "station_valid_days":sum(d["station_slots"]>=threshold for d in daily),"records":len(data),"required_slots":threshold},
            "source":{"file":manifest["input"],"sha256":manifest["sha256"],"created":created,**manifest["source"]},"method":manifest["method"],
            "limitations":"Средние рассчитаны по пригодным наблюдениям. Время станции хранится в исходной зоне, если она известна.",
            "live_dashboard_url":graph_url(actor,ds,version,station,parameter,month,step,""),
            "dashboard_url":graph_url(actor,ds,version,station,parameter,month,step,revision)}


def evidence(con,actor,ds,station,parameter,date,version=None,revision=None):
    d,v,m,_=visible(con,actor,ds,version)
    if parameter not in UNITS:
        raise ValueError("Неизвестный показатель.")
    datetime.strptime(date,"%Y-%m-%d")
    rows=[]
    for stamp,f,line,values,flags in records(con,ds,v,m,station,date[:7],d["revision"] if revision is None else revision):
        if stamp[:10]!=date:
            continue
        value=values.get(parameter)
        reason=("conflict" if "dup_conflict" in flags else "absent_column" if parameter not in values else
                "flagged" if any(tag.startswith(parameter+":") for tag in flags) else "empty" if value is None else "observed")
        rows.append({"timestamp":stamp,"file":f,"line":line,"value":value,"flags":flags,"status":reason})
    return {"date":date,"rows":rows,"version":v,"timezone":m["source"].get("timezone","unknown")}


def wind_sector(values, flags):
    angle = values.get("wind_dir")
    if angle is None or "dup_conflict" in flags or any(f.startswith("wind_dir:") for f in flags):
        return None
    return int((angle + 22.5) % 360 // 45) * 45


def profile(con,actor,ds,station,parameter,month,version=None):
    output=report(con,actor,ds,station,parameter,month,version=version)
    dataset,v,m,_=visible(con,actor,ds,version)
    data=records(con,ds,v,m,station,month,dataset["revision"])
    intervals=Counter()
    signals=[]
    missing_runs=[]
    for day in output["rows"]:
        if day["full"]:
            continue
        if missing_runs and int(day["period"][-2:])==int(missing_runs[-1]["end"][-2:])+1:
            missing_runs[-1]["end"]=day["period"]
            missing_runs[-1]["days"]+=1
        else:
            missing_runs.append({"start":day["period"],"end":day["period"],"days":1})
    sectors=Counter()
    prev=None
    previous_interval=None
    changes=[]
    quality=Counter()
    for stamp,f,line,values,flags in data:
        time=datetime.fromisoformat(stamp)
        val=values.get(parameter)
        item={"timestamp":stamp,"file":f,"line":line,"value":val}
        reason=("conflict" if "dup_conflict" in flags else "absent_column" if parameter not in values else
                "flagged" if any(f.startswith(parameter+":") for f in flags) else "empty" if val is None else "measured_zero" if val==0 else "measured")
        quality[reason]+=1
        for flag in flags:
            if flag=="dup_conflict" or flag.startswith(parameter+":"):
                signals.append({**item,"rule":flag})
        usable = val is not None and "dup_conflict" not in flags and not any(flag.startswith(parameter+":") for flag in flags)
        if parameter=="wind_dir":
            sector=wind_sector(values,flags)
            if sector is not None:
                sectors[sector]+=1
        if prev:
            delta=(time-prev[0]).total_seconds()/60
            if delta>0:
                intervals[delta]+=1
                if previous_interval is not None and previous_interval!=delta:
                    changes.append({**item,"rule":"cadence_change","previous_minutes":previous_interval,"minutes":delta})
                previous_interval=delta
            if parameter=="t_air" and usable and prev[1] is not None and 0<delta<=60 and abs(val-prev[1])>=8:
                signals.append({**item,"rule":"review:temperature_jump_8C_per_hour"})
        prev=(time,val if usable else None)
    seasonal=[]
    for number in range(1,13):
        key=f"{month[:4]}-{number:02}"
        month_view=output if key==month else report(con,actor,ds,station,parameter,key,"month",v,output["revision"])
        s=month_view["summary"]
        seasonal.append({"period":key,"value":s["value"],"valid_days":s["valid"],"calendar_days":s["expected"],"status":s["status"]})
    return {"summary":output["summary"],"interval_minutes":dict(intervals),"issues":signals,"sectors":dict(sectors),"seasonal_profile":seasonal,
            "counts":dict(quality),"missing_runs":missing_runs,"cadence_changes":changes,
            "expected_slots_per_day":96 if station=="ame60" else 24,"daily":output["rows"],"version":v}
