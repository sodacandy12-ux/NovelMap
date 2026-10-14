import json
from fastapi import APIRouter, HTTPException
from app import graphdb, jobs, llm
from app.config import RESULTS
from app.rag import retriever, store
from app.schemas import AskAnswer, AskRequest
from app.service import load_result

router = APIRouter()


def _cache_file(nid):
    return RESULTS / f"{nid}.qa.json"


def _load_cache(nid):
    f = _cache_file(nid)
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def _graph_context(nid, res, q, have):
    """GraphRAG: 질문에 이름이 나온 인물의 1~2단계 이웃 관계(Neo4j)와 그 근거 장면을 벡터 검색 결과에 더한다.
    Neo4j가 연결되지 않았거나 질의가 실패하면 빈 값을 돌려 벡터 검색만 쓴다."""
    low = q.lower()
    named = [c.id for c in res.characters if any(len(n) >= 2 and n.lower() in low for n in [c.name, *c.aliases, c.name.split()[-1]])]
    if not named or not graphdb.available():
        return "", []
    try:
        rels = graphdb.neighbors(nid, named[:3], hops=2)
    except Exception:
        return "", []
    if not rels:
        return "", []
    lines = [f"- {r['source_name']} —[{r['type']}{'/' + r['label'] if r.get('label') else ''}]— {r['target_name']}" for r in rels[:20]]
    scene_ids = []
    for r in rels:
        for s in (r.get("evidence") or [])[:1]:
            if s not in have and s not in scene_ids:
                scene_ids.append(s)
    scenes = {s.scene_id: s for s in store.load_scenes(nid)}
    extra = [{"scene_id": s, "text": scenes[s].text} for s in scene_ids[:2] if s in scenes]
    return "관계 그래프(인물 이웃):\n" + "\n".join(lines) + "\n\n", extra


@router.post("/novels/{nid}/ask")
def ask(nid: str, req: AskRequest):
    q = req.question.strip()
    if not q:
        raise HTTPException(400, "질문이 비어 있습니다")
    try:
        res = load_result(nid)
    except FileNotFoundError:
        raise HTTPException(404, "분석 결과가 없습니다")
    cache = _load_cache(nid)
    if q in cache:      # 캐시된 답변은 API 키 없이도 돌려준다
        return cache[q]
    if not llm.is_configured():
        raise HTTPException(400, "API 키가 설정되지 않았습니다.")
    ix = jobs.index.get(nid, {})
    if ix.get("state") == "running":
        raise HTTPException(409, f"질문용 임베딩 인덱싱 중입니다 ({ix.get('done', 0)}/{ix.get('total', 0)}). 잠시 후 다시 시도하세요.")
    hits = retriever.retrieve(nid, q, k=4)
    graph_ctx, extra = _graph_context(nid, res, q, {h["scene_id"] for h in hits})
    hits = hits + extra
    ctx = "\n\n".join(f"[{h['scene_id']}]\n{h['text']}" for h in hits)
    try:
        out = llm.generate_json(f"질문: {q}\n\n{graph_ctx}장면:\n{ctx}", AskAnswer, system=llm.prompt("ask"), effort="medium", max_tokens=800)
    except llm.LlmError as e:
        raise HTTPException(502, str(e))
    ids = {h["scene_id"] for h in hits}
    out["citations"] = [c for c in out["citations"] if c in ids]
    cites = set(out["citations"])
    text = (q + " " + out["answer"]).lower()
    out["highlight"] = {
        "characters": [c.id for c in res.characters
                       if cites & set(c.scene_ids) and any(n.lower() in text for n in [c.name.split()[-1], c.name, *c.aliases])],
        "events": [e.id for e in res.events if e.scene_id in cites],
        "lore": [e.id for e in res.lore_entries
                 if cites & set(e.scene_ids) and any(n.lower() in text for n in [e.name, *e.aliases])],
    }
    cache[q] = out
    _cache_file(nid).write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return out
