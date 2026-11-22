"""FastAPI application for the Krasnoye analytics console."""

from __future__ import annotations

import io
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .ai import AI
from .analytics import analyse, business_actions_from, estimate_gap, local_narrative_from
from .annotations import AnnotationStore
from .data import catalog, load_dataset
from .report import build_pdf

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"

DATASET = None
ANNOTATIONS = AnnotationStore()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global DATASET
    DATASET = load_dataset()
    yield


app = FastAPI(title="Красное · аналитический пульт", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def dataset():
    if DATASET is None:
        raise HTTPException(status_code=503, detail="Данные ещё загружаются")
    return DATASET


class CurrentSpec(BaseModel):
    station: str = "KR1"
    parameter: str = "t_air"
    start: str = "2024-01-01"
    end: str = "2025-12-31"
    granularity: str = "month"
    baseline: bool = True
    annotations: bool = True
    risk_focus: str | None = None
    title: str | None = None


class DashboardRequest(BaseModel):
    prompt: str = Field(min_length=2, max_length=2000)
    current: CurrentSpec


class ConfigRequest(BaseModel):
    key: str | None = None
    base: str | None = None
    model: str | None = None


class AnnotationRequest(BaseModel):
    station: str
    parameter: str | None = None
    start_date: str
    end_date: str | None = None
    kind: str = "note"
    severity: str = "info"
    title: str = Field(min_length=1, max_length=200)
    note: str = ""
    author: str = ""


class NarrativeRequest(BaseModel):
    station: str
    parameter: str
    start: str
    end: str
    use_ai: bool = True


class ReportRequest(BaseModel):
    station: str
    parameter: str
    start: str
    end: str
    title: str | None = None
    use_ai: bool = True


def _dates(start: str, end: str) -> tuple[date, date]:
    try:
        return date.fromisoformat(start), date.fromisoformat(end)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Дата должна быть в формате ГГГГ-ММ-ДД") from exc


def _annotations_for(station: str, parameter: str, start: date, end: date) -> list[dict]:
    items = ANNOTATIONS.list(station=station, parameter=parameter)
    out = []
    for item in items:
        item_start = date.fromisoformat(item["start_date"])
        item_end = date.fromisoformat(item["end_date"])
        if item_end < start or item_start > end:
            continue
        out.append(item)
    return out


def _context(analysis: dict, annotations: list[dict]) -> dict:
    series = analysis["series"]
    return {
        "station": series["station_label"],
        "place": series["place"],
        "parameter": series["label"],
        "unit": series["unit"],
        "period": [series["start"], series["end"]],
        "summary": series["summary"],
        "trend": analysis["series"]["summary"].get("trend_per_decade"),
        "risks": analysis["risks"].get("risks", []),
        "risk_year": analysis["risks"].get("year"),
        "risk_years_compared": analysis["risks"].get("years_compared"),
        "insights": analysis["insights"],
        "forecast": {
            "method": analysis["forecast"]["statistical"].get("method"),
            "points": analysis["forecast"]["statistical"].get("points", []),
            "external": (analysis["forecast"].get("external") or {}).get("source"),
        },
        "quality": {
            "excluded_months": len(analysis.get("period_quality", {}).get("excluded_months", [])),
            "flags": analysis["eda"]["quality"]["flags"],
            "reasons": analysis.get("period_quality", {}).get("exclusion_reasons", {}),
        },
        "annotations": [
            {
                "title": item["title"],
                "kind": item["kind_label"],
                "severity": item["severity"],
                "period": [item["start_date"], item["end_date"]],
                "note": item["note"],
                "author": item["author"],
            }
            for item in annotations
        ],
        "actions": business_actions_from(analysis),
    }


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)


@app.get("/api/health")
def health():
    ds = dataset()
    return {"ok": True, "rows": ds.meta["rows"], "stations": list(ds.frames), "ai": AI.status()["available"]}


@app.get("/api/catalog")
def get_catalog():
    return catalog(dataset())


@app.get("/api/series")
def get_series(
    station: str = Query(...),
    parameter: str = Query(...),
    start: str = Query(...),
    end: str = Query(...),
    granularity: str = Query("month"),
):
    ds = dataset()
    first, last = _dates(start, end)
    try:
        return ds.series(station, parameter, first, last, granularity)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/quality")
def get_quality(station: str = Query(...), parameter: str = Query(...)):
    try:
        return dataset().quality(station, parameter)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/gap-fill")
def gap_fill(station: str = Query(...), parameter: str = Query(...), day: str = Query(...)):
    ds = dataset()
    try:
        target = date.fromisoformat(day)
        return estimate_gap(ds, station, parameter, target)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/eda")
def get_eda(station: str = Query(...), parameter: str = Query(...)):
    try:
        return dataset().eda(station, parameter)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/analysis")
def get_analysis(
    station: str = Query(...),
    parameter: str = Query(...),
    start: str = Query(...),
    end: str = Query(...),
):
    ds = dataset()
    first, last = _dates(start, end)
    try:
        analysis = analyse(ds, station, parameter, first, last)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    annotations = _annotations_for(station, parameter, first, last)
    analysis["annotations"] = annotations
    analysis["actions"] = business_actions_from(analysis)
    analysis["narrative"] = local_narrative_from(analysis, annotations)
    return analysis


@app.get("/api/annotations")
def list_annotations(station: str | None = None, parameter: str | None = None):
    return {"items": ANNOTATIONS.list(station=station, parameter=parameter), "stats": ANNOTATIONS.stats()}


@app.post("/api/annotations")
def create_annotation(payload: AnnotationRequest):
    try:
        item = ANNOTATIONS.create(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"item": item, "stats": ANNOTATIONS.stats()}


@app.delete("/api/annotations/{annotation_id}")
def delete_annotation(annotation_id: int):
    if not ANNOTATIONS.delete(annotation_id):
        raise HTTPException(status_code=404, detail="Разметка не найдена")
    return {"ok": True, "stats": ANNOTATIONS.stats()}


@app.get("/api/ai/status")
def ai_status():
    return AI.status()


@app.post("/api/ai/config")
def ai_config(payload: ConfigRequest):
    return AI.configure(payload.key, payload.base, payload.model)


@app.post("/api/ai/dashboard")
def ai_dashboard(payload: DashboardRequest):
    return AI.compose(payload.prompt, payload.current.model_dump(), catalog(dataset()))


@app.post("/api/ai/narrative")
def ai_narrative(payload: NarrativeRequest):
    ds = dataset()
    start, end = _dates(payload.start, payload.end)
    annotations = _annotations_for(payload.station, payload.parameter, start, end)
    analysis = analyse(ds, payload.station, payload.parameter, start, end)
    context = _context(analysis, annotations)
    local = local_narrative_from(analysis, annotations)
    result = AI.narrative(context) if payload.use_ai else {"text": None, "source": "local"}
    text = result.get("text") or local
    return {
        "text": text,
        "source": result.get("source", "local"),
        "error": result.get("error"),
        "actions": context["actions"],
        "insights": context["insights"],
        "risks": context["risks"],
    }


@app.post("/api/report")
def report(payload: ReportRequest):
    ds = dataset()
    start, end = _dates(payload.start, payload.end)
    annotations = _annotations_for(payload.station, payload.parameter, start, end)
    narrative = None
    analysis = analyse(ds, payload.station, payload.parameter, start, end)
    if payload.use_ai:
        context = _context(analysis, annotations)
        result = AI.narrative(context)
        narrative = result.get("text")
    try:
        pdf = build_pdf(ds, payload.station, payload.parameter, start, end, annotations, narrative, analysis)
    except Exception as exc:  # noqa: BLE001 - report errors must be readable
        raise HTTPException(status_code=500, detail=f"Не удалось собрать PDF: {exc}") from exc
    filename = f"krasnoe-{payload.station.lower()}-{payload.parameter}-{start}-{end}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.exception_handler(ValueError)
async def value_error_handler(request, exc):
    return JSONResponse(status_code=400, content={"detail": str(exc)})
