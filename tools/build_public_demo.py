"""Build the public atlas and Grafana panels from the bundled organizer archive only."""
import json
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import mean
from unittest.mock import patch

from weather import atlas, imports, neighbors, reports, store


ROOT = Path(__file__).resolve().parents[1]
TITLE = "Архив урочища Красное"
DASHBOARDS = ROOT / "ops/guest-grafana/dashboards"


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n", encoding="utf-8")


def chart(title, dates, series, unit):
    times = [int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp()*1000) for day in dates]
    fields = [{"name": "Дата", "type": "time", "config": {}}]
    fields.extend({"name": name, "type": "number", "config": {}} for name in series)
    frame = {"schema": {"fields": fields, "refId": "A"}, "data": {"values": [times, *series.values()]}}
    source = {"type": "grafana", "uid": "grafana"}
    return {"type": "timeseries", "title": title, "datasource": source,
            "targets": [{"refId": "A", "datasource": source, "queryType": "snapshot", "snapshot": [frame]}],
            "fieldConfig": {"defaults": {"unit": unit, "custom": {"spanNulls": False, "showPoints": "always"}},
                            "overrides": []}}


def dashboard(code, start, end, panels):
    for i, panel in enumerate(panels):
        panel.update(id=i+1, gridPos={"x": 0, "y": i*9, "w": 24, "h": 9})
    return {"uid": "krasnoe-"+code, "title": atlas.NAMES[code]+" · "+TITLE, "schemaVersion": 40,
            "editable": False, "time": {"from": start+"T00:00:00Z", "to": end+"T23:59:59Z"},
            "timezone": "utc", "refresh": "", "panels": panels}


def build():
    archive = ROOT / "data/raw/meteo_hack_dataset.zip"
    raw = archive.read_bytes()
    if store.digest(raw) != imports.RESERVE_SHA:
        raise ValueError("Expected the original organizer archive")
    store.migrate()
    preview = imports.prepare("demo", archive.name, raw)
    files = [{k: f[k] for k in ("name", "station", "mapping")} for f in preview["files"]]
    ds = imports.confirm("demo", preview["id"], TITLE, files)["id"]
    with store.db().connect() as con:
        row, version, manifest, _ = reports.visible(con, "demo", ds)
        sites = atlas.stations("demo", row, version, manifest, neighbors.features(manifest))
        results = {}
        for site in sites:
            code = site["code"]
            month = site["selection"]["month"]
            def report(period, parameter="t_air"):
                return reports.report(con, "demo", ds, code, parameter, period, "day", version, 0)
            air = report(month)
            days = air["rows"]
            dates = [r["period"] for r in days]
            site.update(id=code, report_url="/portal?demo=report&station="+code,
                        grafana_url="/charts/demo/d/krasnoe-"+code+"/archive")
            site["selection"] = {"dataset": "organizers", "month": month}
            result = {"month": month, "mean": air["summary"]["value"], "valid_days": air["summary"]["valid"],
                      "days": [{"date": r["period"], "air": r["value"], "slots": r["valid"]} for r in days]}
            panels = [chart("Среднесуточная температура", dates, {"Наблюдения": [r["value"] for r in days]}, "celsius")]
            if code == "kr1":
                pressure = report(month, "pressure_hpa")
                result["pressure_days"] = pressure["summary"]["valid"]
                for item, p in zip(result["days"], pressure["rows"], strict=True):
                    item.update(pressure=p["value"], pressure_slots=p["valid"])
                panels.append(chart("Давление · пригодные сутки", dates, {"Давление": [r["value"] for r in pressure["rows"]]}, "pressurehpa"))
            if code == "ame60":
                years = {str(y): report(f"{y}-01")["summary"]["value"] for y in range(1992, 2022)}
                result["baseline"] = mean(v for v in years.values() if v is not None)
                result["baseline_years"] = sum(v is not None for v in years.values())
            if code == "kr2":
                result["estimates"] = neighbors.layer(con, "demo", ds, code, "t_air", date(2025,1,1), date(2025,12,31), version, 0)["points"]
                estimates = {p["date"]: p["estimate"] for p in result["estimates"]}
                year_rows = [r for m in range(1,13) for r in report(f"2025-{m:02}")["rows"]]
                observed = [r["value"] for r in year_rows if r["value"] is not None]
                result["year_temperature"] = {"valid_days":len(observed), "days":len(year_rows),
                                              "min":min(observed), "max":max(observed)}
                dates = [r["period"] for r in year_rows]
                panels = [chart("Кр2 · наблюдения и оценки пропусков", dates,
                                {"Измерено": [r["value"] for r in year_rows], "Оценка по Кр1": [estimates.get(day) for day in dates]}, "celsius")]
                panels[0]["fieldConfig"]["overrides"] = [{"matcher":{"id":"byName","options":"Оценка по Кр1"},"properties":[
                    {"id":"custom.lineWidth","value":0},{"id":"custom.pointSize","value":9},
                    {"id":"color","value":{"mode":"fixed","fixedColor":"orange"}}]}]
                panels.append(chart("Полнота наблюдений по дням", dates, {"Пригодных часов": [r["valid"] for r in year_rows]}, "short"))
            board = dashboard(code, dates[0], dates[-1], panels)
            write(DASHBOARDS / (code+".json"), board)
            result["chart_panels"] = [{"id": panel["id"], "title": panel["title"]} for panel in board["panels"]]
            results[code] = result
    write(ROOT/"weather/static/organizer-archive.json", {"sha256": imports.RESERVE_SHA,
          "sites": sites+atlas.references(), "results": results})


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"WEATHER_HOME": tmp, "DATABASE_URL": "", "WEATHER_BUCKET": ""}):
        try:
            build()
        finally:
            store.db().dispose()
