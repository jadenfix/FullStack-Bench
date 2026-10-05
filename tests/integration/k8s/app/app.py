import os

from fastapi import FastAPI

app = FastAPI()
VERSION = os.environ.get("VERSION", "v1")


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/")
def root():
    return {"version": VERSION, "pod": os.environ.get("HOSTNAME")}
