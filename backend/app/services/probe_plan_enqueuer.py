from __future__ import annotations

import asyncio
from typing import Any

from app.core.config import Settings
from app.integrations.grok2api.client import Grok2APIClient
from app.persistence.account_repository import AccountRepository
from app.persistence.probe_repository import ProbeRepository

from .probe_target_validator import ProbeTargetValidator


class ProbePlanEnqueuer:
    """Resolves one scheduled plan and persists its concrete probe runs."""

    def __init__(
        self,
        *,
        settings: Settings,
        repository: ProbeRepository,
        accounts: AccountRepository,
        client: Grok2APIClient,
        target_validator: ProbeTargetValidator,
        enqueue_lock: asyncio.Lock,
        wake: asyncio.Event,
    ):
        self.settings = settings
        self.repository = repository
        self.accounts = accounts
        self.client = client
        self.target_validator = target_validator
        self.enqueue_lock = enqueue_lock
        self.wake = wake

    async def enqueue(self, plan: dict[str, Any]) -> dict[str, Any]:
        plan_id = str(plan["id"])
        profile_ids = self._profile_ids(plan)
        if plan.get("overlap_policy") == "skip" and self.repository.active_plan_run_count(plan_id):
            return {
                "accountScope": str(plan.get("account_scope") or "fixed"),
                "created": 0,
                "skipped": len(plan["account_ids"]),
                "reason": "previous_batch_active",
            }

        execution_mode = str(plan.get("execution_mode") or "chat")
        raw_targets = list(plan["proxy_targets"])
        self._validate_schedule_strategy(execution_mode, raw_targets)
        account_scope, requested_ids, upstream_accounts = await self._resolve_accounts(plan)
        targets = await self.target_validator.validate(raw_targets, execution_mode=execution_mode)
        available_accounts, missing, invalid, diagnostic = self._select_accounts(
            requested_ids,
            upstream_accounts,
            targets,
            allow_disabled=account_scope == "quarantined",
        )
        async with self.enqueue_lock:
            result = self.repository.create_plan_runs_batch(
                plan_id=plan_id,
                accounts=available_accounts,
                profile_ids=profile_ids,
                execution_mode=execution_mode,
                rounds=int(plan["rounds"]),
                proxy_targets=targets,
                priority=int(plan["priority"]),
                queue_limit=self.settings.probe_queue_limit,
                register_cooldown_minutes=(
                    self.settings.scheduled_probe_register_cooldown_minutes
                ),
            )
        if result["runIds"]:
            self.wake.set()
        return self._result(
            account_scope=account_scope,
            requested_ids=requested_ids,
            missing=missing,
            invalid=invalid,
            diagnostic=diagnostic,
            repository_result=result,
        )

    @staticmethod
    def _profile_ids(plan: dict[str, Any]) -> list[str]:
        return list(
            dict.fromkeys(
                str(value).strip()
                for value in (plan.get("profile_ids") or [plan["profile_id"]])
                if str(value).strip()
            )
        )

    @staticmethod
    def _validate_schedule_strategy(
        execution_mode: str, raw_targets: list[dict[str, Any]]
    ) -> None:
        if execution_mode != "chat" or any(
            str(target.get("kind") or "") != "current" for target in raw_targets
        ):
            raise ValueError("Cron 周期巡检仅支持完整对话和账号当前出口")

    async def _resolve_accounts(
        self, plan: dict[str, Any]
    ) -> tuple[str, set[int], list[dict[str, Any]]]:
        account_scope = str(plan.get("account_scope") or "fixed")
        if account_scope == "fixed":
            requested_ids = {int(value) for value in plan["account_ids"]}
            return account_scope, requested_ids, await self.client.list_all_accounts(requested_ids)
        if account_scope not in {"all_enabled", "risky_enabled", "quarantined"}:
            raise ValueError("Cron 计划账号范围无效")
        if account_scope == "quarantined":
            # Isolated accounts are usually disabled upstream. The call runner
            # activates them in diagnostic mode for each sample, so neither the
            # enabled filter below nor target validation may drop them.
            quarantined_ids = self.accounts.quarantined_account_ids()
            upstream_accounts = [
                account
                for account in await self.client.list_all_accounts()
                if int(account.get("id") or 0) in quarantined_ids
            ]
            requested_ids = {int(account.get("id") or 0) for account in upstream_accounts}
            requested_ids.discard(0)
            return account_scope, requested_ids, upstream_accounts
        upstream_accounts = await self.client.list_all_accounts()
        upstream_accounts = [
            account for account in upstream_accounts if bool(account.get("enabled"))
        ]
        if account_scope == "risky_enabled":
            risky_account_ids = self.accounts.risky_account_ids()
            upstream_accounts = [
                account
                for account in upstream_accounts
                if int(account.get("id") or 0) in risky_account_ids
            ]
        requested_ids = {int(account.get("id") or 0) for account in upstream_accounts}
        requested_ids.discard(0)
        return account_scope, requested_ids, upstream_accounts

    @staticmethod
    def _select_accounts(
        requested_ids: set[int],
        upstream_accounts: list[dict[str, Any]],
        targets: list[dict[str, Any]],
        *,
        allow_disabled: bool = False,
    ) -> tuple[list[dict[str, Any]], list[int], list[dict[str, Any]], list[int]]:
        by_id = {int(value.get("id") or 0): value for value in upstream_accounts}
        missing: list[int] = []
        invalid: list[dict[str, Any]] = []
        diagnostic: list[int] = []
        available_accounts: list[dict[str, Any]] = []
        for account_id in sorted(requested_ids):
            account = by_id.get(account_id)
            if account is None:
                missing.append(account_id)
                continue
            try:
                ProbeTargetValidator.validate_account_for_targets(
                    account, targets, allow_disabled=allow_disabled
                )
            except ValueError as exc:
                invalid.append({"id": account_id, "reason": str(exc)})
                continue
            if not bool(account.get("enabled")):
                diagnostic.append(account_id)
            available_accounts.append(account)
        return available_accounts, missing, invalid, diagnostic

    @staticmethod
    def _result(
        *,
        account_scope: str,
        requested_ids: set[int],
        missing: list[int],
        invalid: list[dict[str, Any]],
        diagnostic: list[int],
        repository_result: dict[str, Any],
    ) -> dict[str, Any]:
        skipped_account_ids = set(missing)
        skipped_account_ids.update(item["id"] for item in invalid)
        skipped_account_ids.update(repository_result["activeAccountIds"])
        skipped_account_ids.update(repository_result["restoreBlockedAccountIds"])
        skipped_account_ids.update(repository_result["registerCooldownAccountIds"])
        return {
            "accountScope": account_scope,
            "resolvedAccountCount": len(requested_ids),
            "created": len(repository_result["runIds"]),
            "skipped": len(skipped_account_ids),
            "missingAccountIds": missing,
            "invalidAccounts": invalid,
            "diagnosticAccountIds": diagnostic,
            "activeAccountIds": repository_result["activeAccountIds"],
            "restoreBlockedAccountIds": repository_result["restoreBlockedAccountIds"],
            "registerCooldownAccountIds": repository_result["registerCooldownAccountIds"],
            "profileIds": repository_result["profileIds"],
            "runIds": repository_result["runIds"],
        }
