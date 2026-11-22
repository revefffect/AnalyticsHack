"""Loopback-only probe for PostgreSQL, Grafana Infinity and a separate webhook receiver."""
from datetime import datetime, timezone
import csv
import io
import json
from pathlib import Path
import sqlite3
from time import monotonic, sleep
from uuid import uuid4
from zipfile import ZipFile
import httpx

root=Path(__file__).resolve().parents[1]
cfg=dict(x.split("=",1) for x in (root/".env").read_text(encoding="utf-8-sig").splitlines() if x and not x.startswith("#") and "=" in x)
headers={"Authorization":"Bearer "+cfg["RESEARCH_TOKEN"]}
sensor={"Authorization":"Bearer "+cfg["STREAM_TOKEN"]}
run=uuid4().hex[:10]
source="meteo_clgz_hackathon/data/sokol_m1/kr1_2024.csv"
with ZipFile(root/"data/raw/meteo_hack_dataset.zip") as archive, archive.open(source) as f:
    source_rows=csv.DictReader(io.TextIOWrapper(f,encoding="utf-8-sig",newline=""))
    april=[(n,r) for n,r in enumerate(source_rows,2) if r["datetime"].startswith("2024-04") and
           r["t_air"] and "t_air:" not in r["qc_flag"] and "dup_conflict" not in r["qc_flag"]]
transitions=[]
last=False
for number,row in april:
    above=float(row["t_air"])>=9
    if above!=last:
        transitions.append((number,row,above))
        last=above
    if len(transitions)==3:
        break

with httpx.Client(base_url="http://127.0.0.1:8123",timeout=10,trust_env=False) as client:
    filename=f"kr1_latency_{run}.csv"
    initial=april[0][1]
    raw=f"datetime,t_air,qc_flag\n{initial['datetime']},{initial['t_air']},\n"
    p=client.post("/portal/imports/preview",params={"filename":filename},headers=headers,content=raw.encode())
    p.raise_for_status()
    draft=p.json()
    choice=draft["files"][0]
    p=client.post("/portal/imports/"+draft["id"]+"/confirm",headers=headers,json={
        "title":"Лабораторный замер · вход, панель, webhook "+run,
        "files":[{"name":choice["name"],"mapping":choice["mapping"],"station":"kr1"}]})
    p.raise_for_status()
    dataset,version=p.json()["id"],p.json()["version"]
    p=client.post(f"/portal/datasets/{dataset}/rules",headers=headers,json={"station":"kr1","parameter":"t_air",
                  "threshold":9,"duration_minutes":0,"min_slots":1,"stale_minutes":43200,"recipient":"release-check-local"})
    p.raise_for_status()
    rule_id=p.json()["id"]
    deadline=monotonic()+60
    while monotonic()<deadline:
        p=client.get("/charts/research/api/dashboards/uid/w-"+dataset,headers=headers)
        if p.is_success:
            break
        sleep(.4)
    p.raise_for_status()
    dashboard=p.json()["dashboard"]
    panel=next(p for p in dashboard["panels"] if p["title"]=="События потока")
    q=dict(panel["targets"][0],intervalMs=60000,maxDataPoints=500)
    variables=dict(dataset=dataset,version=version,revision="",month="2024-04",station="kr1",
                   parameter="t_air",start="",end="")
    for key,value in variables.items():
        q["url"]=q["url"].replace("${"+key+"}",value)
    def query():
        res=client.post("/charts/research/api/ds/query",headers=headers,json={"queries":[q],"from":"1711929600000","to":"1714521600000"})
        res.raise_for_status()
        return res.json()["results"]["A"]["frames"]
    query()
    receipt_path=root/".runtime/service/receipts.sqlite"
    samples=[]
    with sqlite3.connect(receipt_path.resolve().as_uri()+"?mode=ro",uri=True,timeout=.5) as receiver:
        for line,row,above in transitions:
            msg={"id":f"bench-{run}-{line}","station":"kr1","timestamp":row["datetime"],"values":{"t_air":float(row["t_air"])},
                 "flags":row["qc_flag"].split(";") if row["qc_flag"] else []}
            sent=monotonic()
            response=client.post("/portal/stream/"+dataset,headers=sensor,json=msg)
            response.raise_for_status()
            ack=monotonic()
            ack_utc=datetime.now(timezone.utc)
            body=response.json()
            assert not body["duplicate"]
            status="active" if above else "recovered"
            event_id=frame_time=receipt=None
            polls=0
            while monotonic()<ack+30:
                if frame_time is None:
                    polls+=1
                    frames=query()
                    finished=monotonic()
                    for frame in frames:
                        cols={f["name"]:v for f,v in zip(frame["schema"]["fields"],frame["data"]["values"])}
                        matches=[i for i,v in enumerate(cols.get("Получено",[])) if v==body["received"] and cols["Состояние"][i]==status]
                        if not matches:
                            continue
                        events=frame["schema"]["meta"]["custom"]["data"]
                        event=next(e for e in events if e["received"]==body["received"] and e["kind"]=="threshold" and e["status"]==status)
                        assert event["rule_id"]==rule_id and event["dataset"]==dataset and event["version"]==version
                        event_id,frame_time=event["id"],finished
                if event_id is not None and receipt is None:
                    hit=receiver.execute("select received,payload from receipt where id=?",(event_id,)).fetchone()
                    if hit:
                        payload=json.loads(hit[1])
                        assert payload["id"]==event_id and payload["received"]==body["received"] and payload["status"]==status
                        receipt=datetime.fromisoformat(hit[0])
                if frame_time is not None and receipt is not None:
                    break
                sleep(.1)
            if frame_time is None or receipt is None:
                raise RuntimeError(f"Missing observation for revision {body['revision']}, frame={frame_time is not None}, receipt={receipt is not None}")
            sample={"line":line,"measurement":msg["timestamp"],"temperature_c":msg["values"]["t_air"],"status":status,
                    "revision":body["revision"],"event_id":event_id,"accepted_utc":body["received"],"ack_utc":ack_utc.isoformat(),
                    "webhook_received_utc":receipt.isoformat(),"frame_polls":polls,"post_ms":round(1000*(ack-sent),1),
                    "ack_to_frame_ms":round(1000*(frame_time-ack),1),"ack_to_webhook_ms":round(1000*(receipt-ack_utc).total_seconds(),1),
                    "service_receive_to_webhook_ms":round(1000*(receipt-datetime.fromisoformat(body["received"])).total_seconds(),1)}
            print(json.dumps(sample),flush=True)
            samples.append(sample)
        result={"dataset":dataset,"version":version,"rule_id":rule_id,"source":source,"poll_sleep_s":.1,"worker_interval_s":3,
                "grafana_refresh":dashboard["refresh"],"runs":samples}
        path=root/".runtime"/("alert-latency-"+run+".json")
        path.write_text(json.dumps(result,indent=2),encoding="utf8")
        print(path.name)
