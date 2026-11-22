import io
import json
import os
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
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

    def test_guest_opens_archive_without_granting_workspace_access(self):
        private=self.fixture()
        for url in ("/","/portal?tab=atlas","/portal?demo=report&station=kr2"):
            landing=self.client.get(url)
            self.assertIn("/portal/static/guest.js",landing.text)
            self.assertIn("no-store",landing.headers["cache-control"])
        for url in ("/portal?login=1",f"/portal?dataset={private['id']}"):
            self.assertIn("/portal/static/portal.js",self.client.get(url).text)
        self.assertIn("/portal/static/portal.js",self.client.get("/portal",headers=self.owner).text)
        for path in ("/portal/me","/portal/catalog","/portal/events","/charts/business/api/search","/charts/research/api/search"):
            self.assertEqual(self.client.get(path).status_code,401)
        params={"station":"kr1","parameter":"t_air","month":"2024-04"}
        self.assertEqual(self.client.get(f"/portal/datasets/{private['id']}/report",params=params).status_code,401)
        self.assertEqual(self.client.post("/portal/imports/demo-preview").status_code,401)
        self.assertEqual(self.client.post("/charts/demo/api/dashboards/db",json={}).status_code,405)
        self.assertEqual(self.client.get("/portal/chart-data/"+private["id"],params=params).status_code,401)
        public=self.client.get("/portal/static/organizer-archive.json")
        self.assertNotIn(private["id"],public.text)
        data=public.json()
        self.assertEqual(data["sha256"],store.digest((ROOT/"data/raw/meteo_hack_dataset.zip").read_bytes()))
        self.assertEqual(sum(s.get("records",0) for s in data["sites"]),874560)
        self.assertEqual(data["results"]["kr1"]["pressure_days"],2)
        self.assertAlmostEqual(data["results"]["ame60"]["mean"],-15.20,places=2)
        kr2=data["results"]["kr2"]
        self.assertEqual(len(kr2["estimates"]),4)
        board=json.loads((ROOT/"ops/guest-grafana/dashboards/kr2.json").read_text(encoding="utf-8"))
        target=board["panels"][0]["targets"][0]
        self.assertEqual(target["queryType"],"snapshot")
        dates,observed,estimated=target["snapshot"][0]["data"]["values"]
        series={datetime.fromtimestamp(t/1000,timezone.utc).date().isoformat():(o,e) for t,o,e in zip(dates,observed,estimated,strict=True)}
        self.assertEqual({d:v for d,(_,v) in series.items() if v is not None},{p["date"]:p["estimate"] for p in kr2["estimates"]})
        self.assertTrue(all(series[p["date"]][0] is None for p in kr2["estimates"]))

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

    def test_atlas_keeps_matching_codes_and_granted_versions_separate(self):
        def read(role):
            response=self.client.get("/portal/catalog",headers=role)
            response.raise_for_status()
            return [s for s in response.json()["station_atlas"] if "selection" in s]

        def upload(value, dataset=None):
            data=HEAD+"".join(f"2024-04-01 {h:02}:00,{value},995,\n" for h in range(24))
            return self.confirm(self.preview("kr1.csv",data.encode()),dataset=dataset)

        cold,warm=upload(-7),upload(12)
        cards=read(self.owner)
        self.assertEqual(len({s["id"] for s in cards}),2)
        self.assertEqual(read(self.business),[])
        for s in cards:
            p=s["selection"]
            self.assertEqual((s["name"],s["location_kind"],s["question"],s["records"]),("KR1","unknown",None,24))
            self.assertEqual(parse_qs(urlsplit(s["report_url"]).query),{k:[str(v)] for k,v in p.items()})
            graph=urlsplit(s["grafana_url"])
            self.assertEqual(graph.path,"/charts/research/d/w-"+p["dataset"]+"/weather")
            self.assertEqual(parse_qs(graph.query),{"var-"+k:[str(v)] for k,v in p.items()})
            rows=self.query(p["dataset"],version=p["version"]).json()["rows"]
            chart=self.client.get("/portal/chart-data/"+p["dataset"],params=p,headers={"x-weather-key":"test-owner-chart"})
            self.assertEqual(chart.json(),rows)
            self.assertEqual(rows[0]["value"],-7 if p["dataset"]==cold["id"] else 12)
        self.allow(cold["id"])
        new=upload(-9,cold["id"])
        self.assertEqual(read(self.business)[0]["selection"]["version"],cold["version"])
        self.assertEqual(len(read(self.business)),1)
        self.assertIn("/charts/business/",read(self.business)[0]["grafana_url"])
        self.assertEqual(self.query(warm["id"],self.business).status_code,404)
        self.assertEqual(self.query(cold["id"],version=warm["version"]).status_code,404)
        self.assertEqual(self.query(cold["id"],self.business,version=new["version"]).status_code,404)
        self.allow(cold["id"])
        self.assertEqual(read(self.business)[0]["selection"]["version"],new["version"])
        self.client.post("/portal/datasets/"+cold["id"]+"/publish?public=false",headers=self.owner).raise_for_status()
        self.assertEqual(read(self.business),[])

    def test_zip_same_code_requires_identity_or_separate_imports(self):
        raw=io.BytesIO()
        with ZipFile(raw,"w") as archive:
            for site,value in (("north",-7),("south",12)):
                archive.writestr(site+"/kr1.csv",HEAD+"".join(f"2024-04-01 {h:02}:00,{value},995,\n" for h in range(24)))
        p=self.preview("stations.zip",raw.getvalue())
        choices=[{k:f[k] for k in ("name","station","mapping")} for f in p["files"]]
        path="/portal/imports/"+p["id"]+"/confirm"
        body={"title":"Тест","files":choices}
        self.assertEqual(self.client.post(path,json=body,headers=self.owner).status_code,422)
        ids=[self.confirm(p,[choice])["id"] for choice in choices]
        self.assertEqual(len(set(ids)),2)
        self.assertEqual([self.query(ds).json()["rows"][0]["value"] for ds in ids],[-7,12])
        self.assertEqual(self.confirm(p,choices[:1])["id"],ids[0])
        merged=self.client.post(path,json={**body,"combine_stations":["kr1"]},headers=self.owner)
        merged.raise_for_status()
        self.assertEqual(merged.json()["count"],48)
        self.assertNotIn(merged.json()["id"],ids)

    def test_archive_oracles_and_partial_range(self):
        raw=(ROOT/"data/raw/meteo_hack_dataset.zip").read_bytes()
        p=self.preview("archive.zip",raw)
        parts=[{k:f[k] for k in ("name","mapping","station")} for f in p["files"] if f["name"].endswith(("kr1_2024.csv","ame60_2010.csv"))]
        ds=self.confirm(p,parts)["id"]
        atlas=self.client.get("/portal/catalog",headers=self.owner).json()["station_atlas"]
        self.assertEqual({s["question"] for s in atlas if s["location_kind"]=="area"},{"pressure","january"})
        self.assertEqual({s["code"] for s in atlas if s["location_kind"]=="reference"},{"RU-Fyo","RU-Fy2"})
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
