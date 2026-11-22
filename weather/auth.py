import hashlib
import hmac
import os
import time

from fastapi import HTTPException, Request



def secret(name):
    return os.getenv(name,"")


def equal(a,b):
    return bool(a and b) and hmac.compare_digest(a.encode(), b.encode())


def actor(request: Request):
    token=request.headers.get("authorization","").removeprefix("Bearer ")
    for role in ("research","business"):
        if equal(token,secret(role.upper()+"_TOKEN")):
            return role
    cookie=request.cookies.get("weather_session","").split(".")
    if len(cookie)==3 and cookie[0] in ("research","business") and cookie[1].isdigit():
        role,expiry,mac=cookie
        token=secret(role.upper()+"_TOKEN")
        if token and int(expiry)>time.time() and equal(mac,sign(role,expiry,token)):
            return role
    raise HTTPException(401,"Войдите в кабинет.")


def sign(role,expiry,token):
    return hmac.new(token.encode(), f"session/v1/{role}/{expiry}".encode(), hashlib.sha256).hexdigest()


def writer(request):
    role=actor(request)
    if role!="research":
        raise HTTPException(403,"Эта операция доступна владельцу набора.")
    return role
