from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.schemas import CATEGORIES
from app.service import load_result

router = APIRouter()


def _res(nid):
    try:
        return load_result(nid)
    except FileNotFoundError:
        raise HTTPException(404, "분석 결과가 없습니다. 먼저 분석을 실행하세요")


@router.get("/novels/{nid}/lore")
def lore(nid: str, category: Optional[str] = None, q: Optional[str] = None):
    r = _res(nid)
    items = r.lore_entries
    if category:
        items = [e for e in items if e.category == category]
    if q:
        ql = q.lower()
        items = [e for e in items if ql in e.name.lower() or any(ql in a.lower() for a in e.aliases)
                 or ql in e.summary.lower() or any(ql in a.value.lower() for a in e.attributes)]
    counts = {c: sum(1 for e in r.lore_entries if e.category == c) for c in CATEGORIES}
    return {"categories": CATEGORIES, "counts": counts, "entries": [e.model_dump() for e in items]}


def to_markdown(title: str, entries) -> str:
    """JSON -> 마크다운 문자열 템플릿 (LLM 호출 없음). 분류별 H2, 항목별 H3, 속성 불릿."""
    lines = [f"# 로어북: {title}", ""]
    for cat in CATEGORIES:
        items = [e for e in entries if e.category == cat]
        if not items:
            continue
        lines += [f"## {cat}", ""]
        for e in items:
            lines.append(f"### {e.name}")
            if e.aliases:
                lines.append(f"- 별칭: {', '.join(e.aliases)}")
            for a in e.attributes:
                lines.append(f"- {a.key}: {a.value} ({', '.join(a.evidence_scene_ids)})")
            lines.append(f"- 첫 등장: {e.first_scene} · 등장 장면 수: {e.scene_count}")
            if e.flags:
                lines.append(f"- 표시: {', '.join(e.flags)}")
            lines.append("")
            if e.summary:
                lines += [e.summary, ""]
    return "\n".join(lines).rstrip() + "\n"


@router.get("/novels/{nid}/lore/export")
def export(nid: str):
    r = _res(nid)
    md = to_markdown(nid, r.lore_entries)
    fname = quote(f"lorebook_{nid}.md")
    return Response(md, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"})
