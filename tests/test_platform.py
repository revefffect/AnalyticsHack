import io
import json
import os
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from fastapi.testclient import TestClient
from openpyxl import Workbook

from weather import store,stream
from weather.app import app

ROOT=Path(__file__).resolve().parents[1]
HEAD="datetime,t_air,pressure_hpa,qc_flag\n"


class Platform(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{"WEATHER_HOME":self.temp.name,"DATABASE_URL":"","WEATHER_BUCKET":"","RESEARCH_TOKEN":"test-research",
                                        "BUSINESS_TOKEN":"test-business","STREAM_TOKEN":"test-station","GRAFANA_BUSINESS_KEY":"test-chart",
                                        "GRAFANA_RESEARCH_KEY":"test-owner-chart"})
        self.env.start()
        self.client=TestClient(app)
        self.client.__enter__()
        self.owner={"Authorization":"Bearer test-research"}
        self.business={"Authorization":"Bearer test-business"}

    def tearDown(self):
        self.client.__exit__(None,None,None)
        store.db().dispose()
        self.env.stop()
        self.temp.cleanup()

    def preview(self,name,data):
        r=self.client.post("/portal/imports/preview",params={"filename":name},content=data,headers=self.owner)
        self.assertEqual(r.status_code,200,r.text)
        return r.json()

    def confirm(self,p,files=None,dataset=None):
        choices=files or [{k:t[k] for k in ("name","mapping","station")} for t in p["files"]]
        r=self.client.post("/portal/imports/"+p["id"]+"/confirm",json={"files":choices,"title":"Тест","template":"поля","dataset":dataset},headers=self.owner)
        self.assertEqual(r.status_code,200,r.text)
        return r.json()

    def fixture(self):
        raw=HEAD+"".join(f"2024-04-01 {i:02}:00,{0 if i==0 else 3},{990 if i else 900},\n" for i in range(24))
        return self.confirm(self.preview("kr1_test.csv",raw.encode()))

    def query(self,ds,role=None,**kwargs):
        return self.client.get("/portal/datasets/"+ds+"/report",params={"station":"kr1","parameter":"t_air","month":"2024-04",**kwargs},headers=role or self.owner)

    def allow(self,d):
        self.client.post("/portal/datasets/"+d+"/publish",headers=self.owner).raise_for_status()
        self.client.post("/portal/datasets/"+d+"/connect",headers=self.business).raise_for_status()

    def test_equivalent_formats_mapping_duplicate_and_reference(self):
        flat=[{"datetime":f"2024-04-01 {h:02}:00","t_air":0 if h==0 else 3,"pressure_hpa":900 if h==0 else 990,"qc_flag":""} for h in range(24)]
        book=Workbook()
        sheet=book.active;sheet.title="kr1_test"
        sheet.append(list(flat[0]))
        for row in flat:
            sheet.append(list(row.values()))
        out=io.BytesIO();book.save(out)
        versions=[self.fixture(),self.confirm(self.preview("kr1_test.json",json.dumps(flat).encode())),self.confirm(self.preview("kr1_test.xlsx",out.getvalue()))]
        reports=[self.query(v["id"]).json() for v in versions]
        self.assertEqual(reports[0]["rows"],reports[1]["rows"])
        self.assertEqual(reports[0]["rows"],reports[2]["rows"])
        self.assertEqual(reports[0]["rows"][0]["valid"],24)
        self.assertEqual(self.fixture()["version"],versions[0]["version"])
        evidence=self.client.get("/portal/datasets/"+versions[0]["id"]+"/evidence",params={"station":"kr1","parameter":"pressure_hpa","date":"2024-04-01"},headers=self.owner).json()
        self.assertEqual((evidence["rows"][0]["line"],evidence["rows"][0]["status"]),(2,"flagged"))
        ambiguous=self.preview("input.csv","datetime,t_air,температура\n2024-01-01,1,2\n".encode())
        self.assertNotIn("t_air",ambiguous["files"][0]["mapping"])
        self.assertIsNone(ambiguous["files"][0]["station"])

    def test_private_grants_saved_versions_export_and_chart(self):
        v=self.fixture();ds=v["id"];p={"station":"kr1","parameter":"t_air","month":"2024-04"}
        for route in ("report","export","profile"):
            self.assertEqual(self.client.get(f"/portal/datasets/{ds}/{route}",params=p,headers=self.business).status_code,404)
        self.assertEqual(self.client.get("/portal/chart-data/"+ds,params=p,headers={"x-weather-key":"test-chart"}).status_code,404)
        self.assertEqual(self.client.get("/charts/research/d/w-"+ds+"/weather",headers=self.business).status_code,403)
        self.assertEqual(self.client.get("/charts/business/d/w-"+ds+"/weather",headers=self.business).status_code,404)
        self.allow(ds)
        saved=self.client.post("/portal/saved",json={"dataset":ds,**p},headers=self.business).json()
        rep=saved["report"]
        self.assertEqual(self.client.get("/portal/chart-data/"+ds,params=p,headers={"x-weather-key":"test-chart"}).json(),rep["rows"])
        self.assertEqual(self.client.get(f"/portal/datasets/{ds}/export",params={**p,"format":"json"},headers=self.business).json()["rows"],rep["rows"])
        version2=self.confirm(self.preview("kr1_changed.csv",(HEAD+"2024-04-03 00:00,9,999,\n").encode()),dataset=ds)
        self.assertNotEqual(v["version"],version2["version"])
        self.assertEqual(self.query(ds,self.business).json()["version"],v["version"])
        self.allow(ds)
        self.assertEqual(self.query(ds,self.business).json()["version"],version2["version"])
        self.assertEqual(self.client.get("/portal/saved/"+saved["id"],headers=self.business).json(),rep)
        self.client.post(f"/portal/datasets/{ds}/publish?public=false",headers=self.owner)
        self.assertEqual(self.client.get("/portal/saved/"+saved["id"],headers=self.business).status_code,404)

    def test_archive_oracles_and_partial_range(self):
        raw=(ROOT/"data/raw/meteo_hack_dataset.zip").read_bytes()
        p=self.preview("archive.zip",raw)
        parts=[{k:f[k] for k in ("name","mapping","station")} for f in p["files"] if f["name"].endswith(("kr1_2024.csv","ame60_2010.csv"))]
        ds=self.confirm(p,parts)["id"]
        rep=self.query(ds,parameter="pressure_hpa").json()
        self.assertEqual((rep["summary"]["valid"],rep["summary"]["value"],rep["summary"]["longest_gap"]),(2,None,28))
        self.assertEqual(round(self.query(ds,station="ame60",month="2010-01").json()["summary"]["value"],2),-15.20)
        p={"station":"kr1","parameter":"t_air","start":"2024-03-31","end":"2024-04-02","step":"month"}
        rr=self.client.get("/portal/datasets/"+ds+"/range",params=p,headers=self.owner).json()
        self.assertEqual(len(rr["rows"]),2)
        self.assertTrue(all(row["partial"] and row["value"] is None for row in rr["rows"]))

    def test_neighbor_layer_is_separate_versioned_and_permission_scoped(self):
        raw=(ROOT/"data/raw/meteo_hack_dataset.zip").read_bytes()
        draft=self.preview("archive.zip",raw)
        names=tuple(f"{s}_{y}.csv" for s in ("kr1","kr2") for y in (2024,2025))
        files=[{k:f[k] for k in ("name","mapping","station")} for f in draft["files"] if f["name"].endswith(names)]
        version=self.confirm(draft,files)
        ds=version["id"]
        base=f"/portal/datasets/{ds}/"
        chart=f"/portal/chart-data/{ds}"
        p={"station":"kr2","parameter":"t_air","month":"2025-05","step":"day","version":version["version"]}
        dates={"start":"2025-01-01","end":"2025-12-31"}
        private=self.client.get(base+"neighbor-estimates",params={**p,**dates},headers=self.business)
        self.assertEqual(private.status_code,404)
        self.allow(ds)
        def get(path,params,headers):
            response=self.client.get(path,params=params,headers=headers)
            response.raise_for_status()
            return response
        def observed():
            return [get(base+"report",dict(p,step=step),self.business).content for step in ("day","decade","month")]+[
                get(base+"export",dict(p,format=fmt),self.business).content for fmt in ("csv","json")]+[
                get(chart,p,{"x-weather-key":"test-chart"}).content,
                get(base+"climatology",dict(p,month=5,first=2024,last=2025),self.business).content]
        before=observed()
        results=get(base+"neighbor-estimates",{**p,**dates},self.business).json()["points"]
        self.assertEqual([(r["date"],round(r["estimate"],2)) for r in results],[
            ("2025-05-23",18.10),("2025-05-27",17.14),("2025-07-09",19.98),("2025-08-03",18.24)])
        first=results[0]
        self.assertEqual((first["donor"],first["valid_hours"],first["pairs"]),("kr1",15,90))
        self.assertAlmostEqual(first["estimate"],first["reference"]+first["offset"])
        series=get(chart+"/neighbor-estimates",dict(p,enabled="on"),{"x-weather-key":"test-chart"}).json()
        self.assertEqual(len(series),31)
        self.assertIsNone(series[22]["observed"])
        self.assertEqual(series[22]["estimate"],first["estimate"])
        self.assertEqual(sum(r["estimate"] is not None for r in series),2)
        self.assertEqual(get(chart+"/neighbor-estimates",p,{"x-weather-key":"test-chart"}).json(),[])
        self.assertEqual(get(base+"neighbor-estimates",{**p,**dates,"parameter":"pressure_hpa"},self.business).json()["points"],[])
        self.assertEqual(observed(),before)
        saved=self.client.post("/portal/saved",json={"dataset":ds,**p},headers=self.business).json()["report"]
        self.assertEqual(saved["rows"],json.loads(before[0])["rows"])
        self.assertIsNone(saved["rows"][22]["value"])
        measured=get(base+"evidence",dict(p,date=first["date"]),self.business).json()["rows"]
        hours={int(r["timestamp"][11:13]) for r in measured if r["status"]=="observed"}
        for hour in sorted(set(range(24))-hours)[:3]:
            self.client.post("/portal/stream/"+ds,headers={"Authorization":"Bearer test-station"},json={
                "id":f"gap-{hour}","station":"kr2","timestamp":f"2025-05-23 {hour:02}:23","values":{"t_air":15}}).raise_for_status()
        day=dict(p,start=first["date"],end=first["date"])
        self.assertEqual(get(base+"neighbor-estimates",day,self.business).json()["points"],[])
        self.assertEqual(get(base+"neighbor-estimates",dict(day,revision=0),self.business).json()["points"],[first])

    def test_stream_late_retry_recover_and_stale(self):
        v=self.fixture();ds=v["id"]; self.allow(ds)
        rule={"station":"kr1","parameter":"t_air","threshold":20,"duration_minutes":60,"min_slots":2,"stale_minutes":61,"recipient":"Дежурный"}
        self.client.post(f"/portal/datasets/{ds}/rules",json=rule,headers=self.owner).raise_for_status()
        auth={"Authorization":"Bearer test-station"}
        def post(hour,value,key=None):
            body={"station":"kr1","id":key or str(hour),"timestamp":f"2024-04-03 {hour:02}:00","values":{"t_air":value}}
            return self.client.post("/portal/stream/"+ds,json=body,headers=auth)
        post(6,22).raise_for_status();post(5,22).raise_for_status()
        self.assertTrue(post(6,22).json()["duplicate"])
        self.assertEqual(post(6,24).status_code,422)
        post(7,10).raise_for_status()
        events=self.client.get("/portal/events",headers=self.business).json()["events"]
        self.assertEqual([e["status"] for e in events],["recovered","active"])
        with store.db().begin() as con:
            stream.stale_scan(con,datetime.now(timezone.utc)+timedelta(hours=2))
            stream.stale_scan(con,datetime.now(timezone.utc)+timedelta(hours=2))
        post(8,12)
        events=self.client.get("/portal/events",headers=self.business).json()["events"]
        self.assertEqual([(e["kind"],e["status"]) for e in events[:2]],[("stale","recovered"),("stale","active")])
        self.assertEqual(len(events),4)
