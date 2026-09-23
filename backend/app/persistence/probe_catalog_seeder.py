from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.clock import utc_now

from .models import (
    MetadataRow,
    ProbeDurationEstimate,
    ProbePlan,
    ProbeProfile,
    ProbeRun,
    ProbeSample,
)
from .seeds import DEFAULT_PROFILES

DEFAULT_PROFILES_UNLIMITED_MIGRATION_KEY = "default_probe_profiles_follow_upstream_v1"
DEFAULT_PROFILES_EXPECTED_OUTPUT_MIGRATION_KEY = "default_probe_profiles_expected_output_v1"
DEFAULT_QUALITY_MARKER_MIGRATION_KEY = "default_probe_profiles_quality_marker_cn_v1"
DEFAULT_HTML_PREVIEW_MIGRATION_KEY = "default_probe_profiles_html_preview_pelican_v1"
DEFAULT_HTML_PREVIEW_MARKER_MIGRATION_KEY = "default_probe_profiles_html_preview_svg_marker_v1"
DEFAULT_REASONING_CHECK_MIGRATION_KEY = "default_probe_profiles_reasoning_check_hidden_answer_v1"
DEFAULT_PROFILES_MODEL_MIGRATION_KEY = "default_probe_profiles_model_grok_4_7_v1"
PROBE_DURATION_ESTIMATE_BACKFILL_KEY = "probe_duration_estimates_backfill_v1"
SAFE_CURRENT_EGRESS_MIGRATION_KEY = "probe_targets_current_egress_v1"
# grok-4.5 is no longer served upstream; built-in profiles still pointing at
# it cannot create a probe route and fail before the first request.
LEGACY_DEFAULT_PROFILE_MODEL = "grok-4.5"
LEGACY_QUALITY_MARKER_FIELDS = {
    "prompt": "先用三点总结为什么天空呈蓝色，最后一行只输出 QUALITY_OK。",
    "expected_text": "QUALITY_OK",
    "expected_output": "最后一行应包含 `QUALITY_OK`。",
}
# The DOCTYPE marker failed on the lowercase ``<!doctype html>`` many models
# emit and on pure-SVG answers such as the profile's own reference output.
LEGACY_HTML_PREVIEW_MARKER_FIELDS = {"expected_text": "<!DOCTYPE html>"}
# The original prompt spelled out the answer, so a degraded model could echo
# ``RESULT=53`` without computing anything.
LEGACY_REASONING_CHECK_FIELDS = {
    "prompt": "有 3 个盒子，各装 4 个袋子，每袋 5 个球。移走 7 个后剩多少？解释后最后输出 RESULT=53。",
    "expected_output": "计算结果应为 **53**，最后输出 `RESULT=53`。",
}
LEGACY_HTML_PREVIEW_FIELDS = {
    "name": "HTML 生成基线",
    "prompt": "生成一个深色风格的服务状态卡片 HTML，包含正常、观察、风险三种状态。",
    "expected_output": """<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>服务状态卡片</title>
    <style>
      body { margin: 0; padding: 32px; background: #09090b; color: #fafafa; font-family: sans-serif; }
      .grid { display: grid; gap: 16px; grid-template-columns: repeat(3, minmax(0, 1fr)); }
      .card { padding: 20px; border: 1px solid #27272a; border-radius: 14px; background: #18181b; }
      .ok { color: #34d399; } .watch { color: #fbbf24; } .risk { color: #fb7185; }
    </style>
  </head>
  <body>
    <main class="grid">
      <section class="card ok">正常</section>
      <section class="card watch">观察</section>
      <section class="card risk">风险</section>
    </main>
  </body>
</html>""",
}


class ProbeCatalogSeeder:
    """Seeds built-in profiles and applies one-time probe data migrations."""

    def seed(self, session: Session) -> None:
        self._create_missing_profiles(session)
        self._apply_unlimited_output_migration(session)
        self._apply_expected_output_migration(session)
        self._apply_profile_field_migration(
            session,
            key=DEFAULT_QUALITY_MARKER_MIGRATION_KEY,
            profile_id="quality-marker",
            legacy_fields=LEGACY_QUALITY_MARKER_FIELDS,
        )
        self._apply_profile_field_migration(
            session,
            key=DEFAULT_HTML_PREVIEW_MIGRATION_KEY,
            profile_id="html-preview",
            legacy_fields=LEGACY_HTML_PREVIEW_FIELDS,
        )
        self._apply_profile_field_migration(
            session,
            key=DEFAULT_HTML_PREVIEW_MARKER_MIGRATION_KEY,
            profile_id="html-preview",
            legacy_fields=LEGACY_HTML_PREVIEW_MARKER_FIELDS,
        )
        self._apply_profile_field_migration(
            session,
            key=DEFAULT_REASONING_CHECK_MIGRATION_KEY,
            profile_id="reasoning-check",
            legacy_fields=LEGACY_REASONING_CHECK_FIELDS,
        )
        self._apply_default_model_migration(session)
        self._backfill_duration_estimates(session)
        self._migrate_current_egress_targets(session)

    @staticmethod
    def _create_missing_profiles(session: Session) -> None:
        for values in DEFAULT_PROFILES:
            if session.get(ProbeProfile, values["id"]) is None:
                session.add(ProbeProfile(**values))

    def _apply_unlimited_output_migration(self, session: Session) -> None:
        if self._migration_applied(session, DEFAULT_PROFILES_UNLIMITED_MIGRATION_KEY):
            return
        for values in DEFAULT_PROFILES:
            profile = session.get(ProbeProfile, values["id"])
            if profile is not None:
                profile.max_output_tokens = 0
                profile.updated_at = utc_now()
        self._mark_migration(session, DEFAULT_PROFILES_UNLIMITED_MIGRATION_KEY)

    def _apply_expected_output_migration(self, session: Session) -> None:
        if self._migration_applied(session, DEFAULT_PROFILES_EXPECTED_OUTPUT_MIGRATION_KEY):
            return
        for values in DEFAULT_PROFILES:
            profile = session.get(ProbeProfile, values["id"])
            if profile is not None and not profile.expected_output:
                profile.expected_output = str(values.get("expected_output") or "")
                profile.updated_at = utc_now()
        self._mark_migration(session, DEFAULT_PROFILES_EXPECTED_OUTPUT_MIGRATION_KEY)

    def _apply_default_model_migration(self, session: Session) -> None:
        """Move every built-in profile still on the retired model to the current one.

        Profiles the operator already pointed elsewhere are left untouched.
        """

        if self._migration_applied(session, DEFAULT_PROFILES_MODEL_MIGRATION_KEY):
            return
        for values in DEFAULT_PROFILES:
            profile = session.get(ProbeProfile, values["id"])
            if profile is not None and profile.model == LEGACY_DEFAULT_PROFILE_MODEL:
                profile.model = str(values["model"])
                profile.updated_at = utc_now()
        self._mark_migration(session, DEFAULT_PROFILES_MODEL_MIGRATION_KEY)

    def _apply_profile_field_migration(
        self,
        session: Session,
        *,
        key: str,
        profile_id: str,
        legacy_fields: dict[str, Any],
    ) -> None:
        if self._migration_applied(session, key):
            return
        defaults = self._profile_defaults(profile_id)
        profile = session.get(ProbeProfile, profile_id)
        changed = False
        if profile is not None:
            for field, legacy_value in legacy_fields.items():
                if getattr(profile, field) == legacy_value:
                    setattr(profile, field, defaults[field])
                    changed = True
            if changed:
                profile.updated_at = utc_now()
        self._mark_migration(session, key)

    def _backfill_duration_estimates(self, session: Session) -> None:
        if self._migration_applied(session, PROBE_DURATION_ESTIMATE_BACKFILL_KEY):
            return
        session.execute(delete(ProbeDurationEstimate))
        rows = session.execute(
            select(
                ProbeRun.profile_id,
                ProbeRun.execution_mode,
                func.count(ProbeSample.id),
                func.sum(ProbeSample.duration_ms),
            )
            .join(ProbeSample, ProbeSample.run_id == ProbeRun.id)
            .where(ProbeSample.duration_ms > 0)
            .group_by(ProbeRun.profile_id, ProbeRun.execution_mode)
        ).all()
        now = utc_now()
        session.add_all(
            [
                ProbeDurationEstimate(
                    profile_id=str(profile_id),
                    execution_mode=str(execution_mode or "chat"),
                    sample_count=int(sample_count or 0),
                    total_duration_ms=int(total_duration_ms or 0),
                    created_at=now,
                    updated_at=now,
                )
                for profile_id, execution_mode, sample_count, total_duration_ms in rows
            ]
        )
        self._mark_migration(session, PROBE_DURATION_ESTIMATE_BACKFILL_KEY, value=now.isoformat())

    def _migrate_current_egress_targets(self, session: Session) -> None:
        if self._migration_applied(session, SAFE_CURRENT_EGRESS_MIGRATION_KEY):
            return
        for plan in session.scalars(
            select(ProbePlan).where(ProbePlan.execution_mode == "chat")
        ).all():
            if self.is_legacy_direct_only(plan.proxy_targets):
                plan.proxy_targets = [
                    {"kind": "current", "id": None, "name": "账号当前出口"}
                ]
                plan.updated_at = utc_now()
        for run in session.scalars(
            select(ProbeRun).where(
                ProbeRun.execution_mode == "chat",
                ProbeRun.status == "queued",
            )
        ).all():
            if self.is_legacy_direct_only(run.proxy_targets):
                run.proxy_targets = [
                    {"kind": "current", "id": None, "name": "账号当前出口"}
                ]
                run.current_target_key = None
        self._mark_migration(session, SAFE_CURRENT_EGRESS_MIGRATION_KEY)

    @staticmethod
    def is_legacy_direct_only(targets: Any) -> bool:
        return (
            isinstance(targets, list)
            and len(targets) == 1
            and isinstance(targets[0], dict)
            and targets[0].get("kind") == "direct"
        )

    @staticmethod
    def _migration_applied(session: Session, key: str) -> bool:
        return session.get(MetadataRow, key) is not None

    @staticmethod
    def _mark_migration(session: Session, key: str, *, value: str | None = None) -> None:
        session.add(MetadataRow(key=key, value=value or utc_now().isoformat()))

    @staticmethod
    def _profile_defaults(profile_id: str) -> dict[str, Any]:
        return next(values for values in DEFAULT_PROFILES if values["id"] == profile_id)
