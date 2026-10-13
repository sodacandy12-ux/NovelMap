"""OpenAI 호환 엔드포인트 어댑터 (Google Gemini의 OpenAI 호환 API, 이후 OpenAI 공급사에도 사용)."""
from openai import OpenAI

from app.llm.base import provider_def, schema_for_prompt


def generate(cfg: dict, system: str, prompt: str, schema, effort: str, max_tokens: int) -> str:
    d = provider_def(cfg["provider"]) or {}
    client = OpenAI(api_key=cfg["api_key"], base_url=d.get("base_url"), max_retries=2, timeout=120)
    sys = (system + "\n\n" if system else "") + "Respond with ONLY a JSON object matching this JSON schema:\n" + schema_for_prompt(schema)
    resp = client.chat.completions.create(
        model=cfg["model"], response_format={"type": "json_object"}, max_tokens=max_tokens,
        messages=[{"role": "system", "content": sys}, {"role": "user", "content": prompt}])
    return resp.choices[0].message.content or ""
