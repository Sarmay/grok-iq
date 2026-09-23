from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from app.core.clock import utc_now
from app.core.config import Settings
from app.services.register_integration import (
    RegisterIntegrationService,
    is_confirmed_register_degradation,
)


class FakeGrokClient:
    def __init__(self) -> None:
        self.priorities: dict[int, int] = {}

    async def set_account_priority(self, account_id: int, priority: int) -> dict[str, Any]:
        self.priorities[int(account_id)] = int(priority)
        return {"id": int(account_id), "priority": int(priority)}


class RegisterRepository:
    def __init__(self) -> None:
        self.completed: tuple[str, int, list[str]] | None = None
        self.retried: tuple[str, str, float] | None = None
        self.failed: tuple[str, str] | None = None
        self.bound: tuple[str, int] | None = None
        self.events: dict[str, dict[str, Any]] = {}
        self.callbacks: dict[str, dict[str, Any]] = {}

    def bind_account(self, event_id: str, account_id: int) -> None:
        self.bound = (event_id, account_id)
        event = self.events.setdefault(event_id, {"event_id": event_id})
        event["resolved_account_id"] = account_id
        event.setdefault("status", "processing")

    def complete(self, event_id: str, account_id: int, run_ids: list[str]) -> None:
        self.completed = (event_id, account_id, run_ids)
        event = self.events.setdefault(event_id, {"event_id": event_id})
        event["status"] = "completed"
        event["resolved_account_id"] = account_id
        event["run_ids"] = list(run_ids)

    def retry(self, event_id: str, error: str, delay_seconds: float) -> None:
        self.retried = (event_id, error, delay_seconds)

    def fail(self, event_id: str, error: str) -> None:
        self.failed = (event_id, error)
        event = self.events.setdefault(event_id, {"event_id": event_id})
        event["status"] = "failed"
        event["last_error"] = error

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        event = self.events.get(event_id)
        return dict(event) if event is not None else None

    def list_unresolved_priority_holds(self) -> list[dict[str, Any]]:
        return [
            dict(event)
            for event in self.events.values()
            if event.get("priority_hold_status") in {"held", "restore_failed"}
        ]

    def mark_priority_hold(
        self,
        event_id: str,
        *,
        original_priority: int,
        held_priority: int,
    ) -> dict[str, Any]:
        event = self.events.setdefault(event_id, {"event_id": event_id})
        if event.get("priority_hold_status") in {"restored", "kept"}:
            return dict(event)
        if event.get("original_priority") is None:
            event["original_priority"] = original_priority
        event["held_priority"] = held_priority
        event["priority_hold_status"] = "held"
        event["priority_hold_error"] = ""
        if self.bound is not None:
            event["resolved_account_id"] = self.bound[1]
        return dict(event)

    def mark_priority_restored(self, event_id: str) -> None:
        event = self.events.setdefault(event_id, {"event_id": event_id})
        event["priority_hold_status"] = "restored"
        event["priority_hold_error"] = ""

    def mark_priority_restore_failed(self, event_id: str, error: str) -> None:
        event = self.events.setdefault(event_id, {"event_id": event_id})
        event["priority_hold_status"] = "restore_failed"
        event["priority_hold_error"] = error

    def mark_priority_kept(self, event_id: str, error: str = "") -> None:
        event = self.events.setdefault(event_id, {"event_id": event_id})
        event["priority_hold_status"] = "kept"
        event["priority_hold_error"] = error

    def enqueue_callback(self, event_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        existing = self.callbacks.get(event_id)
        if existing and existing.get("status") == "delivered":
            return dict(existing)
        row = {
            "event_id": event_id,
            "status": "pending",
            "attempts": int((existing or {}).get("attempts") or 0),
            "payload": dict(payload),
            "last_error": "",
        }
        self.callbacks[event_id] = row
        return dict(row)

    def recover_callback_processing(self) -> int:
        return 0

    def claim_callback_due(self) -> dict[str, Any] | None:
        for row in self.callbacks.values():
            if row.get("status") == "pending":
                row["status"] = "processing"
                row["attempts"] = int(row.get("attempts") or 0) + 1
                return dict(row)
        return None

    def retry_callback(self, event_id: str, error: str, delay_seconds: float) -> None:
        row = self.callbacks.setdefault(event_id, {"event_id": event_id})
        row["status"] = "pending"
        row["last_error"] = error
        row["delay_seconds"] = delay_seconds

    def fail_callback(self, event_id: str, error: str) -> None:
        row = self.callbacks.setdefault(event_id, {"event_id": event_id})
        row["status"] = "failed"
        row["last_error"] = error

    def complete_callback(self, event_id: str) -> None:
        row = self.callbacks.setdefault(event_id, {"event_id": event_id})
        row["status"] = "delivered"
        row["last_error"] = ""

    def get_callback(self, event_id: str) -> dict[str, Any] | None:
        row = self.callbacks.get(event_id)
        return dict(row) if row is not None else None


class RegisterAccountService:
    def __init__(self) -> None:
        self.auto_bound = False
        self.client = FakeGrokClient()
        self.account_priority = 8
        self.quarantines: list[dict[str, Any]] = []

    async def apply_auto_quarantine(self, account_id: int, **values: Any) -> dict[str, Any]:
        payload = {"accountId": int(account_id), **values, "actionStatus": "disabled"}
        self.quarantines.append(payload)
        return payload

    async def find_registered_account(
        self,
        account_id: int | None,
        email: str,
    ) -> dict[str, Any]:
        resolved_id = int(account_id or 17)
        return {
            "id": str(resolved_id),
            "email": email,
            "enabled": True,
            "authStatus": "active",
            "egressNodeId": None,
            "priority": self.client.priorities.get(resolved_id, self.account_priority),
        }

    async def ensure_account_egress(self, account: dict[str, Any]) -> dict[str, Any]:
        self.auto_bound = True
        return {
            **account,
            "egressNodeId": "3",
            "egressAssignmentMode": "auto",
        }


class FakeProbeRepository:
    def __init__(self) -> None:
        self.runs: list[dict[str, Any]] = []

    def list_runs_for_source_event(self, source_event_id: str) -> list[dict[str, Any]]:
        return [
            dict(run)
            for run in self.runs
            if str(run.get("source_event_id") or "") == source_event_id
        ]


class RegisterProbeManager:
    def __init__(self) -> None:
        self.account: dict[str, Any] | None = None
        self.values: dict[str, Any] | None = None
        self.repository = FakeProbeRepository()

    async def enqueue_register_event(self, **values: Any) -> dict[str, Any]:
        self.values = values
        self.account = values["account"]
        return {"runIds": ["run-1"]}


class UnusedAccountRepository:
    pass


class FakeAccountRepository:
    def __init__(self) -> None:
        self.marked: list[dict[str, Any]] = []
        self.assessments: dict[int, dict[str, Any]] = {}

    def get_assessment(self, account_id: int) -> dict[str, Any] | None:
        return self.assessments.get(int(account_id))

    def mark_registration_risk(self, **values: Any) -> dict[str, Any]:
        self.marked.append(values)
        assessment = {
            "account_id": int(values["account_id"]),
            "monitor_status": "high_risk",
            "risk_score": 85,
            "risk_reasons": [f"grok-register 报告 bot_risk/bfs={values.get('bfs')}"],
        }
        self.assessments[int(values["account_id"])] = assessment
        return assessment


@pytest.mark.asyncio
async def test_webhook_auto_binds_before_enqueue():
    settings = Settings(
        initial_probe_on_register=True,
        register_probe_profile_ids=["profile-a", "profile-b"],
        register_probe_execution_mode="quality_test",
        register_probe_rounds=9,
        register_probe_proxy_targets=[{"kind": "egress", "id": 7}],
    )
    repository = RegisterRepository()
    account_service = RegisterAccountService()
    probes = RegisterProbeManager()
    service = RegisterIntegrationService(
        settings=settings,
        repository=repository,  # type: ignore[arg-type]
        accounts=UnusedAccountRepository(),  # type: ignore[arg-type]
        account_service=account_service,  # type: ignore[arg-type]
        probes=probes,  # type: ignore[arg-type]
    )

    await service._process_claimed(
        {
            "event_id": "event-1",
            "attempts": 1,
            "grok2api_account_id": 17,
            "email": "new@example.test",
            "bot_risk": False,
        }
    )

    assert account_service.auto_bound is True
    assert probes.account is not None
    assert probes.account["egressNodeId"] == "3"
    assert probes.values is not None
    assert probes.values["profile_ids"] == ["profile-a", "profile-b"]
    assert probes.values["execution_mode"] == "chat"
    assert probes.values["rounds"] == {"profile-a": 9, "profile-b": 9}
    assert probes.values["proxy_targets"] == [{"kind": "current", "id": None}]
    assert repository.completed == ("event-1", 17, ["run-1"])
    assert repository.bound == ("event-1", 17)


@pytest.mark.asyncio
async def test_webhook_uses_per_profile_register_probe_rounds():
    settings = Settings(
        initial_probe_on_register=True,
        register_probe_stabilization_seconds=0,
        register_probe_profile_ids=["profile-a", "profile-b"],
        register_probe_rounds=3,
        register_probe_profile_rounds={"profile-a": 1, "profile-b": 4},
    )
    repository = RegisterRepository()
    account_service = RegisterAccountService()
    probes = RegisterProbeManager()
    service = RegisterIntegrationService(
        settings=settings,
        repository=repository,  # type: ignore[arg-type]
        accounts=UnusedAccountRepository(),  # type: ignore[arg-type]
        account_service=account_service,  # type: ignore[arg-type]
        probes=probes,  # type: ignore[arg-type]
    )

    await service._process_claimed(
        {
            "event_id": "event-profile-rounds",
            "attempts": 1,
            "grok2api_account_id": 17,
            "email": "new@example.test",
            "bot_risk": False,
        }
    )

    assert probes.values is not None
    assert probes.values["profile_ids"] == ["profile-a", "profile-b"]
    assert probes.values["rounds"] == {"profile-a": 1, "profile-b": 4}


@pytest.mark.asyncio
async def test_webhook_defers_probe_during_new_account_stabilization():
    stabilization_seconds = 15
    settings = Settings(
        initial_probe_on_register=True,
        register_probe_stabilization_seconds=stabilization_seconds,
    )
    repository = RegisterRepository()
    account_service = RegisterAccountService()
    probes = RegisterProbeManager()
    service = RegisterIntegrationService(
        settings=settings,
        repository=repository,  # type: ignore[arg-type]
        accounts=UnusedAccountRepository(),  # type: ignore[arg-type]
        account_service=account_service,  # type: ignore[arg-type]
        probes=probes,  # type: ignore[arg-type]
    )

    await service._process_claimed(
        {
            "event_id": "event-new-account",
            "attempts": 1,
            "created_at": utc_now(),
            "grok2api_account_id": 17,
            "email": "new@example.test",
            "bot_risk": False,
        }
    )

    assert repository.completed is None
    assert repository.failed is None
    assert repository.retried is not None
    assert repository.retried[0] == "event-new-account"
    assert "模型权限传播" in repository.retried[1]
    assert 1 <= repository.retried[2] <= stabilization_seconds
    assert account_service.auto_bound is False
    assert probes.values is None
    assert repository.bound == ("event-new-account", 17)


@pytest.mark.asyncio
async def test_webhook_can_disable_new_account_stabilization():
    settings = Settings(
        initial_probe_on_register=True,
        register_probe_stabilization_seconds=0,
    )
    repository = RegisterRepository()
    account_service = RegisterAccountService()
    probes = RegisterProbeManager()
    service = RegisterIntegrationService(
        settings=settings,
        repository=repository,  # type: ignore[arg-type]
        accounts=UnusedAccountRepository(),  # type: ignore[arg-type]
        account_service=account_service,  # type: ignore[arg-type]
        probes=probes,  # type: ignore[arg-type]
    )

    await service._process_claimed(
        {
            "event_id": "event-no-stabilization",
            "attempts": 1,
            "created_at": utc_now(),
            "grok2api_account_id": 17,
            "email": "new@example.test",
            "bot_risk": False,
        }
    )

    assert repository.retried is None
    assert repository.completed == ("event-no-stabilization", 17, ["run-1"])


@pytest.mark.asyncio
async def test_webhook_uses_longest_initial_readiness_delay():
    settings = Settings(
        initial_probe_on_register=True,
        register_probe_stabilization_seconds=15,
    )
    repository = RegisterRepository()
    account_service = RegisterAccountService()
    probes = RegisterProbeManager()
    service = RegisterIntegrationService(
        settings=settings,
        repository=repository,  # type: ignore[arg-type]
        accounts=UnusedAccountRepository(),  # type: ignore[arg-type]
        account_service=account_service,  # type: ignore[arg-type]
        probes=probes,  # type: ignore[arg-type]
    )

    original_find = account_service.find_registered_account

    async def cooling_account(account_id: int | None, email: str) -> dict[str, Any]:
        account = await original_find(account_id, email)
        return {
            **account,
            "cooldownUntil": (utc_now() + timedelta(seconds=40)).isoformat(),
        }

    account_service.find_registered_account = cooling_account  # type: ignore[method-assign]
    await service._process_claimed(
        {
            "event_id": "event-longest-readiness-delay",
            "attempts": 1,
            "created_at": utc_now(),
            "grok2api_account_id": 17,
            "email": "new@example.test",
            "bot_risk": False,
        }
    )

    assert repository.retried is not None
    assert "冷却" in repository.retried[1]
    assert 39 <= repository.retried[2] <= 40


@pytest.mark.asyncio
async def test_webhook_defers_probe_until_existing_account_cooldown_ends():
    settings = Settings(initial_probe_on_register=True)
    repository = RegisterRepository()
    account_service = RegisterAccountService()
    probes = RegisterProbeManager()
    service = RegisterIntegrationService(
        settings=settings,
        repository=repository,  # type: ignore[arg-type]
        accounts=UnusedAccountRepository(),  # type: ignore[arg-type]
        account_service=account_service,  # type: ignore[arg-type]
        probes=probes,  # type: ignore[arg-type]
    )

    original_find = account_service.find_registered_account

    async def cooling_account(account_id: int | None, email: str) -> dict[str, Any]:
        account = await original_find(account_id, email)
        return {
            **account,
            "cooldownUntil": (utc_now() + timedelta(seconds=40)).isoformat(),
        }

    account_service.find_registered_account = cooling_account  # type: ignore[method-assign]
    await service._process_claimed(
        {
            "event_id": "event-cooling-account",
            "attempts": 2,
            "grok2api_account_id": 17,
            "email": "new@example.test",
            "bot_risk": False,
        }
    )

    assert repository.retried is not None
    assert repository.retried[0] == "event-cooling-account"
    assert "冷却" in repository.retried[1]
    assert 39 <= repository.retried[2] <= 40
    assert probes.values is None


def _service(
    *,
    settings: Settings | None = None,
    repository: RegisterRepository | None = None,
    account_service: RegisterAccountService | None = None,
    probes: RegisterProbeManager | None = None,
) -> tuple[RegisterIntegrationService, RegisterRepository, RegisterAccountService, RegisterProbeManager]:
    repository = repository or RegisterRepository()
    account_service = account_service or RegisterAccountService()
    probes = probes or RegisterProbeManager()
    service = RegisterIntegrationService(
        settings=settings or Settings(initial_probe_on_register=True),
        repository=repository,  # type: ignore[arg-type]
        accounts=UnusedAccountRepository(),  # type: ignore[arg-type]
        account_service=account_service,  # type: ignore[arg-type]
        probes=probes,  # type: ignore[arg-type]
    )
    return service, repository, account_service, probes


def test_confirmed_register_degradation_only_for_bfs_1_or_2():
    assert is_confirmed_register_degradation(bot_risk=True, bfs=1) is True
    assert is_confirmed_register_degradation(bot_risk=True, bfs="2") is True
    assert is_confirmed_register_degradation(bot_risk=True, bfs=3) is False
    assert is_confirmed_register_degradation(bot_risk=False, bfs=1) is False
    assert is_confirmed_register_degradation(bot_risk=True, bfs="") is False


@pytest.mark.asyncio
async def test_webhook_holds_priority_before_stabilization_wait():
    service, repository, account_service, probes = _service(
        settings=Settings(
            initial_probe_on_register=True,
            register_probe_stabilization_seconds=15,
            register_priority_hold_enabled=True,
            register_priority_hold=-1000,
        )
    )

    await service._process_claimed(
        {
            "event_id": "event-hold-wait",
            "attempts": 1,
            "created_at": utc_now(),
            "grok2api_account_id": 17,
            "email": "new@example.test",
            "bot_risk": False,
        }
    )

    assert repository.retried is not None
    assert probes.values is None
    assert account_service.client.priorities[17] == -1000
    held = repository.get_event("event-hold-wait")
    assert held is not None
    assert held["priority_hold_status"] == "held"
    assert held["original_priority"] == 8
    assert held["held_priority"] == -1000


@pytest.mark.asyncio
async def test_webhook_skips_priority_hold_when_disabled():
    service, repository, account_service, probes = _service(
        settings=Settings(
            initial_probe_on_register=True,
            register_probe_stabilization_seconds=0,
            register_priority_hold_enabled=False,
        )
    )

    await service._process_claimed(
        {
            "event_id": "event-no-hold",
            "attempts": 1,
            "grok2api_account_id": 17,
            "email": "new@example.test",
            "bot_risk": False,
        }
    )

    assert repository.completed == ("event-no-hold", 17, ["run-1"])
    assert account_service.client.priorities == {}
    assert repository.get_event("event-no-hold") is None or repository.get_event(
        "event-no-hold"
    ).get("priority_hold_status") not in {"held", "restore_failed"}


@pytest.mark.asyncio
async def test_priority_hold_restores_after_register_probes_pass():
    service, repository, account_service, probes = _service(
        settings=Settings(
            initial_probe_on_register=True,
            register_probe_stabilization_seconds=0,
            register_priority_hold=-500,
        )
    )
    await service._process_claimed(
        {
            "event_id": "event-restore",
            "attempts": 1,
            "grok2api_account_id": 17,
            "email": "new@example.test",
            "bot_risk": False,
        }
    )
    assert account_service.client.priorities[17] == -500
    probes.repository.runs = [
        {
            "id": "run-1",
            "source_event_id": "event-restore",
            "status": "completed",
            "summary": {"anomaly_count": 0},
        }
    ]

    await service.maybe_restore_priority_hold(
        {"source_event_id": "event-restore", "id": "run-1"}
    )

    assert account_service.client.priorities[17] == 8
    restored = repository.get_event("event-restore")
    assert restored is not None
    assert restored["priority_hold_status"] == "restored"


@pytest.mark.asyncio
async def test_priority_hold_keeps_low_priority_when_probe_fails():
    service, repository, account_service, probes = _service(
        settings=Settings(
            initial_probe_on_register=True,
            register_probe_stabilization_seconds=0,
            register_priority_hold=-500,
        )
    )
    await service._process_claimed(
        {
            "event_id": "event-keep",
            "attempts": 1,
            "grok2api_account_id": 17,
            "email": "new@example.test",
            "bot_risk": False,
        }
    )
    probes.repository.runs = [
        {
            "id": "run-1",
            "source_event_id": "event-keep",
            "status": "failed",
            "summary": {"anomaly_count": 1},
        }
    ]

    await service.maybe_restore_priority_hold(
        {"source_event_id": "event-keep", "id": "run-1"}
    )

    assert account_service.client.priorities[17] == -500
    kept = repository.get_event("event-keep")
    assert kept is not None
    assert kept["priority_hold_status"] == "kept"


@pytest.mark.asyncio
async def test_event_failure_after_hold_records_real_reason_and_notifies():
    settings = Settings(
        initial_probe_on_register=True,
        register_probe_stabilization_seconds=0,
        register_priority_hold=-500,
        register_callback_enabled=True,
        register_callback_url="http://register.test/notify",
    )
    probes = RegisterProbeManager()

    async def enqueue_register_event(**values: Any) -> dict[str, Any]:
        raise ValueError("探针方案不存在: quality-marker")

    probes.enqueue_register_event = enqueue_register_event  # type: ignore[method-assign]
    service, repository, account_service, _ = _service(settings=settings, probes=probes)

    await service._process_claimed(
        {
            "event_id": "event-give-up",
            "attempts": 20,
            "grok2api_account_id": 17,
            "email": "new@example.test",
            "bot_risk": False,
        }
    )

    assert repository.failed is not None
    assert account_service.client.priorities[17] == -500
    event = repository.get_event("event-give-up")
    assert event is not None
    assert event["priority_hold_status"] == "kept"
    assert "探针未执行" in event["priority_hold_error"]
    assert "quality-marker" in event["priority_hold_error"]
    assert "未通过" not in event["priority_hold_error"]
    callback = repository.callbacks["event-give-up"]["payload"]
    assert callback["probe_outcome"] == "failed"
    assert callback["verdict"] == "probe_failed"
    assert callback["account_id"] == 17

    await service.scan_priority_holds()

    assert repository.get_event("event-give-up")["priority_hold_status"] == "kept"


@pytest.mark.asyncio
async def test_hold_scan_explains_failed_event_without_runs():
    service, repository, account_service, _ = _service(
        settings=Settings(
            initial_probe_on_register=True,
            register_probe_stabilization_seconds=0,
            register_priority_hold=-500,
        )
    )
    repository.events["event-legacy"] = {
        "event_id": "event-legacy",
        "status": "failed",
        "last_error": "探针队列已达到上限 50",
        "resolved_account_id": 17,
        "priority_hold_status": "held",
        "original_priority": 3,
        "held_priority": -500,
    }

    await service.scan_priority_holds()

    event = repository.get_event("event-legacy")
    assert event["priority_hold_status"] == "kept"
    assert "探针未执行" in event["priority_hold_error"]
    assert "探针队列已达到上限 50" in event["priority_hold_error"]
    assert 17 not in account_service.client.priorities


@pytest.mark.asyncio
async def test_priority_hold_keeps_low_priority_when_samples_insufficient():
    service, repository, account_service, probes = _service(
        settings=Settings(
            initial_probe_on_register=True,
            register_probe_stabilization_seconds=0,
            register_priority_hold=-500,
        )
    )
    await service._process_claimed(
        {
            "event_id": "event-insufficient",
            "attempts": 1,
            "grok2api_account_id": 17,
            "email": "new@example.test",
            "bot_risk": False,
        }
    )
    probes.repository.runs = [
        {
            "id": "run-1",
            "source_event_id": "event-insufficient",
            "status": "completed",
            "summary": {
                "anomaly_count": 0,
                "warning_count": 1,
                "sample_count": 1,
                "classifications": {"insufficient": 1},
            },
        }
    ]

    await service.maybe_restore_priority_hold(
        {"source_event_id": "event-insufficient", "id": "run-1"}
    )

    assert account_service.client.priorities[17] == -500
    kept = repository.get_event("event-insufficient")
    assert kept is not None
    assert kept["priority_hold_status"] == "kept"
    assert "样本不足" in str(kept.get("priority_hold_error") or "")


@pytest.mark.asyncio
async def test_priority_hold_scan_retries_failed_restore():
    service, repository, account_service, probes = _service(
        settings=Settings(
            initial_probe_on_register=True,
            register_probe_stabilization_seconds=0,
            register_priority_hold=-500,
        )
    )
    await service._process_claimed(
        {
            "event_id": "event-scan",
            "attempts": 1,
            "grok2api_account_id": 17,
            "email": "new@example.test",
            "bot_risk": False,
        }
    )
    probes.repository.runs = [
        {
            "id": "run-1",
            "source_event_id": "event-scan",
            "status": "completed",
            "summary": {"anomaly_count": 0},
        }
    ]

    original_set_priority = account_service.client.set_account_priority
    restore_attempts = {"failed": False}

    async def fail_once(account_id: int, priority: int) -> dict[str, Any]:
        if not restore_attempts["failed"]:
            restore_attempts["failed"] = True
            raise RuntimeError("grok2api unavailable")
        return await original_set_priority(account_id, priority)

    account_service.client.set_account_priority = fail_once  # type: ignore[method-assign]
    await service.maybe_restore_priority_hold(
        {"source_event_id": "event-scan", "id": "run-1"}
    )
    failed = repository.get_event("event-scan")
    assert failed is not None
    assert failed["priority_hold_status"] == "restore_failed"
    assert account_service.client.priorities[17] == -500

    await service.scan_priority_holds()
    restored = repository.get_event("event-scan")
    assert restored is not None
    assert restored["priority_hold_status"] == "restored"
    assert account_service.client.priorities[17] == 8


@pytest.mark.asyncio
async def test_priority_hold_does_not_overwrite_original_on_retry():
    service, repository, account_service, _probes = _service(
        settings=Settings(
            initial_probe_on_register=True,
            register_probe_stabilization_seconds=15,
            register_priority_hold=-1000,
        )
    )
    payload = {
        "event_id": "event-retry-hold",
        "attempts": 1,
        "created_at": utc_now(),
        "grok2api_account_id": 17,
        "email": "new@example.test",
        "bot_risk": False,
    }
    await service._process_claimed(payload)
    assert account_service.client.priorities[17] == -1000
    await service._process_claimed({**payload, "attempts": 2})
    held = repository.get_event("event-retry-hold")
    assert held is not None
    assert held["original_priority"] == 8
    assert held["priority_hold_status"] == "held"
    assert account_service.client.priorities[17] == -1000


@pytest.mark.asyncio
async def test_confirmed_bfs_quarantines_without_probe_or_hold():
    repository = RegisterRepository()
    account_service = RegisterAccountService()
    probes = RegisterProbeManager()
    accounts = FakeAccountRepository()
    service = RegisterIntegrationService(
        settings=Settings(
            initial_probe_on_register=True,
            register_priority_hold_enabled=True,
            register_priority_hold=-1000,
        ),
        repository=repository,  # type: ignore[arg-type]
        accounts=accounts,  # type: ignore[arg-type]
        account_service=account_service,  # type: ignore[arg-type]
        probes=probes,  # type: ignore[arg-type]
    )

    await service._process_claimed(
        {
            "event_id": "event-bfs-1",
            "attempts": 1,
            "grok2api_account_id": 17,
            "email": "risk@example.test",
            "bot_risk": True,
            "bfs": 1,
            "registration_id": "reg-1",
        }
    )

    assert repository.completed == ("event-bfs-1", 17, [])
    assert probes.values is None
    assert account_service.client.priorities == {}
    assert len(account_service.quarantines) == 1
    quarantine = account_service.quarantines[0]
    assert quarantine["accountId"] == 17
    assert quarantine["force"] is True
    assert quarantine["permanent"] is True
    assert quarantine["source"] == "grok-register"
    assert accounts.marked[0]["bfs"] == 1


@pytest.mark.asyncio
async def test_bot_risk_without_confirmed_bfs_still_enqueues_probe():
    repository = RegisterRepository()
    account_service = RegisterAccountService()
    probes = RegisterProbeManager()
    accounts = FakeAccountRepository()
    service = RegisterIntegrationService(
        settings=Settings(initial_probe_on_register=True),
        repository=repository,  # type: ignore[arg-type]
        accounts=accounts,  # type: ignore[arg-type]
        account_service=account_service,  # type: ignore[arg-type]
        probes=probes,  # type: ignore[arg-type]
    )

    await service._process_claimed(
        {
            "event_id": "event-bfs-3",
            "attempts": 1,
            "grok2api_account_id": 17,
            "email": "watch@example.test",
            "bot_risk": True,
            "bfs": 3,
        }
    )

    assert repository.completed == ("event-bfs-3", 17, ["run-1"])
    assert probes.values is not None
    assert account_service.quarantines == []
    assert accounts.marked[0]["bfs"] == 3



def _callback_settings(**values: Any) -> Settings:
    return Settings(
        register_callback_enabled=True,
        register_callback_url="http://grok-register:8787/api/integrations/grokiq/notify",
        grok_register_webhook_token="shared-token",
        **values,
    )


@pytest.mark.asyncio
async def test_confirmed_degradation_enqueues_callback_once():
    repository = RegisterRepository()
    account_service = RegisterAccountService()
    probes = RegisterProbeManager()
    accounts = FakeAccountRepository()
    service = RegisterIntegrationService(
        settings=_callback_settings(initial_probe_on_register=True),
        repository=repository,  # type: ignore[arg-type]
        accounts=accounts,  # type: ignore[arg-type]
        account_service=account_service,  # type: ignore[arg-type]
        probes=probes,  # type: ignore[arg-type]
    )

    payload = {
        "event_id": "event-callback-bfs",
        "attempts": 1,
        "grok2api_account_id": 17,
        "email": "risk@example.test",
        "bot_risk": True,
        "bfs": 1,
        "registration_id": "123",
    }
    await service._process_claimed(payload)
    await service._process_claimed(payload)

    assert probes.values is None
    assert "event-callback-bfs" in repository.callbacks
    callback = repository.callbacks["event-callback-bfs"]["payload"]
    assert callback["degraded"] is True
    assert callback["verdict"] == "degraded"
    assert callback["probe_outcome"] == "confirmed_degraded"
    assert callback["isolated"] is True
    assert callback["event_type"] == "grokiq.notify"
    assert callback["registration_id"] == "123"


@pytest.mark.asyncio
async def test_callback_waits_until_register_probes_are_terminal():
    repository = RegisterRepository()
    account_service = RegisterAccountService()
    probes = RegisterProbeManager()
    accounts = FakeAccountRepository()
    service = RegisterIntegrationService(
        settings=_callback_settings(
            initial_probe_on_register=True,
            register_probe_stabilization_seconds=0,
        ),
        repository=repository,  # type: ignore[arg-type]
        accounts=accounts,  # type: ignore[arg-type]
        account_service=account_service,  # type: ignore[arg-type]
        probes=probes,  # type: ignore[arg-type]
    )
    await service._process_claimed(
        {
            "event_id": "event-callback-pending",
            "attempts": 1,
            "grok2api_account_id": 17,
            "email": "new@example.test",
            "bot_risk": False,
            "registration_id": "88",
        }
    )
    assert repository.callbacks == {}
    repository.events["event-callback-pending"].update(
        {
            "email": "new@example.test",
            "registration_id": "88",
        }
    )

    probes.repository.runs = [
        {
            "id": "run-1",
            "source_event_id": "event-callback-pending",
            "status": "running",
            "summary": {},
        }
    ]
    await service.maybe_restore_priority_hold(
        {"source_event_id": "event-callback-pending"}
    )
    assert repository.callbacks == {}

    probes.repository.runs = [
        {
            "id": "run-1",
            "source_event_id": "event-callback-pending",
            "status": "completed",
            "summary": {"anomaly_count": 2, "warning_count": 0, "sample_count": 2},
        }
    ]
    accounts.assessments[17] = {
        "monitor_status": "high_risk",
        "risk_score": 80,
        "risk_reasons": ["强降智信号"],
        "quarantine_until": None,
    }
    await service.maybe_restore_priority_hold(
        {"source_event_id": "event-callback-pending"}
    )
    callback = repository.callbacks["event-callback-pending"]["payload"]
    assert callback["degraded"] is True
    assert callback["verdict"] == "high_risk"
    assert callback["probe_outcome"] == "failed"
    assert callback["source"] == "register_probe"


@pytest.mark.asyncio
async def test_callback_http_retry_then_success(monkeypatch: pytest.MonkeyPatch):
    repository = RegisterRepository()
    service = RegisterIntegrationService(
        settings=_callback_settings(),
        repository=repository,  # type: ignore[arg-type]
        accounts=UnusedAccountRepository(),  # type: ignore[arg-type]
        account_service=RegisterAccountService(),  # type: ignore[arg-type]
        probes=RegisterProbeManager(),  # type: ignore[arg-type]
    )
    calls = {"count": 0}

    async def flaky_post(payload: dict[str, Any]) -> None:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("connection reset")

    monkeypatch.setattr(service, "_post_callback", flaky_post)
    repository.callbacks["event-http"] = {
        "event_id": "event-http",
        "status": "pending",
        "attempts": 0,
        "payload": {"event_id": "event-http", "degraded": True},
    }
    first = repository.claim_callback_due()
    assert first is not None
    await service._deliver_callback(first)
    assert repository.callbacks["event-http"]["status"] == "pending"
    second = repository.claim_callback_due()
    assert second is not None
    await service._deliver_callback(second)
    assert repository.callbacks["event-http"]["status"] == "delivered"
    assert calls["count"] == 2



@pytest.mark.asyncio
async def test_callback_after_import_without_probe():
    repository = RegisterRepository()
    service = RegisterIntegrationService(
        settings=_callback_settings(initial_probe_on_register=False),
        repository=repository,  # type: ignore[arg-type]
        accounts=FakeAccountRepository(),  # type: ignore[arg-type]
        account_service=RegisterAccountService(),  # type: ignore[arg-type]
        probes=RegisterProbeManager(),  # type: ignore[arg-type]
    )
    await service._process_claimed(
        {
            "event_id": "event-imported-only",
            "attempts": 1,
            "grok2api_account_id": 17,
            "email": "new@example.test",
            "bot_risk": False,
            "registration_id": "55",
        }
    )
    callback = repository.callbacks["event-imported-only"]["payload"]
    assert callback["degraded"] is False
    assert callback["probe_outcome"] == "skipped"
    assert callback["verdict"] == "imported"
    assert callback["email"] == "new@example.test"
