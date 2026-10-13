"""Anthropic 어댑터 (Claude Haiku 5.5 기본, Sonnet 5.5 선택)."""
import json

import anthropic

from app.llm.base import LlmError, schema_for_prompt


def _clean_schema(node):
    """구조화 출력용 스키마 정리: $defs 인라인, additionalProperties=false, 미지원 키워드 제거."""
    defs = node.get("$defs", {}) if isinstance(node, dict) else {}

    def walk(n):
        if isinstance(n, dict):
            if "$ref" in n:
                return walk(defs[n["$ref"].split("/")[-1]])
            out = {k: walk(v) for k, v in n.items() if k not in ("$defs", "title", "default", "description")}
            if out.get("type") == "object":
                out["additionalProperties"] = False
                out["required"] = list(out.get("properties", {}).keys())
            return out
        if isinstance(n, list):
            return [walk(x) for x in n]
        return n

    return walk(node)


def generate(cfg: dict, system: str, prompt: str, schema, effort: str, max_tokens: int) -> str:
    client = anthropic.Anthropic(api_key=cfg["api_key"], max_retries=6)   # 429·5xx는 SDK가 지수 대기 후 재시도
    model = cfg["model"]
    kwargs = dict(model=model, max_tokens=max_tokens,
                  messages=[{"role": "user", "content": prompt}])
    if system:
        kwargs["system"] = system
    # temperature 등 샘플링 파라미터는 보내지 않는다(Haiku 5.5는 오류). 생각 모드는 추출에서 끈다.
    kwargs["thinking"] = {"type": "between_tools"} if "sonnet" in model else {"type": "disabled"}
    out_cfg = {"effort": effort}
    try:
        resp = client.messages.create(
            **kwargs, output_config={**out_cfg, "format": {"type": "json_schema", "schema": _clean_schema(schema.model_json_schema())}})
    except anthropic.BadRequestError as e:
        if "schema" not in str(e).lower() and "format" not in str(e).lower():
            raise
        # 스키마 미지원 시: 스키마를 프롬프트에 넣고 JSON만 요청
        kwargs["system"] = (system + "\n\n" if system else "") + \
            "Respond with ONLY a JSON object matching this JSON schema:\n" + schema_for_prompt(schema)
        resp = client.messages.create(**kwargs, output_config=out_cfg)
    if resp.stop_reason == "refusal":
        raise LlmError("모델이 요청을 거절했습니다(refusal).")
    if resp.stop_reason == "max_tokens":
        raise ValueError("출력이 max_tokens에서 잘렸습니다.")
    return "".join(b.text for b in resp.content if b.type == "text")
