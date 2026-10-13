"""분석 실행(진행 이벤트 포함)과 결과 캐시 조회."""
import json
from typing import Callable, Optional

from app import jobs, llm
from app.config import RESULTS
from app.graph.build import NODE_LABELS, build_graph
from app.schemas import AnalysisResult


def run_analysis(novel_id: str, emit: Optional[Callable[[dict], None]] = None, fresh: bool = False) -> None:
    if not llm.is_configured():
        raise llm.LlmError("API 키가 설정되지 않았습니다.")
    emit = emit or (lambda e: None)
    graph = build_graph()
    total = 0
    ok, failed = set(), set()
    cfg = {"max_concurrency": llm.concurrency(), "recursion_limit": 1000}
    for chunk in graph.stream({"novel_id": novel_id, "fresh": fresh}, cfg, stream_mode="updates"):
        jobs.check_cancel(novel_id)
        for node, upd in chunk.items():
            if node == "load_scenes":
                total = len(upd["scenes"])
                ok = {sid for sid, v in upd["scene_errors"].items() if v == ""}   # 캐시로 이미 끝난 장면
                emit({"node": node, "label": NODE_LABELS[node], "total": total, "done": total})
            elif node == "extract":
                for sid, msg in upd.get("scene_errors", {}).items():
                    if msg:
                        failed.add(sid)
                        ok.discard(sid)
                    else:
                        ok.add(sid)
                        failed.discard(sid)
                emit({"node": node, "label": NODE_LABELS[node], "total": total, "done": len(ok), "failed": len(failed)})
            elif node == "validate":
                retrying = any(e.startswith("scene:") for e in upd["errors"]) and upd["retry_count"] <= 2
                emit({"node": node, "label": NODE_LABELS[node], "done": 1, "total": 1,
                      "retrying": retrying, "errors": len(upd["errors"])})
            else:
                emit({"node": node, "label": NODE_LABELS.get(node, node), "done": 1, "total": 1})


def load_result(novel_id: str) -> AnalysisResult:
    f = RESULTS / f"{novel_id}.json"
    if not f.exists():
        raise FileNotFoundError(novel_id)
    return AnalysisResult(**json.loads(f.read_text(encoding="utf-8")))
