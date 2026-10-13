"""인물 수동 보정(병합·제외). data/results/{novel_id}.overrides.json 에 저장되고 다음 정규화 때 최우선으로 적용된다."""
import json

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.config import RESULTS

router = APIRouter()


class OverrideRequest(BaseModel):
    merge: dict[str, str] = Field(default_factory=dict)   # {합칠 이름: 대표 이름}
    drop: list[str] = Field(default_factory=list)         # 인물에서 제외할 이름


def _read(nid: str) -> dict:
    f = RESULTS / f"{nid}.overrides.json"
    if f.exists():
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            return {"merge": d.get("merge", {}), "drop": d.get("drop", [])}
        except ValueError:
            pass
    return {"merge": {}, "drop": []}


@router.get("/novels/{nid}/overrides")
def get_overrides(nid: str):
    return _read(nid)


@router.post("/novels/{nid}/overrides")
def add_overrides(nid: str, req: OverrideRequest):
    d = _read(nid)
    for k, v in req.merge.items():
        if k.strip() and v.strip() and k.strip() != v.strip():
            d["merge"][k.strip()] = v.strip()
    for n in req.drop:
        if n.strip() and n.strip() not in d["drop"]:
            d["drop"].append(n.strip())
    (RESULTS / f"{nid}.overrides.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return d


@router.delete("/novels/{nid}/overrides")
def clear_overrides(nid: str):
    (RESULTS / f"{nid}.overrides.json").unlink(missing_ok=True)
    return {"merge": {}, "drop": []}
