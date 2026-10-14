"""타임라인 장소 레인: 레인 = 로어북 장소 항목, 사건 수 상위 8곳을 처음 등장한 순서로 놓고 나머지·미상은 기타·미상 레인."""
import re
from collections import Counter

MAX_LANES = 8
ETC_ID, ETC_NAME = "etc", "기타·미상"


def _key(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def place_index(res) -> dict:
    """장소 이름·별칭(정규화) -> 로어북 장소 id"""
    idx = {}
    for e in res.lore_entries:
        if e.category == "장소":
            for n in (e.name, *e.aliases):
                idx.setdefault(_key(n), e.id)
    return idx


def resolve_place(res, idx: dict, ev) -> str | None:
    pid = getattr(ev, "location_id", None)
    if pid and any(e.id == pid for e in res.lore_entries):
        return pid
    if not ev.location:
        return None
    k = _key(ev.location)
    if k in idx:
        return idx[k]
    base = _key(re.sub(r"[\(（][^\)）]*[\)）]", " ", ev.location))        # '서재 (저택 안)' -> '서재'
    if base in idx:
        return idx[base]
    # 사건 장소가 장소 항목 이름을 포함할 때('베이커가 221B의 거실' ⊃ '베이커가')만 인정: 긴 이름 우선
    hits = [(len(n), pid) for n, pid in idx.items() if len(n) >= (3 if n.isascii() else 2) and n in k]
    return max(hits)[1] if hits else None


def build_lanes(res, scene_order: dict) -> tuple[list, list]:
    """반환: (레인 목록, 장면 순서대로 정렬된 사건 목록[lane_id·lane_name·order 포함])"""
    idx = place_index(res)
    names = {e.id: e.name for e in res.lore_entries if e.category == "장소"}
    evs = sorted(res.events, key=lambda e: scene_order.get(e.scene_id, 10 ** 9))
    place_of = {e.id: resolve_place(res, idx, e) for e in evs}
    count = Counter(p for p in place_of.values() if p)
    first = {}
    for e in evs:
        p = place_of[e.id]
        if p and p not in first:
            first[p] = len(first)
    top = sorted(count, key=lambda p: (-count[p], first[p]))[:MAX_LANES]
    lane_ids = sorted(top, key=lambda p: first[p])          # 처음 등장한 순서로 배치
    lanes = [{"id": p, "name": names[p], "count": count[p]} for p in lane_ids]
    out = []
    n_etc = 0
    for e in evs:
        p = place_of[e.id]
        lane = p if p in lane_ids else ETC_ID
        n_etc += lane == ETC_ID
        out.append({**e.model_dump(), "order": scene_order.get(e.scene_id, 0), "lane_id": lane,
                    "lane_name": names.get(lane, ETC_NAME)})
    if n_etc:
        lanes.append({"id": ETC_ID, "name": ETC_NAME, "count": n_etc})
    return lanes, out
