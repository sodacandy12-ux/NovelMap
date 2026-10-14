from typing import Literal, Optional
from pydantic import BaseModel, Field

RelType = Literal["가족", "연애·혼인", "협력·동료", "위계·소속", "적대·갈등", "기타·중립"]
Category = Literal["인물", "장소", "조직", "물건", "용어"]
CATEGORIES = ["인물", "장소", "조직", "물건", "용어"]
REL_TYPES = ["가족", "연애·혼인", "협력·동료", "위계·소속", "적대·갈등", "기타·중립"]


class Scene(BaseModel):
    scene_id: str
    chapter: int
    order: int
    text: str
    prev_id: Optional[str] = None
    next_id: Optional[str] = None
    chapter_title: str = ""


# ---- 장면별 추출 원본 (LLM 구조화 출력 스키마) ----
class ExtAttr(BaseModel):
    key: str = Field(description="속성 이름 (예: 나이, 직업, 외모, 소속, 직함)")
    value: str = Field(description="속성 값 (한국어)")
    quote: str = Field(default="", description="이 속성을 뒷받침하는 원문 문장 (원문 그대로)")


class ExtEntity(BaseModel):
    name: str = Field(description="원문에 쓰인 대표 이름")
    category: Category
    aliases: list[str] = Field(default_factory=list)
    attributes: list[ExtAttr] = Field(default_factory=list)


class ExtRel(BaseModel):
    a: str
    b: str
    type: RelType = "기타·중립"
    label: str = Field(default="", description="세부 관계 (예: 스승, 의붓형제, 비밀 연인)")
    directed: bool = Field(default=False, description="a→b 한쪽에서만 성립하는 관계면 true")
    quote: str = Field(default="", description="근거 원문 문장 (원문 그대로)")


class ExtEvent(BaseModel):
    summary: str = Field(description="한국어 한 문장 요약")
    characters: list[str] = Field(default_factory=list)
    location: str = ""
    quote: str = Field(default="", description="근거 원문 문장 (원문 그대로)")


class SceneExtraction(BaseModel):
    entities: list[ExtEntity] = Field(default_factory=list)
    relations: list[ExtRel] = Field(default_factory=list)
    events: list[ExtEvent] = Field(default_factory=list)


class LoreSummary(BaseModel):
    summary: str = Field(description="3~5줄 한국어 설명. 근거 장면에 없는 내용 금지")


class AskAnswer(BaseModel):
    answer: str
    citations: list[str] = Field(default_factory=list)


# ---- 정규화 후 엔티티 ----
class Character(BaseModel):
    id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    first_scene: str
    scene_count: int
    scene_ids: list[str] = Field(default_factory=list)


class Relation(BaseModel):
    source: str
    target: str
    type: RelType = "기타·중립"
    label: str = ""
    directed: bool = False
    since_chapter: int = 1
    since_scene: str = ""                                  # 관계가 처음 나타난 장면
    evidence_scene_ids: list[str] = Field(default_factory=list)
    evidence_quotes: dict[str, str] = Field(default_factory=dict)


class Event(BaseModel):
    id: str
    summary: str
    scene_id: str
    chapter: int
    characters: list[str] = Field(default_factory=list)
    location: str = ""
    location_id: Optional[str] = None                      # 로어북 장소 항목 id (타임라인 레인)
    quote: str = ""


class LoreAttr(BaseModel):
    key: str
    value: str
    evidence_scene_ids: list[str] = Field(default_factory=list)
    evidence_quotes: dict[str, str] = Field(default_factory=dict)


class LoreEntry(BaseModel):
    id: str
    category: Category
    name: str
    aliases: list[str] = Field(default_factory=list)
    attributes: list[LoreAttr] = Field(default_factory=list)
    summary: str = ""
    first_scene: str
    scene_count: int
    scene_ids: list[str] = Field(default_factory=list)
    related: list[str] = Field(default_factory=list)      # 같은 장면에 함께 나온 항목(lore id)
    character_id: Optional[str] = None                    # 인물 카드는 관계도 노드와 연결
    flags: list[str] = Field(default_factory=list)        # 예: "근거 부족"


class AskRequest(BaseModel):
    question: str


class LlmConfigRequest(BaseModel):
    provider: str
    model: str
    api_key: Optional[str] = None


class AnalysisResult(BaseModel):
    novel_id: str
    provider: str = ""
    model: str = ""
    characters: list[Character]
    relations: list[Relation]
    events: list[Event]
    lore_entries: list[LoreEntry]
    errors: list[str] = Field(default_factory=list)
