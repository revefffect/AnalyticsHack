from os import environ
from pathlib import Path

SECRET_KEY = environ["SUPERSET_SECRET_KEY"]
SUPERSET_HOME = Path(environ["SUPERSET_HOME"]).resolve()
SUPERSET_HOME.mkdir(parents=True, exist_ok=True)
SQLALCHEMY_DATABASE_URI = f"sqlite:///{SUPERSET_HOME / 'superset.db'}"
ENABLE_PROXY_FIX = False
SUPERSET_WEBSERVER_TIMEOUT = 45


def file_cache(name):
    path = SUPERSET_HOME / "cache" / name
    path.mkdir(parents=True, exist_ok=True)
    return {"CACHE_TYPE": "FileSystemCache", "CACHE_DIR": str(path), "CACHE_DEFAULT_TIMEOUT": 300}


CACHE_CONFIG = file_cache("general")
DATA_CACHE_CONFIG = file_cache("data")
FILTER_STATE_CACHE_CONFIG = file_cache("filters")
EXPLORE_FORM_DATA_CACHE_CONFIG = file_cache("explore")
