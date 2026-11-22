"""Exercise the local platform over HTTP; the supplied organizer ZIP remains the source."""
import argparse
import json
import os
import time
from pathlib import Path

import httpx


def run(base,env):
    keys={}
    for line in Path(env).read_text(encoding="utf-8-sig").splitlines():
        if "=" in line:
            k,v=line.split("=",1)
            keys[k]=v
    def call(client,method,path,**kw):
        response=client.request(method,path,**kw)
        if not response.is_success:
            raise RuntimeError(f"{method} {path}: {response.status_code} {response.text[:200]}")
        return response.json()
    with httpx.Client(base_url=base,timeout=300) as owner,httpx.Client(base_url=base,timeout=30) as business:
        for client,t in ((owner,"RESEARCH_TOKEN"),(business,"BUSINESS_TOKEN")):
            call(client,"POST","/portal/session",json={"token":keys[t]})
        started=time.perf_counter()
        preview=call(owner,"POST","/portal/imports/demo-preview")
        files=[{k:f[k] for k in ("name","mapping","station")} for f in preview["files"]]
        done=call(owner,"POST",f"/portal/imports/{preview['id']}/confirm",json={"title":"Урочище Красное · архив станций","files":files,"template":"Архив урочища"})
        ds=done["id"]
        path="/portal/datasets/"+ds
        baseline=time.perf_counter()-started
        call(owner,"POST",path+"/publish")
        call(business,"POST",path+"/connect")
        params={"station":"kr1","parameter":"pressure_hpa","month":"2024-04","step":"day"}
        rep=call(business,"GET",path+"/report",params=params)
        assert rep["summary"]["value"] is None and rep["summary"]["valid"]==2 and rep["summary"]["station_valid_days"]==28
        jan=call(owner,"GET",path+"/report",params={**params,"station":"ame60","month":"2010-01","parameter":"t_air"})
        assert round(jan["summary"]["value"],2)==-15.20
        data=call(owner,"GET",path+"/export",params={**params,"format":"json"})
        assert data["rows"]==rep["rows"]
        cfg={"station":"kr1","parameter":"t_air","threshold":5,"duration_minutes":60,"min_slots":2,"stale_minutes":120,"recipient":"Дежурный"}
        rule=call(owner,"POST",path+"/rules",json=cfg)
        replay=call(owner,"POST",path+"/replay",json={"station":"kr1","parameter":"t_air","date":"2024-04-01"})
        saved=call(business,"POST","/portal/saved",json={"dataset":ds,**params})
        snap=call(business,"GET","/portal/saved/"+saved["id"])
        assert snap["rows"]==rep["rows"]
        return {"dataset":ds,"version":done["version"],"import_seconds":round(baseline,2),"repeat":done.get("repeated"),"files":len(files),
                "records":done.get("count"),"january":round(jan["summary"]["value"],2),"april_valid":rep["summary"]["valid"],
                "dashboard_url":rep["dashboard_url"],"saved":saved["id"],"rule":rule["id"],"replay_received":replay["received"],
                "replay_transitions":[[e["kind"],e["status"],e["measured"]] for e in replay["events"]]}


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--base",default="http://127.0.0.1:8123")
    p.add_argument("--env",default=".env")
    args=p.parse_args()
    print(json.dumps(run(args.base,args.env),ensure_ascii=True,indent=2))
