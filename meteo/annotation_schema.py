"""SQLite migration for immutable, version-scoped expert annotations."""

SCHEMA_V4 = (
    """
    CREATE TABLE IF NOT EXISTS expert_annotations (
        id TEXT PRIMARY KEY,
        dataset TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
        station TEXT NOT NULL,
        parameter TEXT NOT NULL,
        start TEXT NOT NULL,
        end TEXT NOT NULL CHECK(end >= start),
        label TEXT NOT NULL CHECK(label IN ('anomaly','sensor_issue','weather_event','normal','gap')),
        note TEXT NOT NULL,
        author TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    )
    """,
    """CREATE INDEX IF NOT EXISTS ix_expert_annotations_slice
       ON expert_annotations(dataset, station, parameter, start, end)""",
)
