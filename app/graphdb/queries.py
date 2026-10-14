"""Cypher 질의 템플릿. LLM 호출 없이 고정 템플릿만 쓰고, 값은 모두 파라미터로 넘긴다."""
from app.graphdb import ontology


def shortest_path() -> str:
    # 두 인물 사이의 최단 관계 경로 (관계 경로 찾기)
    return ("MATCH (a:Character {novel_id: $nid, id: $source_id}), (b:Character {novel_id: $nid, id: $target_id}) "
            f"MATCH p = shortestPath((a)-[:{ontology.rel_type_union()}*..6]-(b)) RETURN p")


def neighbors(hops: int = 2) -> str:
    """질문에 나온 인물들의 1~2단계 이웃 관계(근거 장면 포함)."""
    hops = max(1, min(int(hops), 2))
    return ("MATCH (a:Character {novel_id: $nid}) WHERE a.id IN $ids "
            f"MATCH (a)-[rels:{ontology.rel_type_union()}*1..{hops}]-(:Character) "
            "UNWIND rels AS r "
            "RETURN DISTINCT startNode(r).id AS source, startNode(r).name AS source_name, "
            "endNode(r).id AS target, endNode(r).name AS target_name, "
            "type(r) AS type, r.label AS label, r.evidence_scene_ids AS evidence LIMIT 60")


def hostile_within_two() -> str:
    # 한 인물과 적대 관계로 2단계 이내에 닿는 인물
    return ("MATCH (a:Character {novel_id: $nid, id: $id})-[:HOSTILE*1..2]-(b:Character) RETURN DISTINCT b")


DELETE_NOVEL = "MATCH (n {novel_id: $nid}) DETACH DELETE n"
