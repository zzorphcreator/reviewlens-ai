from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

try:
    from langsmith import traceable as _langsmith_traceable
except ImportError:  # pragma: no cover - optional dependency fallback.
    _langsmith_traceable = None


F = TypeVar("F", bound=Callable[..., Any])


def traceable(*, name: str, run_type: str = "chain") -> Callable[[F], F]:
    if _langsmith_traceable is None:
        return lambda func: func

    try:
        return _langsmith_traceable(
            name=name,
            run_type=run_type,
            process_inputs=redact_inputs,
            process_outputs=redact_outputs,
        )
    except TypeError:
        # Older langsmith versions do not expose redaction hooks on traceable.
        return _langsmith_traceable(name=name, run_type=run_type)


def redact_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(inputs)
    if "settings" in redacted:
        settings = redacted["settings"]
        redacted["settings"] = {
            "openai_chat_model": getattr(settings, "openai_chat_model", None),
            "anthropic_fallback_models": getattr(settings, "anthropic_fallback_models", None),
            "embedding_model": getattr(settings, "embedding_model", None),
            "embedding_dimensions": getattr(settings, "embedding_dimensions", None),
            "rag_top_k": getattr(settings, "rag_top_k", None),
        }
    return redacted


def redact_outputs(outputs: Any) -> Any:
    if _looks_like_vectors(outputs):
        return {
            "vector_count": len(outputs),
            "dimensions": len(outputs[0]) if outputs else 0,
        }
    if isinstance(outputs, list):
        return [redact_outputs(item) for item in outputs[:20]]
    if isinstance(outputs, dict):
        redacted = dict(outputs)
        if isinstance(redacted.get("content"), str):
            redacted["content"] = redacted["content"][:500]
        return redacted
    return outputs


def _looks_like_vectors(value: Any) -> bool:
    return (
        isinstance(value, list)
        and (not value or isinstance(value[0], list))
        and (not value or not value[0] or isinstance(value[0][0], (float, int)))
    )
