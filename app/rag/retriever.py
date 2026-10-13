from app.rag import store


def retrieve(novel_id: str, question: str, k: int = 4, expand: bool = True) -> list[dict]:
    """상위 k개 장면 + 인접 장면 확장(prev/next 메타데이터 활용)."""
    hits = store.search(novel_id, question, k)
    if not expand:
        return hits
    scenes = {s.scene_id: s for s in store.load_scenes(novel_id)}
    seen = {h["scene_id"] for h in hits}
    extra = []
    for h in hits[:2]:
        s = scenes[h["scene_id"]]
        for nid in (s.prev_id, s.next_id):
            if nid and nid not in seen:
                seen.add(nid)
                extra.append({"scene_id": nid, "distance": None, "text": scenes[nid].text})
    return hits + extra
