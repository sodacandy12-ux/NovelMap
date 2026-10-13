"""시연용 사전 분석: python scripts/precompute.py [novel_id] [txt경로] [--fresh]
API 키는 환경 변수(ANTHROPIC_API_KEY 등) 또는 .env에서 읽는다. LLM_PROVIDER / LLM_MODEL로 공급사·모델을 고른다."""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import llm  # noqa: E402
from app.config import NOVELS  # noqa: E402
from app.rag import store  # noqa: E402
from app.rag.splitter import split_scenes  # noqa: E402
from app.service import load_result, run_analysis  # noqa: E402

args = [a for a in sys.argv[1:] if not a.startswith("--")]
fresh = "--fresh" in sys.argv
nid = args[0] if args else "baskervilles"
path = Path(args[1]) if len(args) > 1 else NOVELS / f"{nid}.txt"
if os.getenv("LLM_PROVIDER") and os.getenv("LLM_MODEL"):
    llm.set_config(os.environ["LLM_PROVIDER"], os.environ["LLM_MODEL"], None)
c = llm.current()
if not c["api_key"]:
    sys.exit(f"API 키가 없습니다({c['provider']}). 환경 변수 또는 .env에 키를 넣으세요.")
t0 = time.time()
scenes = split_scenes(path.read_text(encoding="utf-8", errors="ignore"))
print(f"장면 {len(scenes)}개 / 장 {len({s.chapter for s in scenes})}개, 임베딩={store.embed_mode()}, LLM={c['provider']}/{c['model']}")
store.index_scenes(nid, scenes)
run_analysis(nid, lambda e: print(f"  [{e['label']}] {e['done']}/{e['total']}"), fresh)
r = load_result(nid)
cats = {}
for e in r.lore_entries:
    cats[e.category] = cats.get(e.category, 0) + 1
print(f"완료 {time.time() - t0:.1f}s: 인물 {len(r.characters)}, 관계 {len(r.relations)}, 사건 {len(r.events)}, "
      f"로어북 {len(r.lore_entries)} {cats}, 오류 {len(r.errors)}")
