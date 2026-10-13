from langgraph.graph import StateGraph, START, END
from app.graph.state import AnalysisState
from app.graph import nodes

NODE_LABELS = {"load_scenes": "장면 로드", "extract": "추출", "normalize": "정규화",
               "lore": "로어북 생성", "validate": "검증", "save": "저장"}


def build_graph():
    g = StateGraph(AnalysisState)
    g.add_node("load_scenes", nodes.load_scenes)
    g.add_node("extract", nodes.extract)
    g.add_node("normalize", nodes.normalize)
    g.add_node("lore", nodes.lore)
    g.add_node("validate", nodes.validate)
    g.add_node("save", nodes.save)
    g.add_edge(START, "load_scenes")
    g.add_conditional_edges("load_scenes", nodes.fan_out, ["extract", "normalize"])   # Send 팬아웃(캐시된 장면은 건너뜀)
    g.add_edge("extract", "normalize")
    g.add_edge("normalize", "lore")
    g.add_edge("lore", "validate")
    g.add_conditional_edges("validate", nodes.route_after_check, ["extract", "save"])  # 유일한 조건부 재시도 엣지
    g.add_edge("save", END)
    return g.compile()
