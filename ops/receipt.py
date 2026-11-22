"""Standalone webhook receiver for local delivery checks; never expose this demo service publicly."""
import os
from pathlib import Path
import sqlite3
from fastapi import FastAPI, Request

from weather import store

app=FastAPI()


@app.post("/events")
async def receive(request:Request):
    payload=await request.json()
    if not payload.get("id") or request.headers.get("Idempotency-Key")!=payload["id"]:
        from fastapi import HTTPException
        raise HTTPException(422,"Требуется идентификатор события.")
    path=Path(os.getenv("RECEIPT_DB",store.home()/"receipts.sqlite"))
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS receipt(id TEXT PRIMARY KEY,received TEXT,payload TEXT)")
        conn.execute("INSERT OR IGNORE INTO receipt VALUES(?,?,?)",(payload["id"],store.utcnow(),store.packed(payload)))
    return {"id":payload["id"]}
