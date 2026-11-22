"""Single-writer storage for raw files, observations and reproducible reports."""
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import sqlite3

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


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.raw = self.root / 'raw'
        self.raw.mkdir(parents=True, exist_ok=True)
        self.db = self.root / 'meteo.sqlite3'
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript(SCHEMA)
            db.execute('PRAGMA user_version=1')

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
