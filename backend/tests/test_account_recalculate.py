from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from app.analyzer import Thresholds
from app.core.clock import utc_now
from app.persistence.account_repository import AccountRepository
from app.persistence.database import Database
from app.persistence.probe_repository import ProbeRepository

NORMAL: dict[str, Any] = {}
FAST = {
    "output_tokens": 1500,
    "reasoning_tokens": 100,
    "visible_tokens": 1400,
    "first_token_ms": 900,
    "duration_ms": 1900,
    "generation_ms": 1000,
    "first_token_share": 0.47,
    "tps": 1500.0,
    "upstream_tps": 1500.0,
    "classification": "fast_risk",
    "severity": 4,
}
SHORT = {
    "output_tokens": 6,
    "reasoning_tokens": 0,
    "visible_tokens": 6,
    "first_token_ms": 800,
    "duration_ms": 900,
    "generation_ms": 100,
    "first_token_share": 0.89,
    "tps": 60.0,
    "upstream_tps": 60.0,
    "classification": "insufficient",
    "severity": 0,
}
REASONING_ZERO = {
    "reasoning_tokens": 0,
    "output_tokens": 300,
    "visible_tokens": 300,
    "classification": "reasoning_zero",
    "severity": 3,
}
ERROR = {
    "status": "error",
    "status_code": 0,
    "output_tokens": 0,
    "reasoning_tokens": 0,
    "visible_tokens": 0,
    "chunk_count": 0,
    "first_token_ms": 0,
    "duration_ms": 0,
    "generation_ms": 0,
    "first_token_share": 0.0,
    "tps": 0.0,
    "upstream_tps": 0.0,
    "expected_matched": None,
    "classification": "error",
    "severity": 1,
    "error": "upstream_network_error",
}


def _setup(tmp_path: Path) -> tuple[ProbeRepository, AccountRepository]:
    database = Database(tmp_path / "grokiq.db")
    database.initialize()
    repository = ProbeRepository(database)
    repository.seed_defaults()
    return repository, AccountRepository(database)


def _add_run(
    repository: ProbeRepository,
    samples: list[dict[str, Any]],
    *,
    offset_seconds: int,
    account_id: int = 10,
) -> None:
    run_id = repository.create_run(
        account_id=account_id,
        account_name=f"account-{account_id}",
        account_email="",
        profile_id="quality-marker",
        rounds=len(samples),
        proxy_targets=[{"kind": "current", "id": None, "name": "账号当前出口"}],
        trigger="manual",
        priority=100,
        queue_limit=50,
    )
    base = utc_now() - timedelta(hours=1) + timedelta(seconds=offset_seconds)
    for index, sample in enumerate(samples, start=1):
        values: dict[str, Any] = {
            "round_number": index,
            "target_key": "current",
            "target_kind": "current",
            "egress_node_id": 7,
            "egress_name": "test",
            "status": "done",
            "status_code": 200,
            "output_tokens": 400,
            "reasoning_tokens": 300,
            "reasoning_tokens_reported": True,
            "visible_tokens": 100,
            "chunk_count": 20,
            "first_token_ms": 1000,
            "duration_ms": 7000,
            "generation_ms": 6000,
            "first_token_share": 0.14,
            "tps": 66.0,
            "upstream_tps": 66.0,
            "expected_matched": True,
            "classification": "normal",
            "severity": 0,
            "error": "",
            "created_at": base + timedelta(seconds=index),
        }
        values.update(sample)
        repository.add_sample(run_id, values)
    repository.finish_run(run_id)


def test_normal_retest_clears_consecutive_condition(tmp_path: Path) -> None:
    repository, accounts = _setup(tmp_path)
    _add_run(repository, [FAST, FAST, FAST], offset_seconds=0)

    degraded = accounts.recalculate(10, Thresholds(), 168)
    assert degraded["monitor_status"] == "high_risk"
    assert degraded["anomaly_streak"] == 3

    _add_run(repository, [NORMAL, NORMAL, NORMAL, NORMAL], offset_seconds=60)

    recovered = accounts.recalculate(10, Thresholds(), 168)
    assert recovered["anomaly_streak"] == 0
    assert recovered["sample_count"] == 7
    assert recovered["anomaly_count"] == 3
    # 3/7 is below the cumulative rate, and the trailing streak is gone, so
    # the account is no longer repeated-anomalous even though hard signals
    # remain inside the window.
    assert recovered["monitor_status"] == "watch"


def test_insufficient_samples_stay_out_of_the_anomaly_rate(tmp_path: Path) -> None:
    repository, accounts = _setup(tmp_path)
    _add_run(repository, [FAST, SHORT, SHORT], offset_seconds=0)

    result = accounts.recalculate(10, Thresholds(), 168)
    assert result["sample_count"] == 1
    assert result["anomaly_count"] == 1
    assert result["monitor_status"] == "watch"


def test_reasoning_zero_streak_survives_a_failed_sample(tmp_path: Path) -> None:
    repository, accounts = _setup(tmp_path)
    _add_run(repository, [REASONING_ZERO, ERROR, REASONING_ZERO], offset_seconds=0)

    result = accounts.recalculate(10, Thresholds(), 168)
    assert result["reasoning_zero_count"] == 2
    # The second zero is the second consecutive required gap and is promoted
    # to a hard signal; the error in between carries no evidence.
    assert result["hard_anomaly_count"] == 1
    assert result["anomaly_count"] == 2
    assert result["anomaly_streak"] == 2
    assert "成功请求思考输出为 0 共 2 次" in result["risk_reasons"]
    assert "强降智信号 1 次" in result["risk_reasons"]
