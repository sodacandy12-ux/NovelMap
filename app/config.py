import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
NOVELS = DATA / "novels"
CHROMA = DATA / "chroma"
RESULTS = DATA / "results"
WEB = ROOT / "web"
PROMPTS = Path(__file__).resolve().parent / "prompts"

for p in (NOVELS, CHROMA, RESULTS):
    p.mkdir(parents=True, exist_ok=True)


def _load_env():
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if v.strip():
                    os.environ.setdefault(k.strip(), v.strip())


_load_env()
