#!/bin/sh
set -eu
superset db upgrade
superset init
python /app/meteo-superset/provision.py
exec gunicorn --bind 0.0.0.0:8088 --workers 2 --timeout 55 'superset.app:create_app()'
