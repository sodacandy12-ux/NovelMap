import json
from fastapi import APIRouter, HTTPException
from app import jobs, llm
from app.config import RESULTS
from app.rag import retriever
from app.schemas import AskAnswer, AskRequest
from app.service import load_result

router = APIRouter()


def _cache_file(nid):
    return RESULTS / f"{nid}.qa.json"


def _load_cache(nid):
    f = _cache_file(nid)
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


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
    ctx = "\n\n".join(f"[{h['scene_id']}]\n{h['text']}" for h in hits)
    try:
        out = llm.generate_json(f"질문: {q}\n\n장면:\n{ctx}", AskAnswer, system=llm.prompt("ask"), effort="medium", max_tokens=800)
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
