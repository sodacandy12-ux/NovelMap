"""API 키 없이 파이프라인·API·내보내기를 점검하는 스모크 테스트(LLM은 가짜 응답으로 대체).
실행: EMBEDDING=hash python scripts/smoke_test.py"""
import os
import re
import sys
from collections import Counter
from pathlib import Path

os.environ["EMBEDDING"] = "hash"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import llm  # noqa: E402
from app.config import NOVELS  # noqa: E402
from app.rag import store  # noqa: E402
from app.rag.splitter import split_scenes  # noqa: E402
from app.schemas import AskAnswer, LoreSummary, SceneExtraction  # noqa: E402

NID = "smoke"
text = (NOVELS / "baskervilles.txt").read_text(encoding="utf-8")
scenes = split_scenes(text)[:40]
all_text = " ".join(s.text for s in scenes)
FREQ = Counter(w for w in re.findall(r"\b[A-Z][a-z]{3,}\b", all_text))
NAMES = [w for w, c in FREQ.most_common(60) if c >= 6 and w not in ("This", "That", "There", "They", "What", "Then", "When", "Have", "With", "From")]
calls = Counter()


def fake_generate(prompt, schema, system="", effort="low", max_tokens=4000):
    calls[schema.__name__] += 1
    if schema is SceneExtraction:
        body = prompt.split("\n\n", 1)[1]
        sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", body)) if len(s) > 30]
        found = [n for n in NAMES if n in body][:4]
        ents = []
        for n in found:
            sent = next((s for s in sents if n in s), "")
            # 일부러 문장을 살짝 바꿔 쓴 인용을 섞어 '근거 문장 보정'이 동작하는지 본다
            q = sent.replace(",", "") if calls["SceneExtraction"] % 3 == 0 else sent
            ents.append({"name": n, "category": "인물", "aliases": [], "attributes": [{"key": "등장", "value": f"{n}이(가) 등장한다", "quote": q}]})
        if "Hall" in body:
            ents.append({"name": "Baskerville Hall", "category": "장소", "aliases": ["Hall"], "attributes": []})
        rels = [{"a": found[0], "b": found[1], "type": "협력·동료", "label": "동료", "directed": False,
                 "quote": next((s for s in sents if found[0] in s), sents[0])}] if len(found) > 1 and sents else []
        return SceneExtraction(**{"entities": ents, "relations": rels,
                                  "events": [{"summary": "한국어 요약: " + sents[0][:40], "characters": found, "location": "", "quote": sents[0]}] if sents else []}).model_dump()
    if schema is LoreSummary:
        return {"summary": "가짜 요약입니다."}
    if schema is AskAnswer:
        sid = re.findall(r"\[(ch\d+_s\d+)\]", prompt)
        return {"answer": "가짜 답변 Holmes", "citations": sid[:1]}
    if schema.__name__ == "_AliasResolution":
        return {"groups": [], "not_characters": []}
    return {"ok": True}


llm.generate_json = fake_generate
llm.current = lambda: {"provider": "anthropic", "model": "claude-haiku-5-5", "api_key": "test-key", "key_source": "env"}
llm.is_configured = lambda: True

from app import cache  # noqa: E402
from app.config import RESULTS  # noqa: E402
from app.service import load_result, run_analysis  # noqa: E402
from app.routers.lore import to_markdown  # noqa: E402

(RESULTS / f"{NID}.scenes.json").unlink(missing_ok=True)
cache.clear(NID)
store.index_scenes(NID, scenes)
events = []
run_analysis(NID, events.append)
r = load_result(NID)
cats = Counter(e.category for e in r.lore_entries)
print(f"추출 호출 {calls['SceneExtraction']}회, 요약 호출 {calls['LoreSummary']}회, 인물 {len(r.characters)}, 관계 {len(r.relations)}, "
      f"사건 {len(r.events)}, 로어북 {len(r.lore_entries)} {dict(cats)}, 오류 {r.errors}")
assert r.lore_entries and r.characters and r.relations and r.events
assert all(a.evidence_scene_ids for e in r.lore_entries for a in e.attributes), "근거 없는 속성이 남음"
assert any(e.summary for e in r.lore_entries)
assert all(e.related is not None for e in r.lore_entries)

# 캐시 재실행: 추출 LLM 호출이 0이어야 한다(이어서 실행)
before = calls["SceneExtraction"]
run_analysis(NID)
assert calls["SceneExtraction"] == before, "캐시를 쓰지 않고 다시 추출함"
print("캐시 재실행 OK (추출 호출 0회)")

md = to_markdown(NID, r.lore_entries)
assert md.startswith("# 로어북:") and "## 인물" in md and "### " in md
print("마크다운 길이", len(md))

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
c = TestClient(app)
assert c.get(f"/novels/{NID}/graph").json()["links"][0]["type"] in ("협력·동료",)
assert c.get(f"/novels/{NID}/lore?category=인물").json()["entries"]
assert c.get(f"/novels/{NID}/lore/export").text.startswith("# 로어북")
j = c.post(f"/novels/{NID}/ask", json={"question": "Who is Holmes?"}).json()
assert "highlight" in j and "lore" in j["highlight"], j
p = c.get("/llm/providers").json()
assert "api_key" not in str(p) and any(x["id"] == "anthropic" and x["ready"] for x in p["providers"])
assert c.put("/llm/config", json={"provider": "openai", "model": "x"}).json()["ok"] is False
print("API 점검 OK")

if os.getenv("KEEP"):
    print("KEEP=1: 결과를 남겨 둠(화면 확인용)")
    sys.exit(0)
for f in RESULTS.glob(f"{NID}*"):
    f.unlink()
try:
    store.client().delete_collection("novel_smoke_hash")
except Exception:
    pass
print("스모크 테스트 통과")
