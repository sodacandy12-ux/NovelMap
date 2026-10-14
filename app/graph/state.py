from typing import Annotated, TypedDict
from app.schemas import Scene, Character, Relation, Event, LoreEntry


def merge_dict(a: dict, b: dict) -> dict:
    return {**(a or {}), **(b or {})}


class AnalysisState(TypedDict, total=False):
    novel_id: str
    fresh: bool                                          # True면 장면별 추출 캐시를 무시
    scenes: list[Scene]                                  # 장면 로드 결과 (order 순)
    extractions: Annotated[dict, merge_dict]             # scene_id -> SceneExtraction(dict), 재시도 시 덮어씀
    scene_errors: Annotated[dict, merge_dict]            # scene_id -> 추출 실패 사유 ("" = 정상)
    characters: list[Character]
    relations: list[Relation]
    events: list[Event]
    lore_entries: list[LoreEntry]                        # 로어북 항목(요약 포함)
    errors: list[str]                                    # 검증 실패 사유
    retry_count: int                                     # 재시도 횟수, 최대 2
    graph_db: str                                        # Neo4j 적재 결과: loaded / skipped(사유)
