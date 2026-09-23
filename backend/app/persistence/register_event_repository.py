from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.core.clock import utc_now

from .database import Database
from .models import RegisterCallbackDelivery, RegisterWebhookEvent, model_dict

PRIORITY_HOLD_NONE = "none"
PRIORITY_HOLD_HELD = "held"
PRIORITY_HOLD_RESTORED = "restored"
PRIORITY_HOLD_RESTORE_FAILED = "restore_failed"
PRIORITY_HOLD_KEPT = "kept"
UNRESOLVED_PRIORITY_HOLD_STATUSES = (
    PRIORITY_HOLD_HELD,
    PRIORITY_HOLD_RESTORE_FAILED,
)
# Holds a later clean probe run may still lift.  ``kept`` only records the
# register probe's own verdict; it is not final once newer evidence is clean.
LIFTABLE_PRIORITY_HOLD_STATUSES = (
    PRIORITY_HOLD_HELD,
    PRIORITY_HOLD_RESTORE_FAILED,
    PRIORITY_HOLD_KEPT,
)


class RegisterEventConflictError(ValueError):
    pass


class RegisterEventRepository:
    def __init__(self, database: Database):
        self.database = database

    def receive(self, values: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        event_id = str(values["event_id"])
        now = utc_now()
        incoming_sso = str(values.get("sso") or "").strip()
        try:
            with self.database.transaction() as session:
                event = session.get(RegisterWebhookEvent, event_id)
                if event is not None:
                    return self._existing_event(event, values), False
                event = RegisterWebhookEvent(
                    event_id=event_id,
                    event_type=str(
                        values.get("event_type") or "grok2api.account_imported"
                    ),
                    registration_id=str(values.get("registration_id") or ""),
                    email=str(values["email"]).lower(),
                    sso=incoming_sso,
                    sso_received_at=now if incoming_sso else None,
                    grok2api_account_id=values.get("grok2api_account_id"),
                    bot_risk=bool(values.get("bot_risk")),
                    bfs=(
                        "" if values.get("bfs") is None else str(values.get("bfs"))
                    ),
                    occurred_at=str(values.get("occurred_at") or ""),
                    status="pending",
                    attempts=0,
                    next_attempt_at=now,
                    created_at=now,
                    updated_at=now,
                )
                session.add(event)
                return model_dict(event), True
        except IntegrityError:
            # Concurrent at-least-once deliveries can race on the primary key.
            with self.database.transaction() as session:
                event = session.get(RegisterWebhookEvent, event_id)
                if event is None:
                    raise
                return self._existing_event(event, values), False

    def list_events(
        self,
        *,
        page: int,
        page_size: int,
        status: str = "",
        search: str = "",
    ) -> dict[str, Any]:
        filters: list[Any] = []
        if status:
            filters.append(RegisterWebhookEvent.status == status)
        token = search.strip().lower()
        if token:
            search_filters = [
                func.lower(RegisterWebhookEvent.event_id).contains(token),
                func.lower(RegisterWebhookEvent.event_type).contains(token),
                func.lower(RegisterWebhookEvent.registration_id).contains(token),
                func.lower(RegisterWebhookEvent.email).contains(token),
            ]
            if token.isdigit():
                numeric_token = int(token)
                search_filters.extend(
                    [
                        RegisterWebhookEvent.grok2api_account_id == numeric_token,
                        RegisterWebhookEvent.resolved_account_id == numeric_token,
                    ]
                )
            filters.append(or_(*search_filters))

        now = utc_now()
        with self.database.session() as session:
            total = int(
                session.scalar(
                    select(func.count(RegisterWebhookEvent.event_id)).where(*filters)
                )
                or 0
            )
            events = session.scalars(
                select(RegisterWebhookEvent)
                .where(*filters)
                .order_by(RegisterWebhookEvent.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
            status_counts = {
                "pending": 0,
                "processing": 0,
                "completed": 0,
                "failed": 0,
            }
            for event_status, count in session.execute(
                select(
                    RegisterWebhookEvent.status,
                    func.count(RegisterWebhookEvent.event_id),
                ).group_by(RegisterWebhookEvent.status)
            ).all():
                status_counts[str(event_status)] = int(count or 0)
            due_count = int(
                session.scalar(
                    select(func.count(RegisterWebhookEvent.event_id)).where(
                        RegisterWebhookEvent.status == "pending",
                        RegisterWebhookEvent.next_attempt_at <= now,
                    )
                )
                or 0
            )
            retrying_count = int(
                session.scalar(
                    select(func.count(RegisterWebhookEvent.event_id)).where(
                        RegisterWebhookEvent.status == "pending",
                        RegisterWebhookEvent.attempts > 0,
                    )
                )
                or 0
            )

        return {
            "items": [
                {
                    key: value
                    for key, value in model_dict(event).items()
                    if key != "sso"
                }
                for event in events
            ],
            "total": total,
            "page": page,
            "pageSize": page_size,
            "statusCounts": status_counts,
            "dueCount": due_count,
            "retryingCount": retrying_count,
        }

    @staticmethod
    def _existing_event(
        event: RegisterWebhookEvent,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        if event.email.lower() != str(values["email"]).lower():
            raise RegisterEventConflictError("event_id 已被其他邮箱使用")
        incoming_sso = str(values.get("sso") or "").strip()
        if incoming_sso and (
            incoming_sso != str(event.sso or "") or event.sso_received_at is None
        ):
            now = utc_now()
            event.sso = incoming_sso
            event.sso_received_at = now
            event.updated_at = now
        return model_dict(event)

    def sso_for_accounts(self, account_ids: list[int]) -> dict[int, dict[str, str]]:
        """Return the latest non-empty raw SSO received for each account."""
        normalized = sorted({int(value) for value in account_ids if int(value) > 0})
        if not normalized:
            return {}
        with self.database.session() as session:
            rows = session.execute(
                select(
                    RegisterWebhookEvent.resolved_account_id,
                    RegisterWebhookEvent.grok2api_account_id,
                    RegisterWebhookEvent.email,
                    RegisterWebhookEvent.sso,
                )
                .where(
                    RegisterWebhookEvent.sso != "",
                    or_(
                        RegisterWebhookEvent.resolved_account_id.in_(normalized),
                        RegisterWebhookEvent.grok2api_account_id.in_(normalized),
                    ),
                )
                .order_by(
                    RegisterWebhookEvent.sso_received_at.desc(),
                    RegisterWebhookEvent.created_at.desc(),
                )
            ).all()
        result: dict[int, dict[str, str]] = {}
        requested = set(normalized)
        for resolved_id, upstream_id, email, sso in rows:
            resolved_account_id = int(resolved_id or 0)
            grok2api_account_id = int(upstream_id or 0)
            account_id = (
                resolved_account_id
                if resolved_account_id in requested
                else grok2api_account_id
                if grok2api_account_id in requested
                else 0
            )
            if account_id and account_id not in result:
                result[account_id] = {
                    "email": str(email or ""),
                    "sso": str(sso or ""),
                }
        return result

    def account_ids_with_sso(self, account_ids: list[int]) -> set[int]:
        normalized = list(dict.fromkeys(int(value) for value in account_ids if value))
        if not normalized:
            return set()
        requested = set(normalized)
        with self.database.session() as session:
            rows = session.execute(
                select(
                    RegisterWebhookEvent.resolved_account_id,
                    RegisterWebhookEvent.grok2api_account_id,
                ).where(
                    RegisterWebhookEvent.sso != "",
                    or_(
                        RegisterWebhookEvent.resolved_account_id.in_(normalized),
                        RegisterWebhookEvent.grok2api_account_id.in_(normalized),
                    ),
                )
            ).all()
        result: set[int] = set()
        for resolved_account_id, grok2api_account_id in rows:
            for value in (resolved_account_id, grok2api_account_id):
                account_id = int(value or 0)
                if account_id in requested:
                    result.add(account_id)
        return result

    def recover_processing(self) -> int:
        now = utc_now()
        with self.database.transaction() as session:
            result = session.execute(
                update(RegisterWebhookEvent)
                .where(RegisterWebhookEvent.status == "processing")
                .values(status="pending", next_attempt_at=now, updated_at=now)
            )
            return int(result.rowcount or 0)

    def bind_account(self, event_id: str, account_id: int) -> None:
        now = utc_now()
        with self.database.transaction() as session:
            event = session.get(RegisterWebhookEvent, event_id)
            if event is None:
                return
            event.resolved_account_id = int(account_id)
            event.updated_at = now

    def claim_due(self) -> dict[str, Any] | None:
        now = utc_now()
        with self.database.transaction() as session:
            event = session.scalar(
                select(RegisterWebhookEvent)
                .where(
                    RegisterWebhookEvent.status == "pending",
                    RegisterWebhookEvent.next_attempt_at <= now,
                )
                .order_by(
                    RegisterWebhookEvent.next_attempt_at.asc(),
                    RegisterWebhookEvent.created_at.asc(),
                )
                .limit(1)
            )
            if event is None:
                return None
            event.status = "processing"
            event.attempts += 1
            event.updated_at = now
            return model_dict(event)

    def retry(self, event_id: str, error: str, delay_seconds: float) -> None:
        now = utc_now()
        with self.database.transaction() as session:
            event = session.get(RegisterWebhookEvent, event_id)
            if event is None or event.status == "completed":
                return
            event.status = "pending"
            event.last_error = str(error)[:4000]
            event.next_attempt_at = now + timedelta(seconds=max(delay_seconds, 1))
            event.updated_at = now

    def fail(self, event_id: str, error: str) -> None:
        now = utc_now()
        with self.database.transaction() as session:
            event = session.get(RegisterWebhookEvent, event_id)
            if event is None or event.status == "completed":
                return
            event.status = "failed"
            event.last_error = str(error)[:4000]
            event.updated_at = now
            event.completed_at = now

    def complete(self, event_id: str, account_id: int, run_ids: list[str]) -> None:
        now = utc_now()
        with self.database.transaction() as session:
            event = session.get(RegisterWebhookEvent, event_id)
            if event is None:
                return
            event.status = "completed"
            event.last_error = ""
            event.resolved_account_id = account_id
            event.run_ids = list(run_ids)
            event.updated_at = now
            event.completed_at = now

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            event = session.get(RegisterWebhookEvent, event_id)
            return model_dict(event) if event is not None else None

    def list_created_between(
        self, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        with self.database.session() as session:
            events = session.scalars(
                select(RegisterWebhookEvent)
                .where(
                    RegisterWebhookEvent.created_at >= start,
                    RegisterWebhookEvent.created_at < end,
                )
                .order_by(RegisterWebhookEvent.created_at.asc())
            ).all()
            return [
                {
                    key: value
                    for key, value in model_dict(event).items()
                    if key != "sso"
                }
                for event in events
            ]

    def list_unresolved_priority_holds(self) -> list[dict[str, Any]]:
        with self.database.session() as session:
            events = session.scalars(
                select(RegisterWebhookEvent)
                .where(
                    RegisterWebhookEvent.priority_hold_status.in_(
                        UNRESOLVED_PRIORITY_HOLD_STATUSES
                    )
                )
                .order_by(RegisterWebhookEvent.priority_held_at.asc())
            ).all()
            return [model_dict(event) for event in events]

    def list_liftable_priority_holds_for_account(
        self, account_id: int
    ) -> list[dict[str, Any]]:
        """Return the account's still-effective priority holds, newest first.

        An event belongs to the account it restores, i.e. ``resolved_account_id``
        when bound and otherwise the ``grok2api_account_id`` it arrived with.
        """

        normalized = int(account_id or 0)
        if normalized <= 0:
            return []
        with self.database.session() as session:
            events = session.scalars(
                select(RegisterWebhookEvent)
                .where(
                    RegisterWebhookEvent.priority_hold_status.in_(
                        LIFTABLE_PRIORITY_HOLD_STATUSES
                    ),
                    or_(
                        RegisterWebhookEvent.resolved_account_id == normalized,
                        and_(
                            RegisterWebhookEvent.resolved_account_id.is_(None),
                            RegisterWebhookEvent.grok2api_account_id == normalized,
                        ),
                    ),
                )
                .order_by(
                    RegisterWebhookEvent.created_at.desc(),
                    RegisterWebhookEvent.event_id.desc(),
                )
            ).all()
            return [model_dict(event) for event in events]

    def mark_priority_hold(
        self,
        event_id: str,
        *,
        original_priority: int,
        held_priority: int,
    ) -> dict[str, Any] | None:
        now = utc_now()
        with self.database.transaction() as session:
            event = session.get(RegisterWebhookEvent, event_id)
            if event is None:
                return None
            if event.priority_hold_status in {
                PRIORITY_HOLD_RESTORED,
                PRIORITY_HOLD_KEPT,
            }:
                return model_dict(event)
            if event.original_priority is None:
                event.original_priority = int(original_priority)
            event.held_priority = int(held_priority)
            event.priority_hold_status = PRIORITY_HOLD_HELD
            event.priority_hold_error = ""
            if event.priority_held_at is None:
                event.priority_held_at = now
            event.updated_at = now
            return model_dict(event)

    def mark_priority_restored(self, event_id: str) -> None:
        now = utc_now()
        with self.database.transaction() as session:
            event = session.get(RegisterWebhookEvent, event_id)
            if event is None:
                return
            event.priority_hold_status = PRIORITY_HOLD_RESTORED
            event.priority_hold_error = ""
            event.priority_restored_at = now
            event.updated_at = now

    def mark_priority_restore_failed(self, event_id: str, error: str) -> None:
        now = utc_now()
        with self.database.transaction() as session:
            event = session.get(RegisterWebhookEvent, event_id)
            if event is None:
                return
            event.priority_hold_status = PRIORITY_HOLD_RESTORE_FAILED
            event.priority_hold_error = str(error)[:4000]
            event.updated_at = now

    def mark_priority_kept(self, event_id: str, error: str = "") -> None:
        now = utc_now()
        with self.database.transaction() as session:
            event = session.get(RegisterWebhookEvent, event_id)
            if event is None:
                return
            event.priority_hold_status = PRIORITY_HOLD_KEPT
            event.priority_hold_error = str(error)[:4000]
            event.updated_at = now

    def enqueue_callback(
        self, event_id: str, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        now = utc_now()
        with self.database.transaction() as session:
            row = session.get(RegisterCallbackDelivery, event_id)
            if row is None:
                row = RegisterCallbackDelivery(
                    event_id=event_id,
                    status="pending",
                    attempts=0,
                    last_error="",
                    payload=dict(payload),
                    next_attempt_at=now,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
                return model_dict(row)
            if row.status == "delivered":
                return model_dict(row)
            row.payload = dict(payload)
            if row.status == "failed":
                row.status = "pending"
                row.next_attempt_at = now
                row.last_error = ""
            row.updated_at = now
            return model_dict(row)

    def recover_callback_processing(self) -> int:
        now = utc_now()
        with self.database.transaction() as session:
            result = session.execute(
                update(RegisterCallbackDelivery)
                .where(RegisterCallbackDelivery.status == "processing")
                .values(status="pending", next_attempt_at=now, updated_at=now)
            )
            return int(result.rowcount or 0)

    def claim_callback_due(self) -> dict[str, Any] | None:
        now = utc_now()
        with self.database.transaction() as session:
            row = session.scalar(
                select(RegisterCallbackDelivery)
                .where(
                    RegisterCallbackDelivery.status == "pending",
                    RegisterCallbackDelivery.next_attempt_at <= now,
                )
                .order_by(
                    RegisterCallbackDelivery.next_attempt_at.asc(),
                    RegisterCallbackDelivery.created_at.asc(),
                )
                .limit(1)
            )
            if row is None:
                return None
            row.status = "processing"
            row.attempts += 1
            row.updated_at = now
            return model_dict(row)

    def retry_callback(
        self, event_id: str, error: str, delay_seconds: float
    ) -> None:
        now = utc_now()
        with self.database.transaction() as session:
            row = session.get(RegisterCallbackDelivery, event_id)
            if row is None or row.status == "delivered":
                return
            row.status = "pending"
            row.last_error = str(error)[:4000]
            row.next_attempt_at = now + timedelta(seconds=max(delay_seconds, 1))
            row.updated_at = now

    def fail_callback(self, event_id: str, error: str) -> None:
        now = utc_now()
        with self.database.transaction() as session:
            row = session.get(RegisterCallbackDelivery, event_id)
            if row is None or row.status == "delivered":
                return
            row.status = "failed"
            row.last_error = str(error)[:4000]
            row.updated_at = now
            row.completed_at = now

    def complete_callback(self, event_id: str) -> None:
        now = utc_now()
        with self.database.transaction() as session:
            row = session.get(RegisterCallbackDelivery, event_id)
            if row is None:
                return
            row.status = "delivered"
            row.last_error = ""
            row.updated_at = now
            row.completed_at = now

    def get_callback(self, event_id: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            row = session.get(RegisterCallbackDelivery, event_id)
            return model_dict(row) if row is not None else None
