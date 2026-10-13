"""Chroma 저장소 + 임베딩(OpenAI 또는 로컬 해시 임베딩)."""
import hashlib
import json
import math
import os
import re
import chromadb
from app.config import CHROMA, RESULTS
from app.schemas import Scene

DIM = 512
_client = None


def _hash_embed(texts: list[str]) -> list[list[float]]:
    out = []
    for t in texts:
        v = [0.0] * DIM
        words = re.findall(r"[a-z가-힣']{2,}", t.lower())
        feats = words + [a + "_" + b for a, b in zip(words, words[1:])]
        for f in feats:
            h = int(hashlib.md5(f.encode()).hexdigest(), 16)
            v[h % DIM] += 1.0 if h >> 100 & 1 else -1.0
            v[(h >> 20) % DIM] += 0.5
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / n for x in v])
    return out


_st_model = None
_embed_mode = None


def _bge(texts: list[str]) -> list[list[float]]:
    """로컬 bge-m3 임베딩(공급사·API 키와 무관). 첫 실행 때 모델을 내려받는다(약 2GB)."""
    global _st_model
    if _st_model is None:
        from sentence_transformers import SentenceTransformer
        _st_model = SentenceTransformer("BAAI/bge-m3")
        _st_model.max_seq_length = 512   # 장면은 최대 ~700토큰이라 이 정도면 충분하고 CPU에서 훨씬 빠르다
    return _st_model.encode(texts, batch_size=16, normalize_embeddings=True, show_progress_bar=False).tolist()


def embed_mode() -> str:
    """EMBEDDING=hash 로 테스트용 해시 임베딩 강제. 기본은 bge-m3, 불러오기에 실패하면 해시로 대체."""
    global _embed_mode
    if _embed_mode is None:
        if os.getenv("EMBEDDING", "") == "hash":
            _embed_mode = "hash"
        else:
            try:
                _bge(["test"])
                _embed_mode = "bge-m3"
            except Exception as e:  # noqa
                print(f"[경고] bge-m3 로드 실패, 해시 임베딩으로 대체: {type(e).__name__}")
                _embed_mode = "hash"
    return _embed_mode


def embed(texts: list[str]) -> list[list[float]]:
    return _bge(texts) if embed_mode() == "bge-m3" else _hash_embed(texts)


def client():
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=str(CHROMA))
    return _client


def _cname(novel_id: str) -> str:
    """Chroma 컬렉션 이름은 영문·숫자만 허용하므로, 한글 등이 들어간 작품 ID는 해시로 바꾼다."""
    base = novel_id if re.fullmatch(r"[A-Za-z0-9_]{1,80}", novel_id) else "n" + hashlib.md5(novel_id.encode()).hexdigest()[:16]
    return f"novel_{base}_{embed_mode().replace('-', '')}"


def _col(novel_id: str):
    return client().get_or_create_collection(_cname(novel_id), metadata={"hnsw:space": "cosine"})


def save_scenes(novel_id: str, scenes: list[Scene]):
    """장면 JSON만 먼저 저장한다. 분석(LLM 추출)은 이것만 있으면 시작할 수 있다."""
    (RESULTS / f"{novel_id}.scenes.json").write_text(
        json.dumps([s.model_dump() for s in scenes], ensure_ascii=False), encoding="utf-8")


def index_scenes(novel_id: str, scenes: list[Scene], progress=None):
    """장면 임베딩 -> Chroma (질의응답 검색용). 느린 작업이라 배치 단위로 진행 상황을 알린다."""
    save_scenes(novel_id, scenes)
    try:
        client().delete_collection(_cname(novel_id))
    except Exception:
        pass
    col = _col(novel_id)
    step = 32
    for i in range(0, len(scenes), step):
        chunk = scenes[i:i + step]
        col.add(ids=[s.scene_id for s in chunk], documents=[s.text for s in chunk],
                embeddings=embed([s.text for s in chunk]),
                metadatas=[{"chapter": s.chapter, "order": s.order, "prev_id": s.prev_id or "", "next_id": s.next_id or ""} for s in chunk])
        if progress:
            progress(min(i + step, len(scenes)), len(scenes))


def load_scenes(novel_id: str) -> list[Scene]:
    f = RESULTS / f"{novel_id}.scenes.json"
    if not f.exists():
        raise FileNotFoundError(novel_id)
    return [Scene(**d) for d in json.loads(f.read_text(encoding="utf-8"))]


def search(novel_id: str, query: str, k: int = 5) -> list[dict]:
    col = _col(novel_id)
    if col.count() == 0:   # 임베딩 방식이 바뀌어 인덱스가 비었으면 저장된 장면으로 다시 만든다
        index_scenes(novel_id, load_scenes(novel_id))
        col = _col(novel_id)
    r = col.query(query_embeddings=embed([query]), n_results=k)
    return [{"scene_id": i, "distance": d, "text": t}
            for i, d, t in zip(r["ids"][0], r["distances"][0], r["documents"][0])]
