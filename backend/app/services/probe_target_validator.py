from __future__ import annotations

from typing import Any

from app.integrations.grok2api.client import Grok2APIClient


class ProbeTargetValidator:
    """Validates probe targets against live upstream egress inventory."""

    def __init__(self, client: Grok2APIClient):
        self.client = client

    async def validate(
        self, targets: list[dict[str, Any]], *, execution_mode: str = "chat"
    ) -> list[dict[str, Any]]:
        self._validate_selection(targets, execution_mode)
        nodes_by_id = await self._load_requested_nodes(targets)
        return self._normalize(targets, nodes_by_id)

    @staticmethod
    def validate_account(account: dict[str, Any]) -> None:
        account_id = int(account.get("id") or 0)
        auth_status = str(account.get("authStatus") or "")
        if auth_status and auth_status != "active":
            raise ValueError(f"账号 {account_id} 当前鉴权状态为 {auth_status}，暂不具备探针执行条件")

    @classmethod
    def validate_account_for_targets(
        cls,
        account: dict[str, Any],
        targets: list[dict[str, Any]],
        *,
        allow_disabled: bool = False,
    ) -> None:
        """Validate one account for the selected targets.

        ``allow_disabled`` is reserved for isolation re-checks: the call
        runner activates the account in diagnostic mode for each sample and
        restores the recorded settings afterwards.
        """

        cls.validate_account(account)
        if not any(target.get("kind") == "current" for target in targets):
            return
        account_id = int(account.get("id") or 0)
        if not bool(account.get("enabled")) and not allow_disabled:
            raise ValueError(f"账号 {account_id} 已停用，正常定检不会临时激活；请启用账号或改用人工诊断")
        if int(account.get("egressNodeId") or 0) <= 0:
            raise ValueError(f"账号 {account_id} 未绑定固定出口；请先在 grok2api 绑定账号出口")

    @staticmethod
    def _validate_selection(targets: list[dict[str, Any]], execution_mode: str) -> None:
        if not targets:
            raise ValueError("至少选择一个账号当前出口或诊断出口目标")
        if execution_mode not in {"chat", "quality_test"}:
            raise ValueError("探针执行模式无效")
        if execution_mode == "quality_test" and any(target.get("kind") != "egress" for target in targets):
            raise ValueError("快速出口质量探针仅支持 grok_build 出口节点")
        if any(target.get("kind") == "current" for target in targets) and any(
            target.get("kind") != "current" for target in targets
        ):
            raise ValueError("账号当前出口不能与诊断出口混用，请分别创建定检和诊断任务")

    async def _load_requested_nodes(
        self, targets: list[dict[str, Any]]
    ) -> dict[int, dict[str, Any]]:
        requested_node_ids = {
            int(target.get("id") or 0) for target in targets if target.get("kind") == "egress"
        }
        requested_node_ids.discard(0)
        nodes_by_id: dict[int, dict[str, Any]] = {}
        if not requested_node_ids:
            return nodes_by_id
        page = 1
        while True:
            payload = await self.client.list_egress_nodes(page=page, pageSize=500)
            batch = list(payload.get("items", []))
            for node in batch:
                node_id = int(node.get("id") or 0)
                if node_id in requested_node_ids:
                    nodes_by_id[node_id] = node
            if requested_node_ids <= nodes_by_id.keys():
                break
            if not batch or page * int(payload.get("pageSize") or 500) >= int(payload.get("total") or 0):
                break
            page += 1
        return nodes_by_id

    def _normalize(
        self,
        targets: list[dict[str, Any]],
        nodes_by_id: dict[int, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for target in targets:
            value, key = self._normalize_target(target, nodes_by_id)
            if key not in seen:
                normalized.append(value)
                seen.add(key)
        return normalized

    @staticmethod
    def _normalize_target(
        target: dict[str, Any], nodes_by_id: dict[int, dict[str, Any]]
    ) -> tuple[dict[str, Any], str]:
        kind = str(target.get("kind") or "")
        if kind == "current":
            return {"kind": "current", "id": None, "name": "账号当前出口"}, "current"
        if kind == "direct":
            return {"kind": "direct", "id": None, "name": "上游调度（诊断）"}, "direct"
        if kind != "egress":
            raise ValueError("代理目标 kind 必须为 current、direct 或 egress")
        node_id = int(target.get("id") or 0)
        node = nodes_by_id.get(node_id)
        if node is None:
            raise ValueError(f"出口节点 {node_id} 不存在")
        if not node.get("enabled") or not node.get("proxyConfigured"):
            raise ValueError(f"出口节点 {node_id} 未启用或未配置代理")
        return (
            {"kind": "egress", "id": node_id, "name": str(node.get("name") or node_id)},
            f"egress:{node_id}",
        )
