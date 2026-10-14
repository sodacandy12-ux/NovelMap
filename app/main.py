import os

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from app import graphdb, llm
from app.config import WEB
from app.routers import analyze, ask, llm_config, lore, novels, overrides, views

app = FastAPI(title="소설 지도 (Novel Map)")
for r in (novels.router, analyze.router, views.router, lore.router, ask.router, llm_config.router, overrides.router):
    app.include_router(r)


@app.get("/info")
def info():
    c = llm.current()
    return {"provider": c["provider"], "model": c["model"], "configured": bool(c["api_key"]), "embedding": os.getenv("EMBEDDING") or "bge-m3",
            "neo4j": graphdb.status()}


@app.get("/")
def root():
    return RedirectResponse("/web/index.html")


app.mount("/web", StaticFiles(directory=WEB, html=True), name="web")
