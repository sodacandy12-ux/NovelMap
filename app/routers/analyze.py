import asyncio
import json
import threading
from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse
from app import jobs, llm
from app.config import RESULTS
from app.routers.novels import prepare_novel
from app.service import run_analysis

router = APIRouter()


def _work(nid: str, fresh: bool):
    try:
        run_analysis(nid, lambda e: jobs.events[nid].append(e), fresh)
        jobs.finish(nid, "done")
    except jobs.Cancelled:
        jobs.finish(nid, "cancelled")
    except Exception as e:  # noqa
        jobs.events[nid].append({"node": "error", "label": "오류", "message": llm.scrub(str(e), llm.current()["api_key"])})
        jobs.finish(nid, "error")


@router.post("/novels/{nid}/analyze")
def analyze(nid: str, fresh: bool = False):
    prepare_novel(nid)   # 장면 파일이 없으면 만든다(임베딩 인덱싱은 백그라운드, 분석과 무관)
    if not llm.is_configured():
        raise HTTPException(400, "API 키가 설정되지 않았습니다. 상단 설정 바에서 공급사·모델·키를 저장하세요.")
    if jobs.state.get(nid) == "running":
        return {"job_id": nid, "status": "running"}
    c = llm.current()
    jobs.start(nid, f"{c['provider']}/{c['model']}")   # 이 분석이 실제로 쓰는 공급사/모델을 기록
    threading.Thread(target=_work, args=(nid, fresh), daemon=True).start()
    return {"job_id": nid, "status": "running"}


@router.post("/novels/{nid}/analyze/cancel")
def analyze_cancel(nid: str):
    """진행 중인 분석을 중지한다(이미 끝난 장면은 캐시에 남는다). 진행 중이던 LLM 호출 1~2개는 끝난 뒤에 멈춘다."""
    return {"cancelled": jobs.request_cancel(nid)}


@router.get("/novels/{nid}/analyze/status")
def analyze_status(nid: str):
    return jobs.status(nid)


@router.get("/novels/{nid}/analyze/stream")
async def stream(nid: str):
    async def gen():
        i = 0
        while True:
            evs = jobs.events.get(nid, [])
            while i < len(evs):
                yield {"event": "progress", "data": json.dumps(evs[i], ensure_ascii=False)}
                i += 1
            st = jobs.state.get(nid)
            if st is None:
                yield {"event": "end", "data": json.dumps({"status": "idle"})}
                return
            if st in ("done", "error", "cancelled") and i >= len(jobs.events.get(nid, [])):
                yield {"event": "end", "data": json.dumps({"status": st})}
                return
            await asyncio.sleep(0.2)
    return EventSourceResponse(gen())
