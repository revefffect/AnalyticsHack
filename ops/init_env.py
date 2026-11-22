"""Create local configuration without installing application dependencies."""
import argparse
import secrets
from pathlib import Path


def create(target):
    path=Path(target)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("x",encoding="utf-8") as out:
        for name in ("RESEARCH_TOKEN","BUSINESS_TOKEN","STREAM_TOKEN","GRAFANA_RESEARCH_KEY","GRAFANA_BUSINESS_KEY",
                     "GRAFANA_RESEARCH_ADMIN","GRAFANA_BUSINESS_ADMIN","POSTGRES_PASSWORD"):
            out.write(name+"="+secrets.token_urlsafe(30)+"\n")
    return path


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("path",nargs="?",default=".env")
    create(p.parse_args().path)
