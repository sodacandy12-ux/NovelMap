"""LLM 공급사 어댑터 공통부: 설정(서버 메모리), generate_json(prompt, schema) 단일 진입점.

API 키는 이 모듈의 메모리(_CONFIG)에만 있고 파일·로그·JSON 캐시·LangGraph State에 쓰지 않는다.
"""
import json
import os
import re
import threading
from pathlib import Path
from typing import Optional, Type

from pydantic import BaseModel, ValidationError

MODELS_JSON = Path(__file__).with_name("models.json")
PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


def prompt(name: str) -> str:
    return (PROMPTS / f"{name}.txt").read_text(encoding="utf-8")


class LlmError(Exception):
    """사용자에게 보여줘도 안전한(키가 제거된) LLM 오류."""


_lock = threading.Lock()
_CONFIG: dict = {"provider": None, "model": None, "api_key": None}
_sem: Optional[threading.BoundedSemaphore] = None


def load_models() -> dict:
    return json.loads(MODELS_JSON.read_text(encoding="utf-8"))


def provider_def(pid: str) -> Optional[dict]:
    return next((p for p in load_models()["providers"] if p["id"] == pid), None)


def _env_key(pid: str) -> Optional[str]:
    d = provider_def(pid) or {}
    for name in d.get("env_key", []):
        if os.getenv(name):
            return os.getenv(name)
    return None


def current() -> dict:
    """현재 공급사·모델·키(메모리 우선, 없으면 환경변수)."""
    with _lock:
        pid = _CONFIG["provider"] or load_models()["default"]["provider"]
        model = _CONFIG["model"] or (load_models()["default"]["model"] if pid == load_models()["default"]["provider"]
                                      else ((provider_def(pid) or {}).get("models") or [{}])[0].get("id"))
        mem = _CONFIG["api_key"] if _CONFIG["provider"] == pid else None
    key = mem or _env_key(pid)
    return {"provider": pid, "model": model, "api_key": key, "key_source": "memory" if mem else ("env" if key else "none")}


def is_configured() -> bool:
    return bool(current()["api_key"])


def set_config(provider: str, model: str, api_key: Optional[str]):
    with _lock:
        _CONFIG.update(provider=provider, model=model, api_key=api_key or (_CONFIG["api_key"] if _CONFIG["provider"] == provider else None))


def clear_key():
    with _lock:
        _CONFIG["api_key"] = None


def providers_info() -> dict:
    cur = current()
    out = []
    for p in load_models()["providers"]:
        out.append({"id": p["id"], "name": p["name"], "ready": bool(p.get("ready") and p.get("models")),
                    "models": p.get("models", []), "key_in_env": bool(_env_key(p["id"]))})
    return {"providers": out, "current": {"provider": cur["provider"], "model": cur["model"],
                                          "configured": bool(cur["api_key"]), "key_source": cur["key_source"]}}


def scrub(msg: str, key: Optional[str]) -> str:
    msg = str(msg)
    if key:
        msg = msg.replace(key, "***")
    return re.sub(r"(sk-[A-Za-z0-9_\-]{8,}|AIza[0-9A-Za-z_\-]{20,})", "***", msg)[:400]


def concurrency() -> int:
    try:
        return max(1, int(os.getenv("LLM_CONCURRENCY", "8" if current()["provider"] == "anthropic" else "2")))
    except ValueError:
        return 4


def _semaphore():
    global _sem
    if _sem is None:
        _sem = threading.BoundedSemaphore(concurrency())
    return _sem


def schema_for_prompt(model: Type[BaseModel]) -> str:
    return json.dumps(model.model_json_schema(), ensure_ascii=False)


def parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text.strip(), re.S)
    if not m:
        raise ValueError("JSON 없음")
    return json.loads(m.group(0))


def generate_json(prompt: str, schema: Type[BaseModel], system: str = "", effort: str = "low",
                  max_tokens: int = 4000) -> dict:
    """모든 노드가 거치는 단일 호출. 결과는 Pydantic으로 검증하고 dict로 돌려준다."""
    from app.llm import anthropic as a_mod, openai_compat as o_mod
    cfg = current()
    if not cfg["api_key"]:
        raise LlmError("API 키가 설정되지 않았습니다.")
    adapter = a_mod.generate if cfg["provider"] == "anthropic" else o_mod.generate
    last = None
    for _ in range(2):   # 구조화 출력 JSON 오류는 어댑터 안에서 1회 더 시도
        try:
            with _semaphore():
                raw = adapter(cfg, system, prompt, schema, effort, max_tokens)
            return schema.model_validate(parse_json(raw)).model_dump()
        except (ValidationError, ValueError) as e:
            last = e
        except LlmError:
            raise
        except Exception as e:  # noqa  SDK 예외: 키 제거 후 전달
            raise LlmError(scrub(f"{type(e).__name__}: {e}", cfg["api_key"])) from None
    raise LlmError(f"구조화 출력 검증 실패: {str(last)[:200]}")
