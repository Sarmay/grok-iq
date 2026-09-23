from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

from app.core.clock import utc_now
from app.core.config import Settings
from app.services.request_audit_service import RequestAuditService


def build_service() -> RequestAuditService:
    return RequestAuditService(
        settings=MagicMock(),
        client=MagicMock(),
        repository=MagicMock(),
    )


def test_request_audit_window_includes_1h_and_3h():
    service = build_service()
    one = service.resolve_window(window_preset="1h")
    three = service.resolve_window(window_preset="3h")
    assert one["preset"] == "1h"
    assert one["label"] == "最近 1 小时"
    assert one["end"] - one["start"] == timedelta(hours=1)
    assert three["preset"] == "3h"
    assert three["label"] == "最近 3 小时"
    assert three["end"] - three["start"] == timedelta(hours=3)


def test_custom_window_allows_end_after_now():
    service = build_service()
    now = utc_now()
    window = service.resolve_window(
        window_preset="custom",
        start_at=now - timedelta(hours=1),
        end_at=now + timedelta(hours=2),
    )
    assert window["preset"] == "custom"
    assert window["end"] > now


def test_repeated_reasoning_zero_keeps_strong_tps_as_primary_rule():
    service = RequestAuditService(
        settings=Settings(_env_file=None),
        client=MagicMock(),
        repository=MagicMock(),
    )
    now = utc_now()
    records = [
        {
            "upstream_id": str(index),
            "account_id": 7,
            "status_code": 200,
            "output_tokens": 600,
            "reasoning_tokens": 0,
            "reasoning_tokens_reported": True,
            "first_token_ms": 100,
            "duration_ms": 1100,
            "tps": 600,
            "model_upstream_model": "Build/grok-4.6",
            "model_public_id": "grok-4.6",
            "operation": "chat",
            "media_input_images": 0,
            "created_at": now + timedelta(seconds=index),
        }
        for index in (1, 2)
    ]

    evaluations = service._audit_risk_evaluations(records)
    latest = evaluations["2"]

    assert latest.classification.name == "high"
    assert latest.classification.rule_id == "fast_risk"
    assert "reasoning_zero" in latest.classification.rule_ids
    assert latest.reasoning_streak == 2


def test_media_input_observe_does_not_auto_disable_for_reasoning_zero():
    service = RequestAuditService(
        settings=Settings(_env_file=None),
        client=MagicMock(),
        repository=MagicMock(),
    )
    now = utc_now()
    records = [
        {
            "upstream_id": str(index),
            "account_id": 5433,
            "status_code": 200,
            "output_tokens": 155,
            "reasoning_tokens": 0,
            "reasoning_tokens_reported": True,
            "first_token_ms": 100,
            "duration_ms": 1100,
            "tps": 1700 + index,
            "model_upstream_model": "Build/grok-4.6",
            "model_public_id": "grok-4.6",
            "operation": "responses",
            "media_input_images": 3,
            "created_at": now + timedelta(seconds=index),
        }
        for index in (1, 2, 3, 4)
    ]

    evaluations = service._audit_risk_evaluations(records)
    latest = evaluations["4"]
    candidates = service._pre_disable_candidates(records, evaluations=evaluations)

    assert latest.classification.rule_id == "media_input_observe"
    assert latest.classification.name == "watch"
    assert latest.classification.hard is False
    assert "reasoning_zero" in latest.classification.rule_ids
    assert latest.reasoning_streak == 0
    assert candidates == []


def _audit_records(*, operation: str, images: int, tps: float, count: int = 4):
    now = utc_now()
    return [
        {
            "upstream_id": str(index),
            "account_id": 7,
            "status_code": 200,
            "output_tokens": 155,
            "reasoning_tokens": 0,
            "reasoning_tokens_reported": True,
            "first_token_ms": 100,
            "duration_ms": 1100,
            "tps": tps,
            "model_upstream_model": "Build/grok-4.6",
            "model_public_id": "grok-4.6",
            "operation": operation,
            "media_input_images": images,
            "created_at": now + timedelta(seconds=index),
        }
        for index in range(1, count + 1)
    ]


def test_required_text_reasoning_zero_still_auto_disables():
    service = RequestAuditService(
        settings=Settings(_env_file=None),
        client=MagicMock(),
        repository=MagicMock(),
    )
    records = _audit_records(operation="chat", images=0, tps=40)
    evaluations = service._audit_risk_evaluations(records)
    latest = evaluations["4"]
    candidates = service._pre_disable_candidates(records, evaluations=evaluations)

    assert latest.classification.rule_id == "reasoning_zero"
    assert latest.classification.name == "high"
    assert latest.classification.hard is True
    assert latest.reasoning_streak == 4
    assert [item.get("_risk_rule_id") for item in candidates] == ["reasoning_zero"]


def test_required_reasoning_streak_survives_failed_rows():
    service = RequestAuditService(
        settings=Settings(_env_file=None),
        client=MagicMock(),
        repository=MagicMock(),
    )
    records = _audit_records(operation="chat", images=0, tps=40, count=3)
    records[1]["status_code"] = 502
    records[1]["output_tokens"] = 0

    evaluations = service._audit_risk_evaluations(records)

    failed = evaluations["2"]
    assert failed.classification.name == "error"
    assert failed.reasoning_streak == 0
    latest = evaluations["3"]
    assert latest.reasoning_streak == 2
    assert latest.classification.name == "high"
    assert latest.classification.rule_id == "reasoning_zero"


def test_required_media_input_reasoning_zero_does_not_auto_disable():
    service = RequestAuditService(
        settings=Settings(_env_file=None),
        client=MagicMock(),
        repository=MagicMock(),
    )
    records = _audit_records(operation="chat", images=3, tps=40)
    evaluations = service._audit_risk_evaluations(records)
    latest = evaluations["4"]
    candidates = service._pre_disable_candidates(records, evaluations=evaluations)

    assert latest.classification.name == "watch"
    assert latest.classification.hard is False
    assert latest.classification.rule_id == "reasoning_zero"
    assert "reasoning_zero" in latest.classification.rule_ids
    assert latest.reasoning_streak == 0
    assert any("不作为隔离或停用依据" in reason for reason in latest.classification.reasons)
    assert candidates == []



def _tps_row(tps: float, model: str) -> dict[str, object]:
    return {
        "upstream_id": f"{model}-{tps}",
        "account_id": 7,
        "status_code": 200,
        "output_tokens": 2000,
        "reasoning_tokens": 800,
        "reasoning_tokens_reported": True,
        "first_token_ms": 1500,
        "duration_ms": 12_000,
        "tps": tps,
        "model_upstream_model": model,
        "operation": "chat",
        "created_at": utc_now(),
    }


def test_peak_tps_reason_uses_each_rows_model_thresholds():
    service = RequestAuditService(
        settings=Settings(
            _env_file=None, degradation_tps=150, strong_degradation_tps=240
        ),
        client=MagicMock(),
        repository=MagicMock(),
    )

    assert service._risk_reasons([_tps_row(234, "Build/grok-4.7")]) == []
    assert service._risk_reasons(
        [_tps_row(234, "Build/grok-4.7"), _tps_row(180, "Build/grok-4.5")]
    ) == ["峰值 180.0 Token/s ≥ 150 TPS"]
    assert service._risk_reasons([_tps_row(700, "Build/grok-4.7")]) == [
        "峰值 700.0 Token/s ≥ 600 TPS"
    ]


def test_healthy_grok_4_7_traffic_does_not_force_busy_scan():
    repository = MagicMock()
    repository.records_for_range.return_value = [_tps_row(234, "Build/grok-4.7")]
    service = RequestAuditService(
        settings=Settings(
            _env_file=None, degradation_tps=150, strong_degradation_tps=240
        ),
        client=MagicMock(),
        repository=repository,
    )

    assert service._activity_payload()["level"] == "normal"

    repository.records_for_range.return_value = [_tps_row(180, "Build/grok-4.5")]
    assert service._activity_payload()["level"] == "busy"
