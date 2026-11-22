"""Append-only expert interpretations of observations, shared under dataset ACLs."""
from datetime import date
from hashlib import sha256
from typing import Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from meteo.access import pick_dataset
from meteo.importer import PARAMETERS, canonical


class Annotation(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    station: str = Field(min_length=1, max_length=50)
    parameter: str = Field(min_length=1, max_length=64)
    start: date
    end: date
    label: Literal['anomaly', 'sensor_issue', 'weather_event', 'normal', 'gap']
    note: str = Field(min_length=1, max_length=1000)


def list_annotations(db, dataset, station, parameter, start, end):
    """Read notes intersecting an authorized station/parameter/date selection."""
    return [dict(row) for row in db.execute("""
        SELECT id,dataset,station,parameter,start,end,label,note,author,created_at
        FROM expert_annotations
        WHERE dataset=? AND station=? AND parameter=? AND start<=? AND end>=?
        ORDER BY start,created_at,id
    """, (dataset, station, parameter, str(end), str(start)))]


def check_slice(parameter, start, end):
    if parameter not in PARAMETERS or end < start:
        raise HTTPException(422, 'Выберите существующий параметр и укажите даты по порядку.')


def check_annotation_window(db, version, marker):
    check_slice(marker.parameter, marker.start, marker.end)
    series = (version, marker.station, '$.' + marker.parameter)
    # First and last observations of this parameter bound the range. Missing days
    # inside a real series may themselves be the subject of an expert note.
    observations = """FROM observations WHERE dataset=? AND station=?
                      AND json_type(values_json, ?) IS NOT NULL"""
    first = db.execute('SELECT stamp ' + observations + ' ORDER BY stamp LIMIT 1', series).fetchone()
    last = db.execute('SELECT stamp ' + observations + ' ORDER BY stamp DESC LIMIT 1', series).fetchone()
    if not first or str(marker.start) < first['stamp'][:10] or str(marker.end) > last['stamp'][:10]:
        raise HTTPException(422, 'Интервал выходит за границы ряда этой станции и параметра.')
    if marker.label == 'gap':
        return
    measured = db.execute('SELECT 1 ' + observations + ' AND stamp >= ? AND stamp <= ? LIMIT 1',
                          (*series, str(marker.start), str(marker.end) + ' 23:59:59.999999')).fetchone()
    if not measured:
        raise HTTPException(422, 'В интервале нет исходных записей параметра. Можно отметить пропуск.')


def register_annotations(app, current_store, actor):
    route = '/api/datasets/{version}/annotations'

    @app.get(route)
    def annotations(version: str, station: str, parameter: str, start: date, end: date,
                    user=Depends(actor)):
        with current_store().connect() as db:
            dataset = pick_dataset(db, version, user)
            check_slice(parameter, start, end)
            return {'can_annotate': user['role'] == 'research' and bool(dataset['can_edit']),
                    'items': list_annotations(db, version, station, parameter, start, end)}

    @app.post(route, status_code=201)
    def annotate(version: str, marker: Annotation, user=Depends(actor)):
        with current_store().connect(write=True) as db:
            pick_dataset(db, version, user, permission='edit')
            if user['role'] != 'research':
                raise HTTPException(403, 'Экспертная разметка доступна исследователю с правами редактирования.')
            check_annotation_window(db, version, marker)
            author = user['name']
            # Retries of the same submission keep its original provenance. Two
            # experts may independently mark the same interval, including disagreements.
            values = (version, marker.station, marker.parameter, str(marker.start), str(marker.end),
                      marker.label, marker.note, author)
            identifier = sha256(canonical(values).encode()).hexdigest()
            db.execute("""INSERT INTO expert_annotations(id,dataset,station,parameter,start,end,label,note,author)
                          VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING""", (identifier, *values))
            return dict(db.execute('SELECT * FROM expert_annotations WHERE id=?', (identifier,)).fetchone())
