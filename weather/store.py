"""Versioned metadata, access grants and content-addressed weather files."""
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from sqlalchemy import create_engine, event, text

ROOT = Path(__file__).resolve().parents[1]


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def home():
    path = Path(os.getenv("WEATHER_HOME", ROOT / ".runtime" / "service"))
    path.mkdir(parents=True, exist_ok=True)
    return path


@lru_cache(maxsize=4)
def engine_for(url):
    db = create_engine(url, pool_pre_ping=True)
    if url.startswith("sqlite"):
        @event.listens_for(db, "connect")
        def configure(connection, _):
            connection.execute("PRAGMA busy_timeout=30000")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
    return db


def db():
    return engine_for(os.getenv("DATABASE_URL") or "sqlite:///" + str(home() / "weather.sqlite"))


def migrate():
    # The migration is idempotent for concurrent API and worker startup.
    with db().begin() as con:
        if con.dialect.name == "postgresql":
            con.execute(text("select pg_advisory_xact_lock(660321)"))
        path = ROOT / "ops" / "001-platform.sql"
        for statement in path.read_text(encoding="utf-8").split(";"):
            if statement.strip():
                con.execute(text(statement))


def execute(con, sql, **values):
    return con.execute(text(sql), values)


def one(con, sql, **values):
    row = execute(con, sql, **values).mappings().first()
    return dict(row) if row else None


def many(con, sql, **values):
    return [dict(row) for row in execute(con, sql, **values).mappings()]


@lru_cache(maxsize=3)
def s3(endpoint):
    import boto3
    return boto3.client("s3", endpoint_url=endpoint or None)


def put(data):
    key = digest(data)
    bucket = os.getenv("WEATHER_BUCKET")
    if bucket:
        from botocore.exceptions import ClientError
        try:
            s3(os.getenv("S3_ENDPOINT")).put_object(Bucket=bucket, Key=key, Body=data, IfNoneMatch="*")
        except ClientError as exc:
            if exc.response["ResponseMetadata"]["HTTPStatusCode"] != 412:
                raise
    else:
        path = home() / "blobs" / key[:2] / key
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                stream.write(data)
                tmp = Path(stream.name)
            try:
                os.replace(tmp, path)
            finally:
                tmp.unlink(missing_ok=True)
    return key


def get(key):
    bucket = os.getenv("WEATHER_BUCKET")
    if bucket:
        return s3(os.getenv("S3_ENDPOINT")).get_object(Bucket=bucket, Key=key)["Body"].read()
    return (home() / "blobs" / key[:2] / key).read_bytes()
