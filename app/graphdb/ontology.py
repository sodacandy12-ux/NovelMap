"""ontology.yaml 로더. 클래스·관계 타입 화이트리스트와 한국어 라벨 매핑을 제공한다."""
import re
from functools import lru_cache
from pathlib import Path

import yaml

_SAFE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")     # Cypher에 문자열로 끼워 넣는 식별자는 이 형식만 허용


@lru_cache(maxsize=1)
def load() -> dict:
    data = yaml.safe_load((Path(__file__).with_name("ontology.yaml")).read_text(encoding="utf-8"))
    for name in [*data["classes"], *data["relations"]]:
        if not _SAFE.match(name):
            raise ValueError(f"온톨로지 이름이 안전하지 않습니다: {name}")
    return data


def classes() -> set[str]:
    return set(load()["classes"])


def class_of_category(category_ko: str) -> str | None:
    return load()["categories"].get(category_ko)


def character_relations() -> dict[str, str]:
    """인물 관계 대분류: {한국어 라벨: 관계 타입}. (Character -> Character 관계만)"""
    return {v["label_ko"]: k for k, v in load()["relations"].items()
            if v.get("from") == "Character" and v.get("to") == "Character" and "label_ko" in v}


def rel_type_union() -> str:
    """Cypher 관계 패턴용 'FAMILY|ROMANCE|...'"""
    return "|".join(character_relations().values())


def is_allowed_relation(label_ko: str) -> bool:
    return label_ko in character_relations()
