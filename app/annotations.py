"""Expert annotations: the manual layer over the archive.

Annotations are deliberately separate from measurements. They never change a
calculated value; they mark what an expert wants a manager or a colleague to
see, and they travel into the PDF report with the author's name.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import date, datetime
from pathlib import Path

from .data import ANNOTATION_KINDS, PARAMETERS, SEVERITIES, STATION_PARAMS, STATIONS

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "annotations.db"

SEED = [
    {
        "station": "KR1",
        "parameter": "pressure_hpa",
        "start_date": "2024-04-01",
        "end_date": "2024-04-30",
        "kind": "exclusion",
        "severity": "critical",
        "title": "Апрель 2024: давление исключено",
        "note": "1 362 значения pressure_hpa:range; пригодны 2 дня из 30, серия неполных дней — 28. В отчёт давление за апрель не ставим.",
        "author": "Эксперт-метеоролог",
    },
    {
        "station": "AME60",
        "parameter": "t_air",
        "start_date": "2010-01-01",
        "end_date": "2010-01-31",
        "kind": "anomaly",
        "severity": "warning",
        "title": "Январь 2010: −15,20 °C",
        "note": "Отклонение −7,63 °C от среднего за 1992–2021 (−7,57 °C по 13 пригодным январям). Год входит в базу.",
        "author": "Эксперт-метеоролог",
    },
    {
        "station": "AME60",
        "parameter": "soil_05",
        "start_date": "2016-01-01",
        "end_date": "2017-12-31",
        "kind": "exclusion",
        "severity": "warning",
        "title": "Сбой датчика почвы 5 см",
        "note": "12 416 отметок soil_05:fault. Исключаем только этот параметр; температура воздуха и другие глубины остаются пригодны.",
        "author": "Эксперт-метеоролог",
    },
]


class AnnotationStore:
    def __init__(self, path: Path = DB_PATH):
        self.path = path
        self.lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._init()
        self._seed()

    def _init(self) -> None:
        with self.connection:
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS annotations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    station TEXT NOT NULL,
                    parameter TEXT,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    title TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    author TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    seed INTEGER NOT NULL DEFAULT 0
                )
                """
            )

    def _seed(self) -> None:
        with self.lock, self.connection:
            count = self.connection.execute("SELECT COUNT(*) FROM annotations").fetchone()[0]
            if count:
                return
            for item in SEED:
                self.connection.execute(
                    """
                    INSERT INTO annotations
                    (station, parameter, start_date, end_date, kind, severity, title, note, author, created_at, seed)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        item["station"],
                        item["parameter"],
                        item["start_date"],
                        item["end_date"],
                        item["kind"],
                        item["severity"],
                        item["title"],
                        item["note"],
                        item["author"],
                        datetime.now().isoformat(timespec="seconds"),
                    ),
                )

    def list(self, station: str | None = None, parameter: str | None = None) -> list[dict]:
        query = "SELECT * FROM annotations"
        clauses = []
        args: list[str] = []
        if station:
            clauses.append("station = ?")
            args.append(station)
        if parameter:
            clauses.append("(parameter = ? OR parameter IS NULL)")
            args.append(parameter)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY start_date DESC, id DESC"
        with self.lock:
            rows = self.connection.execute(query, args).fetchall()
        return [self._row(row) for row in rows]

    def create(self, payload: dict) -> dict:
        station = str(payload.get("station", "")).upper()
        if station not in STATIONS:
            raise ValueError("Неизвестная станция")
        parameter = payload.get("parameter") or None
        if parameter and parameter not in STATION_PARAMS[station]:
            raise ValueError("Параметр не измеряется этой станцией")
        kind = payload.get("kind", "note")
        if kind not in ANNOTATION_KINDS:
            raise ValueError("Неизвестный тип разметки")
        severity = payload.get("severity", "info")
        if severity not in SEVERITIES:
            raise ValueError("Неизвестная важность")
        start = date.fromisoformat(str(payload.get("start_date")))
        end = date.fromisoformat(str(payload.get("end_date") or start))
        if start > end:
            raise ValueError("Начало разметки позже конца")
        title = str(payload.get("title", "")).strip()
        if not title:
            raise ValueError("Нужен заголовок разметки")
        note = str(payload.get("note", "")).strip()
        author = str(payload.get("author", "")).strip() or "Эксперт"
        created = datetime.now().isoformat(timespec="seconds")
        with self.lock, self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO annotations
                (station, parameter, start_date, end_date, kind, severity, title, note, author, created_at, seed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (station, parameter, start.isoformat(), end.isoformat(), kind, severity, title, note, author, created),
            )
            row = self.connection.execute("SELECT * FROM annotations WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return self._row(row)

    def delete(self, annotation_id: int) -> bool:
        with self.lock, self.connection:
            cursor = self.connection.execute("DELETE FROM annotations WHERE id = ?", (annotation_id,))
        return cursor.rowcount > 0

    def stats(self) -> dict:
        with self.lock:
            rows = self.connection.execute(
                "SELECT kind, severity, COUNT(*) AS n FROM annotations GROUP BY kind, severity"
            ).fetchall()
        return {
            "total": sum(row["n"] for row in rows),
            "by_kind": {row["kind"]: row["n"] for row in rows},
            "by_severity": {row["severity"]: row["n"] for row in rows},
        }

    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        item = dict(row)
        item["kind_label"] = ANNOTATION_KINDS.get(item["kind"], item["kind"])
        item["severity_label"] = SEVERITIES.get(item["severity"], item["severity"])
        item["parameter_label"] = PARAMETERS.get(item["parameter"], {}).get("label") if item.get("parameter") else "Вся станция"
        item["station_label"] = STATIONS.get(item["station"], {}).get("label", item["station"])
        return item
