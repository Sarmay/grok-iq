from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote, urlencode

from curl_cffi.requests import AsyncSession as CurlAsyncSession

LINUXDO_AUTH_URL = "https://connect.linux.do/oauth2/authorize"
LINUXDO_TOKEN_URL = "https://connect.linux.do/oauth2/token"
LINUXDO_USER_INFO_URL = "https://connect.linux.do/api/user"
LINUXDO_OAUTH_SCOPE = "user"


class LinuxDoOAuthError(RuntimeError):
    """A Linux DO Connect token or user-info request failed."""


def parse_linuxdo_user(payload: Any) -> dict[str, Any]:
    """Validate the untrusted Connect user object into a compact identity."""

    if not isinstance(payload, dict):
        raise LinuxDoOAuthError("Linux DO 用户信息格式无效")
    user_id = payload.get("id")
    username = str(payload.get("username") or payload.get("login") or "").strip()
    name = str(payload.get("name") or username).strip()
    if user_id is None or str(user_id).strip() == "" or not username:
        raise LinuxDoOAuthError("Linux DO 用户信息缺少 id 或用户名")
    if payload.get("active") is False:
        raise LinuxDoOAuthError("Linux DO 账号未激活")
    if payload.get("silenced") is True:
        raise LinuxDoOAuthError("Linux DO 账号已被限制")
    try:
        trust_level = int(payload.get("trust_level") or 0)
    except (TypeError, ValueError):
        trust_level = 0
    avatar_url = str(payload.get("avatar_url") or "").strip()
    return {
        "id": str(user_id).strip()[:64],
        "username": username[:64],
        "name": name[:120],
        "trustLevel": max(0, trust_level),
        "avatarUrl": avatar_url[:2000],
    }


class LinuxDoConnectClient:
    """OAuth2 client for Linux DO Connect authorization-code flow."""

    def authorize_url(self, *, client_id: str, redirect_uri: str, state: str) -> str:
        params = urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": LINUXDO_OAUTH_SCOPE,
                "state": state,
            },
            quote_via=quote,
        )
        return f"{LINUXDO_AUTH_URL}?{params}"

    async def fetch_user(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        code: str,
    ) -> dict[str, Any]:
        token_payload = await self._post_form(
            LINUXDO_TOKEN_URL,
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            context="换取 Linux DO 访问令牌",
        )
        access_token = str(token_payload.get("access_token") or "").strip()
        if not access_token:
            raise LinuxDoOAuthError("Linux DO 未返回 access_token")
        payload = await self._get_json(
            LINUXDO_USER_INFO_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
            context="读取 Linux DO 用户信息",
        )
        return parse_linuxdo_user(payload)

    async def _post_form(
        self,
        url: str,
        data: dict[str, str],
        *,
        context: str,
    ) -> dict[str, Any]:
        try:
            async with CurlAsyncSession() as client:
                response = await client.post(
                    url,
                    data=data,
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Accept": "application/json",
                    },
                    timeout=20,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise LinuxDoOAuthError(f"{context}请求失败: {exc}") from exc
        return _json_payload(response, context)

    async def _get_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        context: str,
    ) -> dict[str, Any]:
        try:
            async with CurlAsyncSession() as client:
                response = await client.get(url, headers=headers, timeout=20)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise LinuxDoOAuthError(f"{context}请求失败: {exc}") from exc
        return _json_payload(response, context)


def _json_payload(response: Any, context: str) -> dict[str, Any]:
    status_code = int(getattr(response, "status_code", 0) or 0)
    body = str(getattr(response, "text", "") or "").strip()
    if status_code >= 300:
        raise LinuxDoOAuthError(
            f"{context}返回 HTTP {status_code}: {body[:1000] or '空响应'}"
        )
    try:
        payload = response.json()
    except (TypeError, ValueError) as exc:
        raise LinuxDoOAuthError(f"{context}返回了无法解析的 JSON") from exc
    if not isinstance(payload, dict):
        raise LinuxDoOAuthError(f"{context}返回格式无效")
    error = payload.get("error")
    if error:
        description = str(payload.get("error_description") or error)
        raise LinuxDoOAuthError(f"{context}失败：{description}")
    return payload
