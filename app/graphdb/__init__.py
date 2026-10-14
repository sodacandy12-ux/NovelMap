"""Neo4j 연결·적재·질의. 선택 모드: 연결되지 않아도 JSON 캐시만으로 지도·로어북·타임라인은 동작한다.
접속 정보(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)는 .env 환경 변수로만 받고 로그에 남기지 않는다."""
import json
import os
import time

from app.graphdb import ontology, queries

_driver = None
_status = {"at": 0.0, "ok": False, "why": ""}
_TTL = 10.0


def _cfg():
    return os.getenv("NEO4J_URI", ""), os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "")


def _get_driver():
    global _driver
    if _driver is None:
        uri, user, pw = _cfg()
        from neo4j import GraphDatabase          # 미설치여도 앱이 뜨도록 지연 import
        _driver = GraphDatabase.driver(uri, auth=(user, pw))
    return _driver


def status(force: bool = False) -> dict:
    """{'ok': bool, 'why': 사유}. 연결 확인 결과는 몇 초간 캐시한다."""
    now = time.time()
    if not force and now - _status["at"] < _TTL:
        return {"ok": _status["ok"], "why": _status["why"]}
    ok, why = False, ""
    if not _cfg()[0]:
        why = "NEO4J_URI가 설정되지 않았습니다"
    else:
        try:
            _get_driver().verify_connectivity()
            ok = True
        except ImportError:
            why = "neo4j 파이썬 드라이버가 설치되지 않았습니다 (pip install neo4j)"
        except Exception as e:                    # 접속 정보가 담길 수 있는 메시지는 클래스 이름만 남긴다
            why = f"Neo4j에 연결할 수 없습니다 ({type(e).__name__})"
    _status.update(at=now, ok=ok, why=why)
    return {"ok": ok, "why": why}


def available() -> bool:
    return status()["ok"]


def build_payload(res, scenes) -> dict:
    """AnalysisResult -> 적재용 행 목록(순수 함수, DB 없이 테스트 가능)."""
    order = {s.scene_id: s.order for s in scenes}
    lore_by_char = {e.character_id: e for e in res.lore_entries if e.character_id}
    cls = ontology.class_of_category
    nodes = {c: [] for c in ontology.classes()}
    scene_rows = {}

    def scene(sid):
        if sid in order:
            scene_rows[sid] = {"id": sid, "props": {"scene_id": sid, "order": order[sid]}}
        return sid in order

    for ch in res.characters:
        lore = lore_by_char.get(ch.id)
        nodes["Character"].append({"id": ch.id, "props": {
            "name": ch.name, "aliases": ch.aliases, "first_scene": ch.first_scene,
            "scene_count": ch.scene_count,
            "attributes": json.dumps([a.model_dump() for a in lore.attributes], ensure_ascii=False) if lore else "[]"}})
    mentions = {c: [] for c in ontology.classes()}
    for ch in res.characters:
        mentions["Character"] += [{"source": ch.id, "target": s} for s in ch.scene_ids if scene(s)]
    for e in res.lore_entries:
        label = cls(e.category)
        if label == "Character" or label not in nodes:
            continue                                   # 인물 카드는 Character 노드(속성 포함)가 대신한다
        nodes[label].append({"id": e.id, "props": {
            "name": e.name, "aliases": e.aliases, "summary": e.summary, "scene_count": e.scene_count,
            "attributes": json.dumps([a.model_dump() for a in e.attributes], ensure_ascii=False)}})
        mentions[label] += [{"source": e.id, "target": s} for s in e.scene_ids if scene(s)]

    place_ids = {n["id"] for n in nodes["Place"]}
    char_ids = {n["id"] for n in nodes["Character"]}
    rels = {k: [] for k in ontology.load()["relations"]}
    for r in res.relations:
        code = ontology.character_relations().get(r.type)
        if code and r.source in char_ids and r.target in char_ids:
            rels[code].append({"source": r.source, "target": r.target, "key": f"{r.source}|{r.target}|{r.label}",
                               "props": {"label": r.label, "directed": r.directed, "since_scene": r.since_scene or (r.evidence_scene_ids or [""])[0],
                                         "evidence_scene_ids": r.evidence_scene_ids,
                                         "evidence_quotes": json.dumps(r.evidence_quotes, ensure_ascii=False)}})
    from app.timeline import place_index, resolve_place
    idx = place_index(res)
    for ev in res.events:
        nodes["Event"].append({"id": ev.id, "props": {"summary": ev.summary}})
        for c in ev.characters:
            if c in char_ids:
                rels["PARTICIPATED_IN"].append({"source": c, "target": ev.id, "key": f"{c}|{ev.id}", "props": {}})
        pid = resolve_place(res, idx, ev)
        if pid in place_ids:
            rels["OCCURRED_AT"].append({"source": ev.id, "target": pid, "key": f"{ev.id}|{pid}", "props": {}})
        if scene(ev.scene_id):
            rels["IN_SCENE"].append({"source": ev.id, "target": ev.scene_id, "key": f"{ev.id}|{ev.scene_id}", "props": {}})
    nodes["Scene"] = list(scene_rows.values())
    for label, rows in mentions.items():
        for m in rows:
            rels.setdefault("MENTIONED_IN", []).append({**m, "label": label, "key": f"{m['source']}|{m['target']}", "props": {}})
    return {"nodes": nodes, "rels": rels}


def _write(tx, nid, payload):
    tx.run(queries.DELETE_NOVEL, nid=nid)                      # 재분석 때 사라진 항목이 남지 않게 먼저 비운다
    for label, rows in payload["nodes"].items():               # 라벨은 ontology.yaml 화이트리스트에서만 온다
        if rows:
            tx.run(f"UNWIND $rows AS row MERGE (n:{label} {{novel_id: $nid, id: row.id}}) SET n += row.props", rows=rows, nid=nid)
    spec = ontology.load()["relations"]
    for rtype, rows in payload["rels"].items():
        if not rows:
            continue
        if rtype == "MENTIONED_IN":
            for label in {r["label"] for r in rows}:
                part = [r for r in rows if r["label"] == label]
                tx.run(f"UNWIND $rows AS row MATCH (a:{label} {{novel_id: $nid, id: row.source}}) "
                       "MATCH (b:Scene {novel_id: $nid, id: row.target}) "
                       "MERGE (a)-[r:MENTIONED_IN {key: row.key}]->(b)", rows=part, nid=nid)
            continue
        src, dst = spec[rtype]["from"], spec[rtype]["to"]
        tx.run(f"UNWIND $rows AS row MATCH (a:{src} {{novel_id: $nid, id: row.source}}) MATCH (b:{dst} {{novel_id: $nid, id: row.target}}) "
               f"MERGE (a)-[r:{rtype} {{key: row.key}}]->(b) SET r += row.props", rows=rows, nid=nid)


def sync(res, scenes) -> str:
    """결과 저장 노드가 호출. 연결돼 있으면 같은 데이터를 그래프로 적재한다. 반환: 'loaded' 또는 'skipped: 사유'."""
    st = status(force=True)
    if not st["ok"]:
        return "skipped: " + st["why"]
    try:
        payload = build_payload(res, scenes)
        with _get_driver().session() as s:
            s.execute_write(_write, res.novel_id, payload)
        return "loaded"
    except Exception as e:
        return f"skipped: 적재 실패 ({type(e).__name__})"


def shortest_path(nid: str, source_id: str, target_id: str) -> dict | None:
    """없으면 None. 있으면 {'nodes': [{id,name}], 'links': [{source,target,type,label}]} (경로 순서)."""
    with _get_driver().session() as s:
        rec = s.run(queries.shortest_path(), nid=nid, source_id=source_id, target_id=target_id).single()
    if not rec:
        return None
    p = rec["p"]
    codes = {v: k for k, v in ontology.character_relations().items()}
    return {"nodes": [{"id": n["id"], "name": n.get("name", n["id"])} for n in p.nodes],
            "links": [{"source": r.start_node["id"], "target": r.end_node["id"], "type": codes.get(r.type, r.type),
                       "label": r.get("label", "")} for r in p.relationships]}


def neighbors(nid: str, ids: list[str], hops: int = 2) -> list[dict]:
    codes = {v: k for k, v in ontology.character_relations().items()}
    with _get_driver().session() as s:
        rows = [dict(r) for r in s.run(queries.neighbors(hops), nid=nid, ids=ids)]
    for r in rows:
        r["type"] = codes.get(r["type"], r["type"])
    return rows
