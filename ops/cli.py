"""Run from the repository root: python -m ops.cli init|serve|worker|receipt."""
import argparse
import os
from pathlib import Path

from ops.init_env import create


def environment(path):
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            name,val=line.split("=",1)
            os.environ.setdefault(name.strip(),val.strip())


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("command",choices=["init","serve","worker","receipt"])
    parser.add_argument("--env",default=".env")
    parser.add_argument("--port",type=int,default=8123)
    args=parser.parse_args()
    if args.command=="init":
        create(args.env)
    else:
        environment(args.env)
        if args.command=="serve":
            import uvicorn
            uvicorn.run("weather.app:app",host="127.0.0.1",port=args.port)
        elif args.command=="receipt":
            import uvicorn
            uvicorn.run("ops.receipt:app",host="127.0.0.1",port=args.port)
        else:
            import runpy
            runpy.run_module("weather.worker",run_name="__main__")
