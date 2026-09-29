from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from curl_cffi.requests import AsyncSession as CurlAsyncSession

WHITE_API_URL = "https://white.1024proxy.com/white/api"
_REGION_RE = re.compile(r"^[A-Za-z0-9]{1,16}$")
_NUM_RE = re.compile(r"^[1-9]\d{0,3}$")
_FORMATS = {"n", "rn", "1", "2"}
_TYPES = {"txt", "json"}
_ENDPOINT_RE = re.compile(
    r"(?<![\d.])(?P<ip>(?:\d{1,3}\.){3}\d{1,3}):(?P<port>\d{1,5})(?!\d)"
)
_PREFERRED_KEYS = (
    "data",
    "list",
    "result",
    "results",
    "proxies",
    "rows",
    "items",
)
_MESSAGE_KEYS = ("msg", "message", "error", "detail", "errmsg")
_SKIP_KEYS = frozenset({*_MESSAGE_KEYS, "code", "success", "status"})
_SUCCESS_CODES = {"0", "200"}
_SUCCESS_TEXT = {"success", "ok", "true", "请求成功", "成功"}
_MAX_PROXIES = 10_000


class Proxy1024Error(RuntimeError):
    """The 1024proxy whitelist API failed or returned no proxy endpoint."""


class Proxy1024Client:
    """Extract proxy IP and port pairs from the 1024proxy whitelist API."""

    async def extract(
        self,
        *,
        region: str,
        data_format: str,
        session_time: int,
        num: str | None = None,
        data_type: str | None = None,
    ) -> list[dict[str, Any]]:
        params = _request_params(
            region=region,
            data_format=data_format,
            session_time=session_time,
            num=num,
            data_type=data_type,
        )
        try:
            async with CurlAsyncSession(trust_env=False) as client:
                response = await client.get(WHITE_API_URL, params=params, timeout=20)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise Proxy1024Error(f"调用 1024proxy 失败: {exc}") from exc
        status_code = int(getattr(response, "status_code", 0) or 0)
        body = str(getattr(response, "text", "") or "")
        if status_code >= 300:
            detail = _clip(body) or "空响应"
            raise Proxy1024Error(f"1024proxy 返回 HTTP {status_code}: {detail}")
        return parse_proxy_endpoints(body)


def parse_proxy_endpoints(body: str) -> list[dict[str, Any]]:
    """Normalize a txt or json whitelist response into IP and port pairs."""

    text = body.strip().lstrip("\ufeff")
    if not text:
        raise Proxy1024Error("1024proxy 返回空响应")
    payload = _json_payload(text)
    if payload is None:
        proxies = _from_text(text)
        if proxies:
            return _limit(proxies)
        raise Proxy1024Error(f"1024proxy 提取失败：{_clip(text)}")
    if _explicit_error(payload):
        message = _message(payload) or text
        raise Proxy1024Error(f"1024proxy 提取失败：{_clip(message)}")
    proxies = _collect(payload)
    if proxies:
        return _limit(proxies)
    message = _message(payload) or _data_message(payload)
    if message and message.casefold() not in _SUCCESS_TEXT:
        raise Proxy1024Error(f"1024proxy 提取失败：{_clip(message)}")
    raise Proxy1024Error("1024proxy 未返回 IP 和端口")


def _request_params(
    *,
    region: str,
    data_format: str,
    session_time: int,
    num: str | None,
    data_type: str | None,
) -> dict[str, str]:
    normalized_region = region.strip()
    if normalized_region.isalpha():
        normalized_region = normalized_region.upper()
    if not _REGION_RE.fullmatch(normalized_region):
        raise ValueError("国家 region 无效")
    normalized_format = data_format.strip().lower()
    if normalized_format not in _FORMATS:
        raise ValueError("返回格式 format 无效，只支持 n、rn、1、2")
    if isinstance(session_time, bool) or not isinstance(session_time, int):
        raise ValueError("会话时长 time 无效")
    if not 1 <= session_time <= 10080:
        raise ValueError("会话时长 time 必须在 1 到 10080 之间")
    normalized_num = str(num or "").strip()
    if normalized_num and not _NUM_RE.fullmatch(normalized_num):
        raise ValueError("提取数量 num 无效")
    normalized_type = str(data_type or "json").strip().lower()
    if normalized_type not in _TYPES:
        raise ValueError("数据格式 type 无效，只支持 txt、json")
    params = {
        "region": normalized_region,
        "format": normalized_format,
        "time": str(session_time),
        "type": normalized_type,
    }
    if normalized_num:
        params["num"] = normalized_num
    return params


def _json_payload(text: str) -> Any | None:
    if text[0] not in "{[":
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _explicit_error(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    success = payload.get("success")
    if success is False or str(success).strip().lower() == "false":
        return True
    code = payload.get("code")
    if code is None or str(code).strip() in _SUCCESS_CODES:
        return False
    return not _collect(payload)


def _data_message(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    data = payload.get("data")
    if isinstance(data, str) and data.strip():
        return data.strip()
    return ""


def _message(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in _MESSAGE_KEYS:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    if isinstance(payload, str):
        return payload.strip()
    return ""


def _collect(node: Any, depth: int = 0) -> list[dict[str, Any]]:
    if depth > 8:
        return []
    if isinstance(node, str):
        return _from_text(node)
    if isinstance(node, list):
        found: list[dict[str, Any]] = []
        for item in node:
            found.extend(_collect(item, depth + 1))
            if len(found) > _MAX_PROXIES:
                break
        return found
    if not isinstance(node, dict):
        return []
    endpoint = _from_mapping(node)
    if endpoint is not None:
        return [endpoint]
    preferred = [node[key] for key in _PREFERRED_KEYS if key in node]
    sources = preferred or [
        value
        for key, value in node.items()
        if key not in _SKIP_KEYS and isinstance(value, (str, list, dict))
    ]
    found = []
    for value in sources:
        found.extend(_collect(value, depth + 1))
        if len(found) > _MAX_PROXIES:
            break
    return found


def _from_mapping(item: dict[str, Any]) -> dict[str, Any] | None:
    ip_value = _first(item, ("ip", "IP", "host", "addr", "address", "server"))
    port_value = _first(item, ("port", "Port", "proxy_port", "proxyPort"))
    if isinstance(ip_value, str) and port_value is None and ":" in ip_value:
        found = _from_text(ip_value)
        return found[0] if found else None
    if ip_value is None or port_value is None:
        return None
    return _endpoint(str(ip_value).strip(), port_value)


def _first(item: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return None


def _from_text(text: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for match in _ENDPOINT_RE.finditer(text):
        endpoint = _endpoint(match.group("ip"), match.group("port"))
        if endpoint is not None:
            found.append(endpoint)
    return found


def _endpoint(ip: str, port: Any) -> dict[str, Any] | None:
    try:
        parsed_port = int(str(port).strip())
    except (TypeError, ValueError):
        return None
    if not _ipv4(ip) or not 1 <= parsed_port <= 65535:
        return None
    return {"ip": ip, "port": parsed_port}


def _ipv4(ip: str) -> bool:
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    for part in parts:
        if not part.isdigit() or len(part) > 3 or int(part) > 255:
            return False
    return True


def _limit(proxies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(proxies) > _MAX_PROXIES:
        raise Proxy1024Error("1024proxy 返回的 IP 数量过多")
    return proxies


def _clip(text: str) -> str:
    compact = " ".join(text.split())
    if len(compact) <= 300:
        return compact
    return f"{compact[:300]}..."
