from fastapi import APIRouter
from pydantic import BaseModel

from app import llm
from app.schemas import LlmConfigRequest

router = APIRouter()


class _Pong(BaseModel):
    ok: bool


@router.get("/llm/providers")
def providers():
    """공급사·모델 목록(설정 파일 기반). 키 값은 절대 돌려주지 않고 설정 여부만 알린다."""
    return llm.providers_info()


@router.put("/llm/config")
def put_config(req: LlmConfigRequest):
    """공급사·모델·키를 서버 메모리에 저장하고 연결 테스트를 1회 한다. 키는 요청 본문으로만 받는다."""
    p = llm.provider_def(req.provider)
    if not p or not p.get("ready") or not p.get("models"):
        return {"ok": False, "error": "아직 지원하지 않는 공급사입니다(준비 중).", "configured": llm.is_configured()}
    if req.model not in [m["id"] for m in p["models"]]:
        return {"ok": False, "error": "목록에 없는 모델입니다.", "configured": llm.is_configured()}
    prev = llm.current()
    llm.set_config(req.provider, req.model, (req.api_key or "").strip() or None)
    if not llm.is_configured():
        return {"ok": False, "error": "API 키를 입력하세요.", "configured": False}
    try:
        llm.generate_json('{"ok": true} 형태의 JSON만 답하세요.', _Pong, effort="low", max_tokens=50)
    except llm.LlmError as e:
        if req.api_key:   # 새로 넣은 키가 실패하면 보관하지 않는다
            llm.clear_key()
            llm.set_config(prev["provider"], prev["model"], None)
        return {"ok": False, "error": str(e), "configured": llm.is_configured()}
    return {"ok": True, "configured": True, "provider": req.provider, "model": req.model}
