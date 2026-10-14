"""LangGraph 노드: load_scenes -> extract(Send 병렬) -> normalize -> lore -> validate -> save"""
import difflib
import hashlib
import json
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel, Field

from app import cache, graphdb, jobs, llm
from app.config import RESULTS
from app.graphdb import ontology
from app.llm import LlmError
from app.rag import store
from app.schemas import (CATEGORIES, AnalysisResult, Character, Event, LoreAttr, LoreEntry, Relation,
                         SceneExtraction, LoreSummary)

NL = chr(10)
SEP2 = NL + NL
MAX_LORE = 200          # 로어북 항목 상한
MAX_SUMMARIES = 80      # 요약을 LLM으로 쓰는 항목 수(등장 장면 수 상위)
TITLE_RE = re.compile(r"^(mr|mrs|miss|dr|sir|lady|lord|inspector|captain|colonel|professor)\.?\s+", re.I)
GENERIC = re.compile(r"^(the|a|an|this|that|his|her|their|our|my)\b|'s\b|\b(brother|sister|wife|husband|waiter|maid|boy|girl|lady|"
                     r"man|woman|narrator|convict|shepherd|postmaster|servant|stranger|baronet|irregulars)\b|들$|^(그|이|저)\s", re.I)
KO_GENERIC = {"부인", "선생", "아버지", "어머니", "어른", "처녀", "계집", "갈보", "악한", "아이", "여자", "남자", "노인", "청년", "소녀", "소년"}
# '김첨지의 아내'처럼 이름이 끝까지 나오지 않는 인물은 관계 호칭형 이름이 유일한 식별자이므로 허용한다
KIN_POSS = re.compile(r"의\s*(아내|남편|아버지|어머니|아들|딸|형|동생|누이|오빠|언니|누나|친구|스승|제자|하인|주인|장인|장모|아저씨|아주머니)$")
NARRATOR = "나(화자)"                       # 1인칭 화자는 하나의 인물로 통일한다
NARRATOR_FORMS = {"나", "내", "저", "제", "화자", "나(화자)", "나 (화자)", "'나'", "“나”", "필자"}
NARRATOR_ID = "나_화자"
HON_TITLES = ("선생님", "선생", "어른", "님", "씨")   # 호칭: 표시 이름에서는 떼고 병합 기준으로도 쓴다
HON_PARTICLES = ("이", "아", "야")                   # 이름 뒤 조사/접미(점순이, 점순아): 병합 기준으로만 쓴다
_SUMMARY_LOCK = __import__("threading").Lock()


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9가-힣]+", "_", TITLE_RE.sub("", name.lower())).strip("_") or "x"


def _in_text(quote: str, text: str) -> bool:
    q = _norm(quote)
    return not q or q in _norm(text)


def _snap(quote: str, text: str) -> str:
    """LLM이 바꿔 쓴 근거 문장을 원문에서 가장 비슷한 문장으로 교정. 비슷한 문장이 없으면 빈 문자열."""
    if not quote or _norm(quote) in _norm(text):
        return quote
    best, score = "", 0.0
    q = _norm(quote)
    for s in re.split(r"(?<=[.!?。”\"])\s+", re.sub(r"\s+", " ", text)):
        r = difflib.SequenceMatcher(None, q, _norm(s)).ratio()
        if r > score:
            best, score = s, r
    return best if score >= 0.6 else ""


def _bad_person(n: str) -> bool:
    """일반 명사형·소유격 관계 표현·대명사는 인물로 보지 않는다."""
    n = n.strip()
    if n == NARRATOR or KIN_POSS.search(n):
        return False
    return (len(n) < 2 or (len(n) < 3 and n.isascii()) or n[0].islower() or n in ("I", "He", "She", "You")
            or n in KO_GENERIC or bool(GENERIC.search(n)))


def _strip_title(n: str) -> str:
    """'장인님' -> '장인' (호칭 접미 제거, 남는 글자가 2자 이상일 때만)."""
    if n.isascii():
        return n
    for h in HON_TITLES:
        if n.endswith(h) and len(n) - len(h) >= 2:
            return n[: -len(h)]
    return n


def _base(n: str) -> str:
    """병합 기준 이름: 호칭·이름 접미(님·씨·이·아·야)를 뗀 형태."""
    b = _strip_title(n)
    if not b.isascii():
        for h in HON_PARTICLES:
            if b.endswith(h) and len(b) - len(h) >= 2:
                return b[: -len(h)]
    return b


# ---------- 1. 장면 로드 ----------
def load_scenes(state):
    scenes = sorted(store.load_scenes(state["novel_id"]), key=lambda s: s.order)
    cached = {} if state.get("fresh") else cache.load(state["novel_id"], llm.current()["model"], scenes)
    return {"scenes": scenes, "retry_count": 0, "errors": [], "extractions": cached,
            "scene_errors": {sid: "" for sid in cached}}


def fan_out(state):
    from langgraph.types import Send
    todo = [s for s in state["scenes"] if s.scene_id not in state["extractions"]]
    return [Send("extract", {"scene": s, "novel_id": state["novel_id"]}) for s in todo] or "normalize"


# ---------- 2. 장면별 추출 (Send 팬아웃: 장면당 1회 실행) ----------
def extract(payload):
    sc = payload["scene"]
    jobs.check_cancel(payload["novel_id"])
    try:
        raw = llm.generate_json(f"장면 {sc.scene_id}:{SEP2}{sc.text}", SceneExtraction,
                                system=llm.prompt("extract"), effort="low", max_tokens=3500)
    except LlmError as e:
        msg = str(e)
        if any(k in msg for k in ("429", "RateLimit", "quota", "RESOURCE_EXHAUSTED")):
            # 호출 한도 초과는 장면마다 재시도해도 같은 결과(대기만 길어짐)이므로 즉시 중단한다. 끝난 장면은 캐시에 있어 이어서 실행된다.
            raise LlmError("호출 한도 초과(429): 무료 티어의 분당/일일 한도일 수 있습니다. 지금까지 끝난 장면은 저장돼 있으니 "
                           "한도가 풀린 뒤 ‘분석 실행’을 다시 누르면 이어서 진행합니다. 또는 다른 공급사·모델·키를 쓰세요.") from None
        if any(k in msg for k in ("401", "403", "Authentication", "PermissionDenied", "API 키")):
            raise   # 키 문제는 장면마다 재시도해도 소용없으므로 분석 전체를 중단
        return {"scene_errors": {sc.scene_id: msg[:160]}}
    ext = SceneExtraction(**raw)
    # 근거 문장 보정: 원문과 가장 비슷한 문장으로 교정하고, 근거를 못 찾으면 해당 속성·관계를 버린다
    for ent in ext.entities:
        for a in ent.attributes:
            a.quote = _snap(a.quote, sc.text)
        ent.attributes = [a for a in ent.attributes if a.quote and a.key.strip() and a.value.strip()]
    for r in ext.relations:
        r.quote = _snap(r.quote, sc.text)
    ext.relations = [r for r in ext.relations if r.quote]
    for v in ext.events:
        v.quote = _snap(v.quote, sc.text)
    dump = ext.model_dump()
    cache.append(payload["novel_id"], llm.current()["model"], sc.scene_id, sc.text, dump)   # 장면별 결과 즉시 저장
    return {"extractions": {sc.scene_id: dump}, "scene_errors": {sc.scene_id: ""}}


def retry_fan_out(state):
    """검증 실패 시 실패한 장면만 다시 추출."""
    from langgraph.types import Send
    bad = {e.split(":")[1] for e in state["errors"] if e.startswith("scene:")}
    return [Send("extract", {"scene": s, "novel_id": state["novel_id"]}) for s in state["scenes"] if s.scene_id in bad]


# ---------- 3. 정규화 (별칭 병합, 엔티티 생성) ----------
def _tokens(name: str) -> frozenset:
    return frozenset(re.findall(r"[a-z가-힣]+", TITLE_RE.sub("", name.lower())))


def _singular(n: str, text: str) -> str:
    parts = n.split()
    last = parts[-1]
    if len(last) > 4 and last.endswith("s") and re.search(r"\b" + re.escape(last[:-1]) + r"\b", text):
        parts[-1] = last[:-1]
    return " ".join(parts)


def _cluster(names: Counter, merge_map: dict, drop: set, prefix: str = ""):
    """토큰 부분집합·한글 접미 일치로 별칭을 병합. 반환: (정규화된 이름 -> id, id -> (대표이름, 별칭들))."""
    keyed = {n: _tokens(n) for n in names if _tokens(n)}
    parent = {n: n for n in keyed}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    # 한국어 호칭·접미 병합: 장인/장인님, 구장/구장님, 점순/점순이 처럼 기본형이 같으면 같은 인물
    by_base = defaultdict(list)
    for n in keyed:
        if not n.isascii():
            by_base[_base(n)].append(n)
    for b, ms in by_base.items():
        group = ms + ([b] if b in keyed and b not in ms else [])
        for m in group[1:]:
            parent[find(m)] = find(group[0])
    for n, t in keyed.items():
        cands = [m for m in keyed if m != n and t < keyed[m]]
        same = [m for m in keyed if m != n and keyed[m] == t and names[m] > names[n]]
        cands += same
        if not n.isascii():  # 한글 이름: '형식'은 '이형식'의 일부(접미 일치)
            cands += [m for m in keyed if m != n and not m.isascii() and any(
                len(a) >= 2 and b != a and b.endswith(a) for a in t for b in keyed[m])]
        if cands:
            parent[find(n)] = find(max(cands, key=lambda m: (names[m], len(keyed[m]))))
    clusters = defaultdict(list)
    for n in keyed:
        clusters[find(n)].append(n)
    name2cid, canon = {}, {}
    for members in clusters.values():
        plain = [m for m in members if _strip_title(m) == m] or members     # 호칭이 붙지 않은 표기를 대표 이름으로 선호
        best = max(plain, key=lambda m: (len(TITLE_RE.sub("", m).split()), names[m]))
        best_clean = TITLE_RE.sub("", best).strip() or best
        if _norm(best_clean) in drop or _norm(best) in drop or any(_norm(m) in drop for m in members):
            continue
        cid = prefix + _slug(best)
        canon[cid] = (best_clean, sorted(set(members) - {best_clean}))
        for m in members:
            name2cid[_norm(m)] = cid
    return name2cid, canon


class _AliasGroup(BaseModel):
    canonical: str = Field(description="그룹의 대표 이름 (members 중 하나)")
    members: list[str] = Field(description="같은 인물을 가리키는 이름들 (후보 목록에 있는 표기 그대로)")


class _AliasResolution(BaseModel):
    groups: list[_AliasGroup] = Field(default_factory=list)
    not_characters: list[str] = Field(default_factory=list, description="개별 인물이 아닌 항목(군중·일반 명사 등)")


def _resolve_aliases(novel_id, canon, counts, exts, order):
    """LLM이 인물 후보 목록을 보고 같은 인물을 묶는다. 코드가 결과를 검증(후보에 있는 이름만 허용)한다.
    반환: (병합 {이름: 대표이름}, 제외 [이름]). 실패하거나 키가 없으면 빈 결과."""
    if len(canon) < 3 or not llm.is_configured():
        return {}, []
    cands = []
    for cid, (nm, al) in canon.items():
        members = [nm, *al]
        cnt = sum(counts.get(m, 0) for m in members)
        ctx = ""
        for sid in order:
            for e in exts[sid].entities:
                if e.category == "인물" and (e.name in members or any(a in members for a in e.aliases)):
                    ctx = next((a.quote for a in e.attributes if a.quote), "") or ctx
            if ctx:
                break
        cands.append((cnt, nm, al, ctx))
    cands.sort(key=lambda x: -x[0])
    cands = cands[:70]
    key = hashlib.sha1("|".join(sorted(f"{c[1]}:{','.join(sorted(c[2]))}" for c in cands)).encode()).hexdigest()
    f = RESULTS / f"{novel_id}.aliases.json"
    if f.exists():
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if d.get("key") == key:
                return d["merge"], d["drop"]
        except ValueError:
            pass
    listing = NL.join(f"- {nm} | 별칭: {', '.join(al) or '-'} | 언급 {cnt}회 | 문맥: {ctx[:90]}" for cnt, nm, al, ctx in cands)
    try:
        out = llm.generate_json("인물 후보 목록:" + NL + listing, _AliasResolution, system=llm.prompt("resolve"),
                                effort="low", max_tokens=3000)
    except LlmError as e:
        if any(k in str(e) for k in ("401", "403", "Authentication", "429", "RateLimit", "quota", "RESOURCE_EXHAUSTED")):
            raise
        return {}, []
    allowed = {m for _, nm, al, _ in cands for m in (nm, *al)}
    merge, used = {}, set()
    for g in out.get("groups", []):
        mem = [m for m in g["members"] if m in allowed and m not in used]
        if g["canonical"] not in mem or len(mem) < 2:
            continue
        for m in mem:
            used.add(m)
            if m != g["canonical"]:
                merge[m] = g["canonical"]
    drop = [n for n in out.get("not_characters", []) if n in allowed and n not in merge and n != NARRATOR]
    f.write_text(json.dumps({"key": key, "merge": merge, "drop": drop}, ensure_ascii=False, indent=1), encoding="utf-8")
    return merge, drop


def normalize(state):
    scenes = {s.scene_id: s for s in state["scenes"]}
    exts = {sid: SceneExtraction(**e) for sid, e in state["extractions"].items() if sid in scenes}
    order = sorted(exts, key=lambda sid: scenes[sid].order)
    all_text = " ".join(s.text for s in scenes.values())
    overrides = {}
    of = RESULTS / f"{state['novel_id']}.overrides.json"
    if of.exists():
        overrides = json.loads(of.read_text(encoding="utf-8"))
    merge_map = {_norm(k): v for k, v in overrides.get("merge", {}).items()}
    drop = {_norm(x) for x in overrides.get("drop", [])}

    def fixname(n, cat):
        n = merge_map.get(_norm(n), n)
        if cat == "인물":
            if n.strip() in NARRATOR_FORMS:
                return NARRATOR
            return _singular(n, all_text)
        return n

    def collect(cat):
        names: Counter = Counter()
        for sid in order:
            for e in exts[sid].entities:
                if e.category != cat:
                    continue
                for n in {e.name, *e.aliases}:
                    n = fixname(n, cat)
                    if len(n.strip()) >= 2 and not (cat == "인물" and _bad_person(n)):
                        names[n] += 1
        return names

    # --- 카테고리별 이름 집계와 병합 (1차: 규칙 기반) ---
    maps, canons = {}, {}
    for cat in CATEGORIES:
        maps[cat], canons[cat] = _cluster(collect(cat), merge_map, drop, "" if cat == "인물" else cat + ":")

    # --- 2차: LLM 별칭 정리 (규칙이 못 잡는 호칭 변형·지칭 통합, 인물이 아닌 항목 제외). 결과는 파일에 캐시 ---
    resolved_merge, resolved_drop = _resolve_aliases(state["novel_id"], canons["인물"], collect("인물"), exts, order)
    if resolved_merge or resolved_drop:
        for k, v in resolved_merge.items():
            merge_map.setdefault(_norm(k), v)           # 사용자가 직접 지정한 병합(overrides)이 우선
        drop |= {_norm(x) for x in resolved_drop}
        maps["인물"], canons["인물"] = _cluster(collect("인물"), merge_map, drop, "")

    def cid_of(n, cat="인물"):
        n = fixname(n, cat)      # '나' -> '나(화자)' 변환을 먼저 한 뒤 잡음 여부를 판단
        if cat == "인물" and _bad_person(n):
            return None
        return maps[cat].get(_norm(n))

    # --- 항목별 등장 장면·속성 수집 ---
    scene_ids = {cat: defaultdict(list) for cat in CATEGORIES}
    attrs = {cat: defaultdict(lambda: defaultdict(lambda: {"scenes": [], "quotes": {}})) for cat in CATEGORIES}
    for sid in order:
        low = scenes[sid].text.lower()
        for e in exts[sid].entities:
            cid = cid_of(e.name, e.category)
            if not cid:
                continue
            toks = [t for t in _tokens(e.name) | {t for a in e.aliases for t in _tokens(a)} if len(t) > (2 if t.isascii() else 1)]
            # 이름(의 일부)이 실제로 그 장면 원문에 나올 때만 등장으로 인정 -> 모델이 지어낸 대상 제거
            if not any(t in low for t in toks) and cid != NARRATOR_ID:   # 화자는 '나'가 대명사로 쓰여 이름 확인이 불가능
                continue
            if sid not in scene_ids[e.category][cid]:
                scene_ids[e.category][cid].append(sid)
            for a in e.attributes:
                slot = attrs[e.category][cid][(a.key.strip(), a.value.strip())]
                if sid not in slot["scenes"]:
                    slot["scenes"].append(sid)
                    slot["quotes"][sid] = a.quote

    # --- 인물(관계도 노드) ---
    characters = [Character(id=cid, name=nm, aliases=al, first_scene=scene_ids["인물"][cid][0],
                            scene_count=len(scene_ids["인물"][cid]), scene_ids=scene_ids["인물"][cid])
                  for cid, (nm, al) in canons["인물"].items() if scene_ids["인물"].get(cid)]
    characters.sort(key=lambda c: -c.scene_count)
    valid = {c.id for c in characters}

    # --- 관계: 쌍·대분류별 집계 (관계가 바뀌면 별도 Relation으로 since_chapter와 함께 남음) ---
    agg = defaultdict(lambda: {"labels": Counter(), "dirs": Counter(), "scenes": {}, "chap": 9999, "n": 0})
    for sid in order:
        for r in exts[sid].relations:
            a, b = cid_of(r.a), cid_of(r.b)
            if not a or not b or a == b or a not in valid or b not in valid:
                continue
            g = agg[(tuple(sorted((a, b))), r.type)]
            g["n"] += 1
            if r.label.strip():
                g["labels"][r.label.strip()] += 1
            if r.directed:
                g["dirs"][(a, b)] += 1
            g["scenes"].setdefault(sid, r.quote)
            g["chap"] = min(g["chap"], scenes[sid].chapter)
    per_pair = defaultdict(list)
    for (pair, typ), g in agg.items():
        per_pair[pair].append((typ, g))
    relations = []
    for pair, groups in per_pair.items():
        groups.sort(key=lambda x: -x[1]["n"])
        for typ, g in groups[:2]:
            src, tgt = pair
            directed = bool(g["dirs"]) and sum(g["dirs"].values()) * 2 >= g["n"]
            if directed:
                (src, tgt), _ = g["dirs"].most_common(1)[0]
            ev = sorted(g["scenes"], key=lambda s: scenes[s].order)[:6]
            relations.append(Relation(source=src, target=tgt, type=typ,
                                      label=g["labels"].most_common(1)[0][0] if g["labels"] else typ,
                                      directed=directed, since_chapter=g["chap"], since_scene=ev[0], evidence_scene_ids=ev,
                                      evidence_quotes={s: g["scenes"][s] for s in ev}))
    relations.sort(key=lambda r: -len(r.evidence_scene_ids))
    relations = relations[:120]

    # --- 사건: 장면당 최대 2개, 같은 요약은 합침 ---
    events = []
    for sid in order:
        seen = set()
        for ev in exts[sid].events[:2]:
            if _norm(ev.summary) in seen or not ev.summary.strip():
                continue
            seen.add(_norm(ev.summary))
            events.append(Event(id=f"ev_{len(events) + 1:03d}", summary=ev.summary.strip(), scene_id=sid,
                                chapter=scenes[sid].chapter,
                                characters=sorted({cid_of(n) for n in ev.characters if cid_of(n) in valid}),
                                location=ev.location.strip(), quote=ev.quote))

    # --- 로어북 항목(요약 전) ---
    raw_entries = []
    for cat in CATEGORIES:
        for cid, (nm, al) in canons[cat].items():
            sids = scene_ids[cat].get(cid)
            if not sids:
                continue
            at = [LoreAttr(key=k, value=v, evidence_scene_ids=sorted(s["scenes"], key=lambda x: scenes[x].order),
                           evidence_quotes=s["quotes"]) for (k, v), s in attrs[cat][cid].items()]
            at.sort(key=lambda a: (a.key, scenes[a.evidence_scene_ids[0]].order))
            # 한 번만 나오고 속성도 없는 비인물 항목은 잡음일 가능성이 커서 제외
            if len(sids) < 2 and not at and cat != "인물":
                continue
            raw_entries.append((cat, cid, nm, al, sids, at))
    raw_entries.sort(key=lambda x: (CATEGORIES.index(x[0]), -len(x[4])))
    raw_entries = raw_entries[:MAX_LORE]
    lore, idmap = [], {}
    for i, (cat, cid, nm, al, sids, at) in enumerate(raw_entries, 1):
        lid = f"lore_{i:03d}"
        idmap[(cat, cid)] = lid
        lore.append(LoreEntry(id=lid, category=cat, name=nm, aliases=al, attributes=at, first_scene=sids[0],
                              scene_count=len(sids), scene_ids=sids, character_id=cid if cat == "인물" else None))
    # 함께 나온 항목: 같은 장면에 등장한 다른 항목(코드로 계산)
    by_scene = defaultdict(list)
    for e in lore:
        for s in e.scene_ids:
            by_scene[s].append(e.id)
    for e in lore:
        co = Counter(o for s in e.scene_ids for o in by_scene[s] if o != e.id)
        e.related = [o for o, _ in co.most_common(8)]
    # 사건 장소 -> 로어북 장소 항목 id (타임라인 레인의 근거)
    for i, ev in enumerate(events):
        pid = idmap.get(("장소", maps["장소"].get(_norm(ev.location)))) if ev.location else None
        if pid:
            events[i] = ev.model_copy(update={"location_id": pid})
    return {"characters": characters, "relations": relations, "events": events, "lore_entries": lore}


# ---------- 4. 로어북 생성 (요약) ----------
def _excerpt(text: str, names: list, width: int = 450) -> str:
    low = text.lower()
    pos = min([p for p in (low.find(n.lower()) for n in names if n) if p >= 0] or [0])
    s = max(0, pos - width // 3)
    return re.sub(r"\s+", " ", text[s:s + width]).strip()


def lore(state):
    scenes = {s.scene_id: s for s in state["scenes"]}
    entries = list(state["lore_entries"])
    targets = sorted([e for e in entries if e.attributes or e.scene_count >= 2],
                     key=lambda e: -e.scene_count)[:MAX_SUMMARIES]

    sfile = RESULTS / f"{state['novel_id']}.summaries.json"      # 요약 캐시(내용 해시 기준): 보정 후 재처리해도 호출 최소화
    try:
        scache = json.loads(sfile.read_text(encoding="utf-8")) if sfile.exists() else {}
    except ValueError:
        scache = {}

    def summarize(e: LoreEntry):
        jobs.check_cancel(state["novel_id"])
        attr_lines = NL.join(f"- {a.key}: {a.value} (장면 {', '.join(a.evidence_scene_ids[:3])})" for a in e.attributes[:12]) or "(속성 없음)"
        ex = NL.join(f"[{s}] {_excerpt(scenes[s].text, [e.name, *e.aliases])}" for s in e.scene_ids[:3])
        prompt = f"대상: {e.name} (분류: {e.category})" + SEP2 + "속성:" + NL + attr_lines + SEP2 + "장면 발췌:" + NL + ex
        key = hashlib.sha1((state["novel_id"] + prompt).encode()).hexdigest()
        if key in scache:
            return e.id, scache[key]
        try:
            out = llm.generate_json(prompt, LoreSummary, system=llm.prompt("lore"), effort="low", max_tokens=700)
            with _SUMMARY_LOCK:
                scache[key] = out["summary"].strip()
            return e.id, scache[key]
        except LlmError as err:
            if any(k in str(err) for k in ("401", "403", "Authentication", "API 키", "429", "RateLimit", "quota", "RESOURCE_EXHAUSTED")):
                raise
            return e.id, ""

    with ThreadPoolExecutor(max_workers=llm.concurrency()) as ex:
        sums = dict(ex.map(summarize, targets))
    sfile.write_text(json.dumps(scache, ensure_ascii=False), encoding="utf-8")
    out = []
    for e in entries:
        s = sums.get(e.id, "")
        flags = list(e.flags)
        if e.id in sums and not s:
            flags.append("요약 실패")
        out.append(e.model_copy(update={"summary": s, "flags": flags}))
    return {"lore_entries": out}


# ---------- 5. 검증 ----------
def validate(state):
    scenes = {s.scene_id: s for s in state["scenes"]}
    errors = [f"scene:{sid}:{msg}" for sid, msg in state["scene_errors"].items() if msg]
    # 근거 없는 로어북 속성 제거 (장면 ID가 실존하고 인용이 그 장면 원문에 있어야 함)
    cleaned = []
    for e in state["lore_entries"]:
        kept, removed = [], 0
        for a in e.attributes:
            ev = [s for s in a.evidence_scene_ids if s in scenes and _in_text(a.evidence_quotes.get(s, ""), scenes[s].text)]
            if ev:
                kept.append(a.model_copy(update={"evidence_scene_ids": ev, "evidence_quotes": {s: a.evidence_quotes.get(s, "") for s in ev}}))
            else:
                removed += 1
        flags = list(e.flags)
        if removed and not kept:
            flags.append("근거 부족")
        cleaned.append(e.model_copy(update={"attributes": kept, "flags": flags}))
    # 온톨로지(ontology.yaml)에 없는 클래스·관계 타입 제거, 관계는 양쪽 인물이 모두 있을 때만 통과
    classes = ontology.classes()
    cleaned = [e for e in cleaned if ontology.class_of_category(e.category) in classes]
    chars = {c.id for c in state["characters"]}
    relations = [r for r in state["relations"]
                 if ontology.is_allowed_relation(r.type) and r.source in chars and r.target in chars]
    for r in relations:
        for s in r.evidence_scene_ids:
            if s not in scenes:
                errors.append(f"relation:{r.source}-{r.target}:존재하지 않는 장면 {s}")
    errors = list(dict.fromkeys(errors))
    return {"lore_entries": cleaned, "relations": relations, "errors": errors,
            "retry_count": state["retry_count"] + (1 if errors else 0)}


def route_after_check(state):
    if not state["errors"]:
        return "save"
    scene_level = any(e.startswith("scene:") for e in state["errors"])
    if state["retry_count"] <= 2 and scene_level:
        return retry_fan_out(state)
    return "save"  # 2회 넘으면 실패 항목을 표시하고 저장


# ---------- 6. 저장 ----------
def save(state):
    scenes = {s.scene_id: s for s in state["scenes"]}
    if not state["extractions"]:   # 전부 실패했는데 빈 결과로 이전 결과를 덮어쓰지 않는다
        raise LlmError("추출에 성공한 장면이 없어 결과를 저장하지 않았습니다. 오류: " +
                       "; ".join(v for v in state["scene_errors"].values() if v)[:200])
    cfg = llm.current()
    errors = list(state["errors"])
    rels = []
    for r in state["relations"]:
        ev = [s for s in r.evidence_scene_ids if s in scenes and _in_text(r.evidence_quotes.get(s, ""), scenes[s].text)]
        if ev:
            rels.append(r.model_copy(update={"evidence_scene_ids": ev, "evidence_quotes": {s: r.evidence_quotes.get(s, "") for s in ev}}))
    events = [e.model_copy(update={"quote": e.quote if _in_text(e.quote, scenes[e.scene_id].text) else ""}) for e in state["events"]]
    res = AnalysisResult(novel_id=state["novel_id"], provider=cfg["provider"], model=cfg["model"],
                         characters=state["characters"], relations=rels, events=events,
                         lore_entries=state["lore_entries"], errors=errors)
    (RESULTS / f"{state['novel_id']}.json").write_text(res.model_dump_json(indent=1), encoding="utf-8")
    # JSON이 기본 데이터. Neo4j는 같은 데이터를 한 번 더 저장하는 사본이라 실패해도 분석은 성공으로 둔다
    return {"errors": errors, "graph_db": graphdb.sync(res, state["scenes"])}
