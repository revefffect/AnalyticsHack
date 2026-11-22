#!/bin/sh
set -eu
cd "$(dirname "$0")"
exec python3 -m uvicorn app.server:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8010}"
