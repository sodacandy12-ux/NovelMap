from fastapi import APIRouter, HTTPException
from app.rag import store
from app.service import load_result

router = APIRouter()


def _res(nid):
    try:
        return load_result(nid)
    except FileNotFoundError:
        raise HTTPException(404, "분석 결과가 없습니다. 먼저 분석을 실행하세요")


@router.get("/novels/{nid}/graph")
def graph(nid: str, top: int = 0):
    r = _res(nid)
    chars = r.characters[:top] if top else r.characters
    ids = {c.id for c in chars}
    return {"provider": r.provider, "model": r.model,
            "nodes": [{"id": c.id, "name": c.name, "aliases": c.aliases, "scene_count": c.scene_count,
                       "first_scene": c.first_scene, "scene_ids": c.scene_ids} for c in chars],
            "links": [{"source": x.source, "target": x.target, "type": x.type, "label": x.label, "directed": x.directed,
                       "since_chapter": x.since_chapter, "evidence": x.evidence_scene_ids, "quotes": x.evidence_quotes}
                      for x in r.relations if x.source in ids and x.target in ids]}


@router.get("/novels/{nid}/timeline")
def timeline(nid: str):
    r = _res(nid)
    scenes = {s.scene_id: s for s in store.load_scenes(nid)}
    names = {c.id: c.name for c in r.characters}
    evs = sorted(r.events, key=lambda e: scenes[e.scene_id].order)
    return {"characters": [{"id": c.id, "name": c.name} for c in r.characters],
            "events": [{**e.model_dump(), "character_names": [names[c] for c in e.characters if c in names]} for e in evs]}


@router.get("/novels/{nid}/scenes/{scene_id}")
def scene(nid: str, scene_id: str):
    try:
        scenes = {s.scene_id: s for s in store.load_scenes(nid)}
    except FileNotFoundError:
        raise HTTPException(404, "novel not found")
    s = scenes.get(scene_id)
    if not s:
        raise HTTPException(404, "scene not found")
    return {"scene": s.model_dump(),
            "prev": scenes[s.prev_id].model_dump() if s.prev_id else None,
            "next": scenes[s.next_id].model_dump() if s.next_id else None}
