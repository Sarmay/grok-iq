from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timedelta
from typing import Any

from curl_cffi.requests import AsyncSession as CurlAsyncSession

from app.core.clock import ensure_utc, utc_now
from app.core.config import (
    DEFAULT_REGISTER_PROBE_STABILIZATION_SECONDS,
    REGISTER_PROBE_EXECUTION_MODE,
    REGISTER_PROBE_PROXY_TARGETS,
    Settings,
)
from app.persistence.account_repository import AccountRepository
from app.persistence.probe_repository import (
    TERMINAL_RUN_STATUSES,
    QueueFullError,
    RunStateError,
)
from app.persistence.register_event_repository import (
    PRIORITY_HOLD_HELD,
    PRIORITY_HOLD_NONE,
    PRIORITY_HOLD_RESTORE_FAILED,
    RegisterEventRepository,
)
from app.services.account_service import AccountService
from app.services.probe_manager import ProbeManager
from app.services.wechat_notification import WeChatAccountNotificationService

logger = logging.getLogger(__name__)
MAX_EVENT_ATTEMPTS = 20
RETRY_DELAYS = (2, 5, 10, 20, 30, 60, 120, 300)
HOLD_SCAN_INTERVAL_SECONDS = 30.0
# Kept as a compatibility alias for integrations importing the former constant;
# runtime behavior uses the hot-updatable Settings value below.
REGISTER_PROBE_STABILIZATION_SECONDS = DEFAULT_REGISTER_PROBE_STABILIZATION_SECONDS
CONFIRMED_REGISTER_DEGRADATION_BFS = frozenset({"1", "2"})


def is_confirmed_register_degradation(*, bot_risk: Any, bfs: Any) -> bool:
    """Return True when grok-register reports a confirmed 降智 account."""

    if not bool(bot_risk):
        return False
    return str(bfs or "").strip() in CONFIRMED_REGISTER_DEGRADATION_BFS


class RegisteredAccountPending(RuntimeError):
    def __init__(self, message: str, *, retry_after_seconds: float = 0) -> None:
        super().__init__(message)
        self.retry_after_seconds = max(0.0, float(retry_after_seconds or 0))


class RegisterIntegrationService:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: RegisterEventRepository,
        accounts: AccountRepository,
        account_service: AccountService,
        probes: ProbeManager,
        notifications: WeChatAccountNotificationService | None = None,
    ):
        self.settings = settings
        self.repository = repository
        self.accounts = accounts
        self.account_service = account_service
        self.probes = probes
        self.notifications = notifications
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._next_hold_scan_at = 0.0

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        recovered = self.repository.recover_processing()
        if recovered:
            logger.info("recovered register webhook events count=%s", recovered)
        recovered_callbacks = self.repository.recover_callback_processing()
        if recovered_callbacks:
            logger.info(
                "recovered register callback deliveries count=%s",
                recovered_callbacks,
            )
        self._task = asyncio.create_task(
            self._worker(), name="grok-register-integration"
        )
        self._wake.set()

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    def accept(self, values: dict[str, Any]) -> dict[str, Any]:
        event, created = self.repository.receive(values)
        self._wake.set()
        return {
            "accepted": True,
            "duplicate": not created,
            "eventId": event["event_id"],
        }

    def list_events(
        self,
        *,
        page: int,
        page_size: int,
        status: str = "",
        search: str = "",
    ) -> dict[str, Any]:
        return self.repository.list_events(
            page=page,
            page_size=page_size,
            status=status,
            search=search,
        )

    async def _worker(self) -> None:
        while True:
            await self._maybe_scan_priority_holds()
            event = self.repository.claim_due()
            if event is not None:
                await self._process_claimed(event)
                continue
            if self.settings.register_callback_enabled:
                delivery = self.repository.claim_callback_due()
                if delivery is not None:
                    await self._deliver_callback(delivery)
                    continue
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=2)
            except TimeoutError:
                pass

    async def _process_claimed(self, event: dict[str, Any]) -> None:
        event_id = str(event["event_id"])
        attempts = int(event.get("attempts") or 1)
        try:
            account = await self._registered_account(event)
            account_id = int(account.get("id") or 0)
            self.repository.bind_account(event_id, account_id)
            await self._record_registration_risk(event_id, event, account)
            if is_confirmed_register_degradation(
                bot_risk=event.get("bot_risk"),
                bfs=event.get("bfs"),
            ):
                await self._quarantine_confirmed_register_account(event, account)
                self.repository.complete(event_id, account_id, [])
                await self.maybe_enqueue_register_callback(
                    event_id,
                    confirmed_degraded=True,
                    event={
                        **event,
                        "status": "completed",
                        "resolved_account_id": account_id,
                        "run_ids": [],
                    },
                )
                logger.info(
                    "register webhook completed event_id=%s account_id=%s "
                    "confirmed_degradation=1 runs=0",
                    event_id,
                    account_id,
                )
                return
            await self._apply_priority_hold(event, account)
            if self.settings.initial_probe_on_register:
                self._ensure_initial_probe_ready(event, account)
            run_ids = await self._enqueue_initial_probe(event_id, account)
            self.repository.complete(event_id, account_id, run_ids)
            if not run_ids:
                await self.maybe_enqueue_register_callback(
                    event_id,
                    event={
                        **event,
                        "status": "completed",
                        "resolved_account_id": account_id,
                        "run_ids": run_ids,
                    },
                )
            logger.info(
                "register webhook completed event_id=%s account_id=%s runs=%s",
                event_id,
                account_id,
                len(run_ids),
            )
        except (RegisteredAccountPending, QueueFullError, RunStateError) as exc:
            await self._retry_or_fail(event_id, attempts, exc)
        except ValueError as exc:
            await self._retry_or_fail(event_id, attempts, exc)
        except Exception as exc:
            logger.exception("register webhook processing failed event_id=%s", event_id)
            await self._retry_or_fail(event_id, attempts, exc)

    async def _registered_account(self, event: dict[str, Any]) -> dict[str, Any]:
        account = await self.account_service.find_registered_account(
            event.get("grok2api_account_id"), str(event["email"])
        )
        if account is None:
            raise RegisteredAccountPending("grok2api 中尚未发现该注册账号")
        return account

    async def _record_registration_risk(
        self,
        event_id: str,
        event: dict[str, Any],
        account: dict[str, Any],
    ) -> None:
        if not bool(event.get("bot_risk")):
            return
        account_id = int(account.get("id") or 0)
        previous_assessment = self.accounts.get_assessment(account_id)
        assessment = self.accounts.mark_registration_risk(
            account_id=account_id,
            bfs=event.get("bfs"),
            registration_id=str(event.get("registration_id") or ""),
            risk_score_cap=self.settings.risk_score_cap,
            risk_high_floor=self.settings.risk_high_floor,
        )
        if self.notifications is None:
            return
        try:
            await self.notifications.notify_account_transition(
                account=account,
                previous=previous_assessment,
                current=assessment,
                source="grok-register",
            )
        except Exception:
            logger.exception(
                "wechat notification failed event_id=%s account_id=%s",
                event_id,
                account_id,
            )

    async def _quarantine_confirmed_register_account(
        self,
        event: dict[str, Any],
        account: dict[str, Any],
    ) -> dict[str, Any]:
        account_id = int(account.get("id") or 0)
        bfs = event.get("bfs")
        result = await self.account_service.apply_auto_quarantine(
            account_id,
            source="grok-register",
            note=f"grok-register 确认降智：bot_risk/bfs={bfs}",
            risk_score=max(float(self.settings.risk_high_floor), 85.0),
            force=True,
            permanent=True,
            detail={
                "botRisk": True,
                "bfs": bfs,
                "registrationId": str(event.get("registration_id") or ""),
                "eventId": str(event.get("event_id") or ""),
            },
        )
        logger.info(
            "register confirmed degradation quarantined event_id=%s "
            "account_id=%s action=%s",
            event.get("event_id"),
            account_id,
            result.get("actionStatus"),
        )
        return result

    async def _enqueue_initial_probe(
        self, event_id: str, account: dict[str, Any]
    ) -> list[str]:
        if not self.settings.initial_probe_on_register:
            return []
        if int(account.get("egressNodeId") or 0) <= 0:
            account = await self.account_service.ensure_account_egress(account)
        result = await self.probes.enqueue_register_event(
            source_event_id=event_id,
            account=account,
            profile_ids=self.settings.register_probe_profile_ids,
            execution_mode=REGISTER_PROBE_EXECUTION_MODE,
            rounds=self.settings.register_probe_rounds_by_profile(),
            proxy_targets=[
                dict(target) for target in REGISTER_PROBE_PROXY_TARGETS
            ],
        )
        return list(result.get("runIds") or [])

    @staticmethod
    def _timestamp(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return ensure_utc(value)
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError, OverflowError):
            return None
        return ensure_utc(parsed)

    def _ensure_initial_probe_ready(
        self,
        event: dict[str, Any],
        account: dict[str, Any],
    ) -> None:
        """Defer a new account probe until import-side permissions settle.

        grok2api finishes credential and model-catalog persistence before its
        import request returns, while the upstream chat permission can take a
        few more seconds to propagate. Calling immediately can create a false
        first sample and trigger an upstream cooldown. Keep the durable webhook
        pending for the configured stabilization window, but always prefer a
        longer live account cooldown when grok2api exposes one.
        """

        now = utc_now()
        stabilization_remaining = 0.0
        received_at = self._timestamp(event.get("created_at"))
        if received_at is not None:
            ready_at = received_at + timedelta(
                seconds=self.settings.register_probe_stabilization_seconds
            )
            stabilization_remaining = max(0.0, (ready_at - now).total_seconds())

        cooldown_remaining = 0.0
        cooldown_until = self._timestamp(account.get("cooldownUntil"))
        if cooldown_until is not None:
            cooldown_remaining = max(
                0.0,
                (cooldown_until - now).total_seconds(),
            )

        remaining = max(stabilization_remaining, cooldown_remaining)
        if remaining <= 0:
            return
        if cooldown_remaining >= stabilization_remaining:
            reason = "新导入账号仍在 grok2api 冷却中"
        else:
            reason = "新导入账号正在等待 grok2api 模型权限传播"
        raise RegisteredAccountPending(
            reason,
            retry_after_seconds=math.ceil(remaining),
        )

    async def _retry_or_fail(
        self, event_id: str, attempts: int, exc: Exception
    ) -> None:
        if attempts >= MAX_EVENT_ATTEMPTS:
            self.repository.fail(event_id, str(exc))
            await self._finalize_failed_event(event_id, str(exc))
            return
        requested_delay = float(getattr(exc, "retry_after_seconds", 0) or 0)
        delay = requested_delay or RETRY_DELAYS[
            min(max(attempts - 1, 0), len(RETRY_DELAYS) - 1)
        ]
        self.repository.retry(event_id, str(exc), delay)
        logger.info(
            "register webhook deferred event_id=%s attempts=%s retry_in=%.1fs reason=%s",
            event_id,
            attempts,
            delay,
            exc,
        )

    async def _finalize_failed_event(self, event_id: str, error: str) -> None:
        """Close out an event that gave up before any register probe ran.

        The priority hold may already be in place.  Keep the account parked,
        but record the real cause instead of a probe verdict, and tell
        grok-register so the registration does not wait forever.
        """

        event = self.repository.get_event(event_id) or {"event_id": event_id}
        status = str(event.get("priority_hold_status") or PRIORITY_HOLD_NONE)
        if status in {PRIORITY_HOLD_HELD, PRIORITY_HOLD_RESTORE_FAILED}:
            self.repository.mark_priority_kept(
                event_id, self._event_failed_reason(error)
            )
            logger.info(
                "register priority kept after event failure event_id=%s "
                "account_id=%s error=%s",
                event_id,
                event.get("resolved_account_id") or event.get("grok2api_account_id"),
                error,
            )
        await self.maybe_enqueue_register_callback(
            event_id,
            event={**event, "status": "failed", "last_error": error},
        )

    @staticmethod
    def _event_failed_reason(error: str) -> str:
        detail = str(error or "").strip()
        base = "注册事件处理失败，探针未执行，保持降低后的 grok2api 优先级"
        return f"{base}：{detail}" if detail else base

    @staticmethod
    def _probe_never_ran(event: dict[str, Any], runs: list[dict[str, Any]]) -> bool:
        return not runs and str(event.get("status") or "") == "failed"

    async def maybe_restore_priority_hold(self, run: dict[str, Any]) -> None:
        event_id = str(run.get("source_event_id") or "").strip()
        if not event_id:
            return
        await self._restore_priority_hold_if_ready(event_id)
        await self.maybe_enqueue_register_callback(event_id)

    async def scan_priority_holds(self) -> None:
        for event in self.repository.list_unresolved_priority_holds():
            event_id = str(event.get("event_id") or "").strip()
            if not event_id:
                continue
            try:
                await self._restore_priority_hold_if_ready(event_id)
            except Exception:
                logger.exception(
                    "register priority hold scan failed event_id=%s", event_id
                )

    async def _maybe_scan_priority_holds(self) -> None:
        loop = asyncio.get_running_loop()
        now = loop.time()
        if now < self._next_hold_scan_at:
            return
        self._next_hold_scan_at = now + HOLD_SCAN_INTERVAL_SECONDS
        await self.scan_priority_holds()

    async def _apply_priority_hold(
        self,
        event: dict[str, Any],
        account: dict[str, Any],
    ) -> None:
        if not self.settings.register_priority_hold_enabled:
            return
        if not self.settings.initial_probe_on_register:
            return
        client = getattr(self.account_service, "client", None)
        if client is None or not hasattr(client, "set_account_priority"):
            raise RegisteredAccountPending("grok2api 客户端不可用，无法降低新账号优先级")
        account_id = int(account.get("id") or 0)
        if account_id <= 0:
            raise RegisteredAccountPending("注册账号 ID 无效")
        current_priority = self._account_priority(account)
        target_priority = int(self.settings.register_priority_hold)
        stored = self.repository.mark_priority_hold(
            str(event["event_id"]),
            original_priority=current_priority,
            held_priority=target_priority,
        )
        if stored is None:
            return
        if str(stored.get("priority_hold_status") or PRIORITY_HOLD_NONE) != PRIORITY_HOLD_HELD:
            return
        original_priority = int(stored.get("original_priority") or current_priority)
        if current_priority <= target_priority:
            logger.info(
                "register priority already held event_id=%s account_id=%s "
                "priority=%s original=%s",
                event.get("event_id"),
                account_id,
                current_priority,
                original_priority,
            )
            return
        await client.set_account_priority(account_id, target_priority)
        logger.info(
            "register priority held event_id=%s account_id=%s from=%s to=%s",
            event.get("event_id"),
            account_id,
            original_priority,
            target_priority,
        )

    async def _restore_priority_hold_if_ready(self, event_id: str) -> None:
        event = self.repository.get_event(event_id)
        if event is None:
            return
        status = str(event.get("priority_hold_status") or PRIORITY_HOLD_NONE)
        if status not in {PRIORITY_HOLD_HELD, PRIORITY_HOLD_RESTORE_FAILED}:
            return
        outcome = self._register_probe_outcome(event)
        if outcome == "pending":
            return
        if outcome != "passed":
            if outcome == "insufficient":
                reason = "注册探针样本不足，保持降低后的 grok2api 优先级"
            elif self._probe_never_ran(event, self._event_runs(event_id)):
                reason = self._event_failed_reason(str(event.get("last_error") or ""))
            else:
                reason = "注册探针未通过，保持降低后的 grok2api 优先级"
            self.repository.mark_priority_kept(event_id, reason)
            logger.info(
                "register priority kept after %s probe event_id=%s account_id=%s",
                outcome,
                event_id,
                event.get("resolved_account_id") or event.get("grok2api_account_id"),
            )
            return
        await self._restore_held_priority(event)

    def _event_runs(self, event_id: str) -> list[dict[str, Any]]:
        probe_repository = getattr(self.probes, "repository", None)
        if probe_repository is None or not event_id:
            return []
        return list(probe_repository.list_runs_for_source_event(event_id))

    def _register_probe_outcome(self, event: dict[str, Any]) -> str:
        event_id = str(event.get("event_id") or "")
        runs = self._event_runs(event_id)
        if not runs:
            event_status = str(event.get("status") or "")
            if event_status == "completed":
                return "empty"
            if event_status == "failed":
                return "failed"
            return "pending"
        if any(str(run.get("status") or "") not in TERMINAL_RUN_STATUSES for run in runs):
            return "pending"
        parents_with_children = {
            str(run.get("parent_run_id") or "")
            for run in runs
            if run.get("parent_run_id")
        }
        leaves = [
            run for run in runs if str(run.get("id") or "") not in parents_with_children
        ]
        if not leaves:
            return "pending"
        if any(self._register_run_insufficient(run) for run in leaves):
            return "insufficient"
        if all(self._register_run_passed(run) for run in leaves):
            return "passed"
        return "failed"

    @staticmethod
    def _register_run_summary(run: dict[str, Any]) -> dict[str, Any]:
        summary = run.get("summary")
        return summary if isinstance(summary, dict) else {}

    @classmethod
    def _register_run_insufficient(cls, run: dict[str, Any]) -> bool:
        summary = cls._register_run_summary(run)
        classifications = summary.get("classifications")
        counts = classifications if isinstance(classifications, dict) else {}
        if int(summary.get("warning_count") or 0) > 0:
            return True
        if int(counts.get("insufficient") or 0) > 0:
            return True
        sample_count = summary.get("sample_count")
        if sample_count is not None and int(sample_count) <= 0:
            return True
        return False

    @classmethod
    def _register_run_passed(cls, run: dict[str, Any]) -> bool:
        if str(run.get("status") or "") != "completed":
            return False
        if cls._register_run_insufficient(run):
            return False
        summary = cls._register_run_summary(run)
        return int(summary.get("anomaly_count") or 0) == 0

    async def _restore_held_priority(self, event: dict[str, Any]) -> None:
        event_id = str(event.get("event_id") or "")
        account_id = int(
            event.get("resolved_account_id") or event.get("grok2api_account_id") or 0
        )
        original = event.get("original_priority")
        if not event_id or account_id <= 0 or original is None:
            self.repository.mark_priority_kept(
                event_id,
                "缺少可恢复的原始 grok2api 优先级",
            )
            return
        client = getattr(self.account_service, "client", None)
        if client is None or not hasattr(client, "set_account_priority"):
            self.repository.mark_priority_restore_failed(
                event_id, "grok2api 客户端不可用，无法恢复账号优先级"
            )
            return
        try:
            await client.set_account_priority(account_id, int(original))
        except Exception as exc:
            self.repository.mark_priority_restore_failed(event_id, str(exc))
            logger.warning(
                "register priority restore failed event_id=%s account_id=%s error=%s",
                event_id,
                account_id,
                exc,
            )
            return
        self.repository.mark_priority_restored(event_id)
        logger.info(
            "register priority restored event_id=%s account_id=%s priority=%s",
            event_id,
            account_id,
            original,
        )

    async def maybe_enqueue_register_callback(
        self,
        event_id: str,
        *,
        confirmed_degraded: bool = False,
        event: dict[str, Any] | None = None,
    ) -> None:
        if not self.settings.register_callback_enabled:
            return
        if not str(self.settings.register_callback_url or "").strip():
            return
        stored = self.repository.get_event(event_id) or {}
        event = {**stored, **(event or {}), "event_id": event_id}
        if confirmed_degraded:
            probe_outcome = "confirmed_degraded"
        else:
            probe_outcome = self._register_probe_outcome(event)
            if probe_outcome == "pending":
                return
            if str(event.get("status") or "") not in {"completed", "failed"}:
                return
            if probe_outcome == "empty" and not self.settings.initial_probe_on_register:
                probe_outcome = "skipped"
        payload = self._callback_payload(
            event,
            probe_outcome=probe_outcome,
        )
        stored = self.repository.enqueue_callback(event_id, payload)
        if stored is not None and str(stored.get("status") or "") == "pending":
            self._wake.set()

    def _callback_payload(
        self,
        event: dict[str, Any],
        *,
        probe_outcome: str,
    ) -> dict[str, Any]:
        account_id = int(
            event.get("resolved_account_id") or event.get("grok2api_account_id") or 0
        )
        getter = getattr(self.accounts, "get_assessment", None)
        assessment = (
            getter(account_id) if callable(getter) and account_id > 0 else None
        )
        if not isinstance(assessment, dict):
            assessment = {}
        monitor_status = str(assessment.get("monitor_status") or "")
        isolated = monitor_status == "quarantined" and assessment.get(
            "quarantine_until"
        ) is None
        if probe_outcome == "confirmed_degraded":
            isolated = True
            monitor_status = monitor_status or "quarantined"
        risk_reasons = assessment.get("risk_reasons")
        if not isinstance(risk_reasons, list):
            risk_reasons = []
        run_ids = event.get("run_ids")
        if not isinstance(run_ids, list):
            run_ids = []
        degraded = (
            probe_outcome in {"confirmed_degraded", "failed"}
            or isolated
            or monitor_status in {"high_risk", "quarantined"}
        )
        verdict = self._callback_verdict(
            probe_outcome=probe_outcome,
            monitor_status=monitor_status,
            isolated=isolated,
        )
        source = (
            "grok-register"
            if probe_outcome == "confirmed_degraded"
            else "register_probe"
        )
        return {
            "event_id": str(event.get("event_id") or ""),
            "event_type": "grokiq.notify",
            "registration_id": str(event.get("registration_id") or ""),
            "email": str(event.get("email") or ""),
            "account_id": account_id or None,
            "occurred_at": utc_now().isoformat().replace("+00:00", "Z"),
            "verdict": verdict,
            "degraded": degraded,
            "monitor_status": monitor_status,
            "risk_score": float(assessment.get("risk_score") or 0),
            "risk_reasons": [str(item) for item in risk_reasons if str(item).strip()],
            "isolated": isolated,
            "probe_outcome": probe_outcome,
            "run_ids": [str(item) for item in run_ids if str(item).strip()],
            "source": source,
        }

    @staticmethod
    def _callback_verdict(
        *,
        probe_outcome: str,
        monitor_status: str,
        isolated: bool,
    ) -> str:
        if probe_outcome == "confirmed_degraded":
            return "degraded"
        if isolated or monitor_status == "quarantined":
            return "quarantined"
        if monitor_status == "high_risk":
            return "high_risk"
        if monitor_status in {"suspect", "watch"}:
            return "suspect"
        if probe_outcome == "insufficient":
            return "insufficient_samples"
        if probe_outcome == "failed":
            return "probe_failed"
        if probe_outcome in {"empty", "skipped"}:
            return "imported"
        if probe_outcome == "passed":
            return "normal"
        return "imported"

    async def _deliver_callback(self, delivery: dict[str, Any]) -> None:
        event_id = str(delivery.get("event_id") or "")
        attempts = int(delivery.get("attempts") or 1)
        try:
            await self._post_callback(delivery.get("payload") or {})
            self.repository.complete_callback(event_id)
            logger.info("register callback delivered event_id=%s", event_id)
        except Exception as exc:
            if attempts >= MAX_EVENT_ATTEMPTS:
                self.repository.fail_callback(event_id, str(exc))
                logger.warning(
                    "register callback failed event_id=%s error=%s",
                    event_id,
                    exc,
                )
                return
            delay = RETRY_DELAYS[min(max(attempts - 1, 0), len(RETRY_DELAYS) - 1)]
            self.repository.retry_callback(event_id, str(exc), delay)
            logger.info(
                "register callback deferred event_id=%s attempts=%s retry_in=%.1fs reason=%s",
                event_id,
                attempts,
                delay,
                exc,
            )

    async def _post_callback(self, payload: dict[str, Any]) -> None:
        url = str(self.settings.register_callback_url or "").strip()
        if not url:
            raise RuntimeError("回调通知地址未配置")
        timeout = max(1, min(int(self.settings.register_callback_timeout_seconds or 10), 60))
        try:
            async with CurlAsyncSession(trust_env=False) as client:
                response = await client.post(
                    url,
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "x-grokiq-token": str(
                            self.settings.grok_register_webhook_token or ""
                        ).strip(),
                    },
                    json=payload,
                    timeout=timeout,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise RuntimeError(f"回调通知请求失败: {exc}") from exc
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code < 200 or status_code >= 300:
            detail = str(getattr(response, "text", "") or "").strip()[:1000]
            raise RuntimeError(
                f"回调通知返回 HTTP {status_code}: {detail or '空响应'}"
            )

    @staticmethod
    def _account_priority(account: dict[str, Any]) -> int:
        raw = account.get("priority")
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0
