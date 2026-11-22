"""Dashboard drafts are private to a researcher and their selected data slice."""
from hashlib import sha256
from typing import Annotated

from fastapi import Depends, HTTPException, Query

from meteo.access import pick_dataset
from meteo.chart_workspace import (
    AssistantRequest, AssistantUnavailable, ChartDraft, ChartSelection, SaveChart, chart_assistant,
)
from meteo.importer import canonical


def workspace_key(db, version, user, selection):
    dataset = pick_dataset(db, version, user)
    if selection.step == 'instant' and dataset['source'] not in {'stream', 'stream_snapshot'}:
        raise ValueError('Показ по отдельным измерениям доступен для потока')
    station = db.execute('SELECT 1 FROM observations WHERE dataset=? AND station=? LIMIT 1',
                         (version, selection.station)).fetchone()
    if station is None:
        raise HTTPException(404, 'Станция недоступна')
    return sha256(canonical([version, selection.model_dump(mode='json')]).encode()).hexdigest()


def register_chart_assistant(app, current_store, researcher):
    @app.get('/api/datasets/{version}/chart-workspace')
    def read_chart(version: str, selection: Annotated[ChartSelection, Query()], user=Depends(researcher)):
        with current_store().connect() as db:
            key = workspace_key(db, version, user, selection)
            row = db.execute("SELECT body FROM settings WHERE owner=? AND kind='chart-v1' AND name=?",
                             (user['name'], key)).fetchone()
        return ChartDraft.model_validate_json(row['body']) if row else ChartDraft()

    @app.put('/api/datasets/{version}/chart-workspace')
    def save_chart(version: str, request: SaveChart, user=Depends(researcher)):
        draft = ChartDraft(settings=request.settings, edits=request.edits)
        if request.selection.parameter == 'wind_dir' and any(
            edit.value is not None and not 0 <= edit.value < 360 for edit in draft.edits.values()
        ):
            raise ValueError('Направление задаётся в градусах от 0 до 360, не включая 360')
        with current_store().connect(write=True) as db:
            key = workspace_key(db, version, user, request.selection)
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?,?,?)',
                       (user['name'], 'chart-v1', key, draft.model_dump_json()))
        return draft

    @app.post('/api/datasets/{version}/chart-assistant')
    def suggest_chart(version: str, request: AssistantRequest, user=Depends(researcher)):
        with current_store().connect() as db:
            workspace_key(db, version, user, request.selection)
        try:
            return chart_assistant(request)
        except AssistantUnavailable as error:
            raise HTTPException(502, str(error)) from error
