import hashlib
import re
import threading

from fastapi import APIRouter, File, HTTPException, UploadFile

from app import jobs
from app.config import NOVELS, RESULTS
from app.rag import store
from app.rag.splitter import split_scenes

router = APIRouter()


def _slug(filename: str) -> str:
    stem = (filename or "novel").rsplit(".", 1)[0]
    s = re.sub(r"[^0-9A-Za-z가-힣]+", "_", stem).strip("_")
    return s[:60] or "novel_" + hashlib.md5(stem.encode()).hexdigest()[:6]


def start_indexing(nid: str, scenes):
    """임베딩 인덱싱은 오래 걸리므로 백그라운드로 돌린다(요청·분석을 막지 않는다). 질의응답에만 필요하다."""
    if jobs.index.get(nid, {}).get("state") == "running":
        return
    jobs.index[nid] = {"state": "running", "done": 0, "total": len(scenes)}

    def work():
        try:
            store.index_scenes(nid, scenes, lambda d, t: jobs.index[nid].update(done=d, total=t))
            jobs.index[nid]["state"] = "done"
        except Exception as e:  # noqa
            jobs.index[nid] = {"state": "error", "done": 0, "total": len(scenes), "message": f"{type(e).__name__}: {str(e)[:150]}"}

    threading.Thread(target=work, daemon=True).start()


def prepare_novel(nid: str):
    """txt는 있는데 장면 파일이 없으면(수동으로 넣은 원고 등) 지금 분할해서 만든다."""
    f = RESULTS / f"{nid}.scenes.json"
    if f.exists():
        return
    txt = NOVELS / f"{nid}.txt"
    if not txt.exists():
        raise HTTPException(404, "원고를 찾을 수 없습니다")
    scenes = split_scenes(txt.read_text(encoding="utf-8", errors="ignore"))
    if not scenes:
        raise HTTPException(400, "장면을 찾지 못했습니다")
    store.save_scenes(nid, scenes)
    start_indexing(nid, scenes)


@router.get("/novels")
def list_novels():
    return [{"novel_id": f.stem, "analyzed": (RESULTS / f"{f.stem}.json").exists(), "state": jobs.state.get(f.stem, "idle"),
             "index": jobs.index.get(f.stem, {}).get("state", "idle")} for f in sorted(NOVELS.glob("*.txt"))]


@router.post("/novels")
def upload(file: UploadFile = File(...)):
    """동기 함수라 스레드풀에서 실행된다(서버 전체를 막지 않음). 장면 분할·저장은 즉시, 임베딩은 백그라운드."""
    raw = file.file.read()
    if len(raw) > 2_000_000:
        raise HTTPException(413, "원고가 너무 큽니다(약 10만 단어 이하)")
    text = raw.decode("utf-8", errors="ignore")
    nid = _slug(file.filename)
    scenes = split_scenes(text)
    if not scenes:
        raise HTTPException(400, "장면을 찾지 못했습니다")
    (NOVELS / f"{nid}.txt").write_text(text, encoding="utf-8")
    store.save_scenes(nid, scenes)
    for stale in (RESULTS / f"{nid}.json", RESULTS / f"{nid}.qa.json"):   # 같은 이름으로 다시 올리면 이전 결과는 무효
        stale.unlink(missing_ok=True)
    start_indexing(nid, scenes)
    return {"novel_id": nid, "scenes": len(scenes), "chapters": len({s.chapter for s in scenes}), "indexing": True}
