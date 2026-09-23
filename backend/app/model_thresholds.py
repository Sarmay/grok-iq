from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from app.reasoning_policy import canonical_reasoning_model

MAX_MODEL_TPS_THRESHOLDS = 50


@dataclass(slots=True, frozen=True)
class ModelTpsThreshold:
    """TPS thresholds that replace the global values for one upstream model."""

    model: str
    degradation_tps: float
    strong_degradation_tps: float

    def public_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "degradationTps": self.degradation_tps,
            "strongDegradationTps": self.strong_degradation_tps,
        }


# Healthy grok-4.7 traffic always carries reasoning and runs roughly 110-235
# TPS (p95 ~180), while degraded accounts run 700+ TPS.  The global 150/240
# values tuned for grok-4.5 (55-75 TPS) would flag a large share of healthy
# grok-4.7 requests, so the model gets its own band.
DEFAULT_MODEL_TPS_THRESHOLDS: tuple[ModelTpsThreshold, ...] = (
    ModelTpsThreshold("Build/grok-4.7", 300, 600),
)


def default_model_tps_thresholds() -> list[dict[str, Any]]:
    return [item.public_dict() for item in DEFAULT_MODEL_TPS_THRESHOLDS]


def _value(raw: Mapping[str, Any], snake: str, camel: str) -> Any:
    if snake in raw:
        return raw[snake]
    return raw.get(camel)


def _positive_number(value: Any, *, model: str, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"模型 TPS 阈值的{label}无效: {model}")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"模型 TPS 阈值的{label}无效: {model}") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"模型 TPS 阈值的{label}必须大于 0: {model}")
    return number


def normalize_model_tps_thresholds(
    values: Iterable[Mapping[str, Any]] | None,
) -> tuple[ModelTpsThreshold, ...]:
    """Validate per-model TPS thresholds.

    ``None`` means "not configured" and yields the built-in defaults; an
    explicit empty list disables every model-specific override.
    """

    source = default_model_tps_thresholds() if values is None else list(values)
    if len(source) > MAX_MODEL_TPS_THRESHOLDS:
        raise ValueError(
            f"模型 TPS 阈值最多 {MAX_MODEL_TPS_THRESHOLDS} 条"
        )
    result: list[ModelTpsThreshold] = []
    seen: set[str] = set()
    for raw in source:
        if not isinstance(raw, Mapping):
            raise ValueError("模型 TPS 阈值项必须是对象")
        model = str(raw.get("model") or "").strip()
        if not model:
            raise ValueError("模型 TPS 阈值缺少上游模型")
        if len(model) > 255:
            raise ValueError("模型 TPS 阈值的上游模型不能超过 255 个字符")
        key = canonical_reasoning_model(model)
        if key == "*":
            raise ValueError("模型 TPS 阈值不支持通配模型，请修改全局 TPS 阈值")
        if key in seen:
            raise ValueError(f"模型 TPS 阈值重复: {model}")
        degradation = _positive_number(
            _value(raw, "degradation_tps", "degradationTps"),
            model=model,
            label="降智信号 TPS",
        )
        strong = _positive_number(
            _value(raw, "strong_degradation_tps", "strongDegradationTps"),
            model=model,
            label="强降智信号 TPS",
        )
        if degradation >= strong:
            raise ValueError(
                f"模型 TPS 阈值的降智信号 TPS 必须小于强降智信号 TPS: {model}"
            )
        seen.add(key)
        result.append(ModelTpsThreshold(model, degradation, strong))
    return tuple(result)


def resolve_model_tps_threshold(
    entries: Iterable[ModelTpsThreshold],
    *,
    model_upstream_model: str = "",
    model_public_id: str = "",
) -> ModelTpsThreshold | None:
    """Return the override for a sample's model, matching Build/ and bare ids."""

    model = canonical_reasoning_model(model_upstream_model or model_public_id)
    if not model or model == "*":
        return None
    for entry in entries:
        if canonical_reasoning_model(entry.model) == model:
            return entry
    return None
