"""장면별 추출 결과 캐시(jsonl). 호출 한도로 중단돼도 이어서 실행하고, 정규화만 다시 돌릴 때 LLM 호출을 아낀다.
키: (scene_id, 모델, 장면 본문 해시). API 키는 저장하지 않는다."""
import hashlib
import json
import threading

from app.config import PROMPTS, RESULTS

_lock = threading.Lock()


def _file(novel_id: str):
    return RESULTS / f"{novel_id}.extractions.jsonl"


def text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def prompt_version() -> str:
    """추출 프롬프트가 바뀌면 이전 캐시를 쓰지 않도록 프롬프트 해시를 키에 포함한다."""
    return hashlib.sha1((PROMPTS / "extract.txt").read_bytes()).hexdigest()[:8]


def load(novel_id: str, model: str, scenes, ignore_version: bool = False) -> dict:
    f = _file(novel_id)
    if not f.exists():
        return {}
    want = {s.scene_id: text_hash(s.text) for s in scenes}
    out = {}
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("model") == model and want.get(d["scene_id"]) == d.get("hash") and (ignore_version or d.get("ver") == prompt_version()):
            out[d["scene_id"]] = d["extraction"]
    return out


def append(novel_id: str, model: str, scene_id: str, text: str, extraction: dict):
    rec = json.dumps({"scene_id": scene_id, "model": model, "hash": text_hash(text), "ver": prompt_version(), "extraction": extraction},
                     ensure_ascii=False)
    with _lock, _file(novel_id).open("a", encoding="utf-8") as fh:
        fh.write(rec + "\n")


def clear(novel_id: str):
    f = _file(novel_id)
    if f.exists():
        f.unlink()
