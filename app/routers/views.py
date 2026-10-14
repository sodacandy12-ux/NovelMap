from fastapi import APIRouter, HTTPException, Query
from app import graphdb
from app.rag import store
from app.service import load_result
from app.timeline import build_lanes

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
                       "since_chapter": x.since_chapter, "since_scene": x.since_scene or (x.evidence_scene_ids or [""])[0],
                       "evidence": x.evidence_scene_ids, "quotes": x.evidence_quotes}
                      for x in r.relations if x.source in ids and x.target in ids]}


@router.get("/novels/{nid}/graph/path")
def graph_path(nid: str, source: str = Query(..., alias="from"), target: str = Query(..., alias="to")):
    """두 인물 사이의 최단 관계 경로(Neo4j 필요, LLM 호출 없음)."""
    r = _res(nid)
    ids = {c.id for c in r.characters}
    if source not in ids or target not in ids:
        raise HTTPException(404, "알 수 없는 인물입니다")
    if source == target:
        raise HTTPException(400, "서로 다른 두 인물을 고르세요")
    st = graphdb.status()
    if not st["ok"]:
        raise HTTPException(503, "관계 경로 찾기를 쓸 수 없습니다: " + st["why"])
    try:
        path = graphdb.shortest_path(nid, source, target)
    except Exception as e:
        raise HTTPException(503, f"Neo4j 질의에 실패했습니다 ({type(e).__name__}). 분석을 다시 실행해 그래프를 적재했는지 확인하세요")
    return {"found": path is not None, **(path or {"nodes": [], "links": []})}


@router.get("/novels/{nid}/timeline")
def timeline(nid: str):
    r = _res(nid)
    scenes = {s.scene_id: s for s in store.load_scenes(nid)}
    names = {c.id: c.name for c in r.characters}
    lanes, evs = build_lanes(r, {sid: s.order for sid, s in scenes.items()})
    return {"characters": [{"id": c.id, "name": c.name} for c in r.characters], "lanes": lanes,
            "events": [{**e, "character_names": [names[c] for c in e["characters"] if c in names]} for e in evs]}


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
