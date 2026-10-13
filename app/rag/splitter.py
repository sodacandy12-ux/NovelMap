"""원고 txt -> 장(chapter) -> 장면(scene) 분할."""
import re
from app.schemas import Scene

CH_RE = re.compile(r"^\s*(?:chapter|CHAPTER)\s+(\d+|[IVXLC]+)\.?\s*$|^\s*(?:제\s*)?(\d+)\s*장\s*.*$", re.M | re.I)
SEP_RE = re.compile(r"^\s*(?:[-*_=~]{3,}|\*\s+\*\s+\*)\s*$")
MAX_TOK, MIN_TOK = 600, 150


def tok(s: str) -> int:
    """대략적 토큰 수: 한글은 글자당 ~0.8, 그 외는 영어 기준(공백 단어 x 1.33)."""
    ko = len(re.findall(r"[가-힣]", s))
    if ko > len(s) * 0.3:
        return int(ko * 0.8 + (len(s) - ko) / 4) + 1
    return int(len(s.split()) * 1.33) + 1


def strip_boilerplate(raw: str) -> str:
    m = re.search(r"\*\*\* START OF .*?\*\*\*", raw)
    if m:
        raw = raw[m.end():]
    m = re.search(r"\*\*\* END OF .*?\*\*\*", raw)
    if m:
        raw = raw[: m.start()]
    return raw


def split_chapters(raw: str) -> list[tuple[int, str, str]]:
    raw = strip_boilerplate(raw).replace("\r\n", "\n")
    ms = list(CH_RE.finditer(raw))
    if not ms:
        return [(1, "", raw.strip())]
    out = []
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(raw)
        body = raw[m.end():end].strip("\n")
        lines = body.split("\n")
        title = ""
        # 장 제목: 헤딩 직후 첫 비어있지 않은 짧은 줄
        for j, ln in enumerate(lines):
            if ln.strip():
                if len(ln.strip()) < 70 and (j + 1 >= len(lines) or not lines[j + 1].strip()):
                    title = ln.strip()
                    body = "\n".join(lines[j + 1:])
                break
        out.append((i + 1, title, body.strip()))
    return out


def _paragraphs(body: str) -> list[list[str]]:
    """구분선 기준 블록 -> 문단 리스트."""
    blocks, cur = [], []
    for ln in body.split("\n"):
        if SEP_RE.match(ln):
            blocks.append(cur)
            cur = []
        else:
            cur.append(ln)
    blocks.append(cur)
    res = []
    for b in blocks:
        paras = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", "\n".join(b))]
        paras = [p for p in paras if p]
        # 빈 줄 없이 줄바꿈만 있는 원고(한 문단이 수천 자)는 문장 단위로 쪼개 장면 크기를 맞춘다
        fixed = []
        for p in paras:
            if tok(p) <= MAX_TOK:
                fixed.append(p)
                continue
            cur, n = [], 0
            for sent in re.split(r"(?<=[.!?。…”\"’'])\s+", p):
                t = tok(sent)
                if cur and n + t > MAX_TOK * 0.8:
                    fixed.append(" ".join(cur))
                    cur, n = [], 0
                cur.append(sent)
                n += t
            if cur:
                fixed.append(" ".join(cur))
        paras = fixed
        if paras:
            res.append(paras)
    return res


def split_scenes(raw: str) -> list[Scene]:
    scenes: list[Scene] = []
    order = 0
    for ch, title, body in split_chapters(raw):
        chunks: list[str] = []
        for paras in _paragraphs(body):
            cur: list[str] = []
            for p in paras:
                if cur and tok(" ".join(cur)) + tok(p) > MAX_TOK:
                    chunks.append(" ".join(cur))
                    cur = []
                cur.append(p)
            if cur:
                chunks.append(" ".join(cur))
        # 150토큰 미만은 앞 장면에 합침
        merged: list[str] = []
        for c in chunks:
            if merged and tok(c) < MIN_TOK:
                merged[-1] += "\n\n" + c
            else:
                merged.append(c)
        if len(merged) > 1 and tok(merged[0]) < MIN_TOK:
            merged[1] = merged[0] + "\n\n" + merged[1]
            merged = merged[1:]
        for i, c in enumerate(merged, 1):
            order += 1
            scenes.append(Scene(scene_id=f"ch{ch:02d}_s{i:02d}", chapter=ch, order=order, text=c, chapter_title=title))
    for i, s in enumerate(scenes):
        s.prev_id = scenes[i - 1].scene_id if i else None
        s.next_id = scenes[i + 1].scene_id if i + 1 < len(scenes) else None
    return scenes
