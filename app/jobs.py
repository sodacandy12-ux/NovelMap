import time
from typing import Dict, List

events: Dict[str, List[dict]] = {}
state: Dict[str, str] = {}      # novel_id -> running | done | error | cancelled
times: Dict[str, dict] = {}     # novel_id -> {"started": ts, "finished": ts}
index: Dict[str, dict] = {}     # novel_id -> {"state": running|done|error, "done": n, "total": n}
_cancel: set = set()

ORDER = ["load_scenes", "extract", "normalize", "lore", "validate", "save"]


class Cancelled(Exception):
    pass


def start(nid: str, model: str = ""):
    events[nid] = []
    state[nid] = "running"
    times[nid] = {"started": time.time(), "finished": None, "model": model}
    _cancel.discard(nid)


def finish(nid: str, st: str):
    state[nid] = st
    times[nid]["finished"] = time.time()


def request_cancel(nid: str) -> bool:
    if state.get(nid) == "running":
        _cancel.add(nid)
        return True
    return False


def check_cancel(nid: str):
    if nid in _cancel:
        raise Cancelled("사용자가 분석을 중지했습니다.")


def status(nid: str) -> dict:
    """현재 분석 상태: 상태, 경과 시간, 단계별 진행, 재시도 회차, 실패 장면 수, 마지막 오류.
    이벤트는 각 노드가 '끝났을 때' 발생하므로, 끝나지 않은 첫 단계를 진행 중으로 본다.
    검증 실패로 재시도가 시작되면(retrying 이벤트) 그 뒤의 이벤트만 현재 회차로 본다."""
    st = state.get(nid, "idle")
    t = times.get(nid) or {}
    evs = events.get(nid, [])
    rounds = sum(1 for e in evs if e.get("retrying"))
    last_retry = max([i for i, e in enumerate(evs) if e.get("retrying")], default=-1)
    seg = evs[last_retry + 1:]
    all_last = {}
    for e in evs:
        all_last[e["node"]] = e
    last = {e["node"]: e for e in seg}
    seen = {e["node"] for e in seg}

    def complete(n):
        if st == "done":
            return True
        if n == "load_scenes":
            return "load_scenes" in all_last
        if any(x in seen for x in ORDER[ORDER.index(n) + 1:]):
            return True            # 뒤 단계가 시작됐다는 건 이 단계가 끝났다는 뜻
        e = last.get(n)
        return bool(e) and n != "extract" and e["done"] >= e["total"]

    err = next((e["message"] for e in reversed(evs) if e["node"] == "error"), None)
    steps, marked = [], False
    for n in ORDER:
        e = all_last.get(n)
        if complete(n):
            s = "done"
        elif st == "running" and not marked:
            s, marked = "running", True
        elif st == "error" and not marked:
            s, marked = "error", True
        else:
            s = "wait"
        label = (e or {}).get("label")
        if n == "extract" and s == "running" and rounds:
            label = f"추출 재시도 {rounds}/2회차"
        steps.append({"node": n, "label": label, "state": s, "done": (e or {}).get("done", 0),
                      "total": (e or {}).get("total", 0), "failed": (e or {}).get("failed", 0)})
    end = t.get("finished") or time.time()
    return {"index": index.get(nid, {"state": "idle"}), "state": st,
            "elapsed": round(end - t["started"], 1) if t.get("started") else None,
            "steps": steps, "rounds": rounds, "error": err, "model": t.get("model", "")}
