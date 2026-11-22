"""Single-writer storage for raw files, observations and reproducible reports."""
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import sqlite3
from time import monotonic, sleep

from meteo.annotation_schema import SCHEMA_V4

SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL,
    raw_hash TEXT REFERENCES blobs(hash), source TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    visibility TEXT NOT NULL DEFAULT 'private', row_count INTEGER NOT NULL DEFAULT 0,
    metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS blobs (hash TEXT PRIMARY KEY, size INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS uploads (
    owner TEXT NOT NULL, hash TEXT NOT NULL REFERENCES blobs(hash), name TEXT NOT NULL,
    PRIMARY KEY (owner, hash)
);
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY,
    dataset TEXT NOT NULL REFERENCES datasets(id), station TEXT NOT NULL,
    stamp TEXT NOT NULL, original_stamp TEXT NOT NULL, zone TEXT NOT NULL,
    received_at TEXT, source_file TEXT NOT NULL, source_row INTEGER NOT NULL,
    values_json TEXT NOT NULL, flags TEXT NOT NULL, signature TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_observation_slice ON observations(dataset, station, stamp, zone);
CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, dataset TEXT NOT NULL REFERENCES datasets(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    owner TEXT NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL, body TEXT NOT NULL,
    PRIMARY KEY (owner, kind, name)
);
CREATE TABLE IF NOT EXISTS subscriptions (
    owner TEXT NOT NULL, dataset TEXT NOT NULL REFERENCES datasets(id), PRIMARY KEY (owner, dataset)
);
CREATE TABLE IF NOT EXISTS stream_messages (
    feed TEXT NOT NULL, message_id TEXT NOT NULL, body TEXT NOT NULL,
    snapshot TEXT NOT NULL REFERENCES datasets(id), received_at TEXT NOT NULL,
    PRIMARY KEY (feed, message_id)
);
CREATE TABLE IF NOT EXISTS alert_rules (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, body TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'insufficient', changed_at TEXT
);
CREATE TABLE IF NOT EXISTS alert_events (
    id TEXT PRIMARY KEY, rule TEXT NOT NULL REFERENCES alert_rules(id),
    state TEXT NOT NULL, happened_at TEXT NOT NULL, body TEXT NOT NULL,
    delivery TEXT NOT NULL DEFAULT 'pending'
);
"""

SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS station_photos (
    dataset TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE, station TEXT NOT NULL,
    credit TEXT NOT NULL, media_type TEXT NOT NULL, image BLOB NOT NULL,
    PRIMARY KEY (dataset, station)
)
"""


SCHEMA_V3 = """
CREATE TABLE IF NOT EXISTS dataset_members (
    dataset TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    username TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('reader','editor')),
    PRIMARY KEY (dataset, username)
)
"""


def enable_wal(db):
    # Changing journal mode can return SQLITE_BUSY immediately during simultaneous starts.
    deadline = monotonic() + 60
    while True:
        try:
            db.execute('PRAGMA journal_mode=WAL')
            return
        except sqlite3.OperationalError as exc:
            if (exc.sqlite_errorcode & 0xff) != sqlite3.SQLITE_BUSY or monotonic() >= deadline:
                raise
            sleep(0.05)


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.raw = self.root / 'raw'
        self.raw.mkdir(parents=True, exist_ok=True)
        self.db = self.root / 'meteo.sqlite3'
        with self.connect() as db:
            if db.execute('PRAGMA user_version').fetchone()[0] >= 4:
                return
            enable_wal(db)
            db.execute('BEGIN IMMEDIATE')
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version < 1:
                # executescript commits an active transaction before running the script.
                for statement in SCHEMA.split(';'):
                    if statement.strip():
                        db.execute(statement)
            if version < 2:
                db.execute(SCHEMA_V2)
            if version < 3:
                db.execute(SCHEMA_V3)
            if version < 4:
                for statement in SCHEMA_V4:
                    db.execute(statement)
                db.execute('PRAGMA user_version=4')

    @contextmanager
    def connect(self, *, write=False):
        db = sqlite3.connect(self.db, timeout=60)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        finally:
            db.close()

    def save_blob(self, owner, name, payload):
        digest = sha256(payload).hexdigest()
        path = self.raw / digest
        with self.connect(write=True) as db:
            if not path.exists():
                temporary = path.with_suffix('.part')
                temporary.write_bytes(payload)
                temporary.replace(path)
            db.execute('INSERT OR IGNORE INTO blobs VALUES (?, ?)', (digest, len(payload)))
            db.execute('INSERT OR IGNORE INTO uploads VALUES (?, ?, ?)', (owner, digest, name))
        return digest

    def upload(self, owner, digest):
        with self.connect() as db:
            row = db.execute('SELECT name FROM uploads WHERE owner=? AND hash=?', (owner, digest)).fetchone()
        if row is None:
            raise LookupError('Файл не найден')
        return row['name'], self.raw / digest
