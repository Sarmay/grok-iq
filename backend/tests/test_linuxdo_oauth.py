from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.requests import Request

from app.core.config import Settings
from app.integrations.linuxdo.client import (
    LINUXDO_AUTH_URL,
    LinuxDoConnectClient,
    LinuxDoOAuthError,
    parse_linuxdo_user,
)
from app.services.linuxdo_oauth import (
    SESSION_COOKIE,
    LinuxDoAuthenticationRequired,
    LinuxDoOAuthService,
)
from app.web.exception_handlers import install_exception_handlers


class StubLinuxDoClient(LinuxDoConnectClient):
    def __init__(self, user: dict[str, Any] | None = None, *, fail: bool = False):
        self.user = user or {
            "id": "42",
            "username": "demo",
            "name": "Demo",
            "trustLevel": 2,
            "avatarUrl": "",
        }
        self.fail = fail
        self.calls: list[dict[str, str]] = []

    async def fetch_user(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        code: str,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "code": code,
            }
        )
        if self.fail:
            raise LinuxDoOAuthError("换取失败")
        return self.user


def build_service(
    *,
    enabled: bool = True,
    client: StubLinuxDoClient | None = None,
) -> tuple[Settings, StubLinuxDoClient, LinuxDoOAuthService]:
    settings = Settings(
        _env_file=None,
        jwt_secret_key="s" * 48,
        linuxdo_oauth_enabled=enabled,
        linuxdo_oauth_client_id="client-id",
        linuxdo_oauth_client_secret="client-secret",
        linuxdo_oauth_redirect_uri="http://127.0.0.1:8090/api/public/linuxdo/callback",
    )
    stub = client or StubLinuxDoClient()
    return settings, stub, LinuxDoOAuthService(settings, settings.jwt_secret_key, stub)


def request_with_cookie(token: str = "") -> Request:
    headers = []
    if token:
        headers.append((b"cookie", f"{SESSION_COOKIE}={token}".encode()))
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/public/upstream-accounts",
            "raw_path": b"/api/public/upstream-accounts",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 9),
            "server": ("testserver", 80),
        }
    )


def test_parse_linuxdo_user_requires_id_and_username():
    parsed = parse_linuxdo_user(
        {"id": 7, "username": "alice", "name": "Alice", "trust_level": 3}
    )
    assert parsed["id"] == "7"
    assert parsed["username"] == "alice"
    assert parsed["name"] == "Alice"
    assert parsed["trustLevel"] == 3
    with pytest.raises(LinuxDoOAuthError, match="缺少"):
        parse_linuxdo_user({"id": 1})
    with pytest.raises(LinuxDoOAuthError, match="未激活"):
        parse_linuxdo_user({"id": 1, "username": "bob", "active": False})
    with pytest.raises(LinuxDoOAuthError, match="限制"):
        parse_linuxdo_user({"id": 1, "username": "bob", "silenced": True})


def test_authorize_url_matches_linuxdo_connect_query():
    url = LinuxDoConnectClient().authorize_url(
        client_id="abc",
        redirect_uri="http://127.0.0.1:8090/api/public/linuxdo/callback",
        state="state-token",
    )
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == LINUXDO_AUTH_URL
    assert query["client_id"] == ["abc"]
    assert query["response_type"] == ["code"]
    assert query["scope"] == ["user"]
    assert query["state"] == ["state-token"]
    assert query["redirect_uri"] == [
        "http://127.0.0.1:8090/api/public/linuxdo/callback"
    ]


def test_login_redirect_includes_signed_state():
    _settings, _client, service = build_service()
    response = service.login_redirect()
    location = response.headers["location"]
    state = parse_qs(urlsplit(location).query)["state"][0]
    assert service._verify_state(state) is True


def test_login_redirect_without_config_returns_status_error():
    settings, client, service = build_service()
    settings.linuxdo_oauth_client_secret = ""
    response = service.login_redirect()
    assert response.headers["location"] == "/status?oauth_error=not_configured"
    assert client.calls == []


@pytest.mark.asyncio
async def test_callback_sets_httponly_session_cookie():
    _settings, client, service = build_service()
    state = service._create_state()
    response = await service.callback_redirect(
        code="auth-code",
        state=state,
        error="",
    )
    assert response.headers["location"] == "/status"
    cookie = response.headers.get("set-cookie", "")
    assert SESSION_COOKIE in cookie
    assert "HttpOnly" in cookie
    assert client.calls == [
        {
            "client_id": "client-id",
            "client_secret": "client-secret",
            "redirect_uri": "http://127.0.0.1:8090/api/public/linuxdo/callback",
            "code": "auth-code",
        }
    ]
    token = cookie.split("=", 1)[1].split(";", 1)[0]
    user = service.authenticate_token(token)
    assert user == {"id": "42", "username": "demo", "name": "Demo"}


@pytest.mark.asyncio
async def test_callback_rejects_invalid_state_and_provider_errors():
    _settings, client, service = build_service()
    invalid = await service.callback_redirect(
        code="auth-code",
        state="not-a-token",
        error="",
    )
    assert invalid.headers["location"] == "/status?oauth_error=invalid_state"
    denied = await service.callback_redirect(
        code="",
        state=service._create_state(),
        error="access_denied",
    )
    assert denied.headers["location"] == "/status?oauth_error=denied"
    client.fail = True
    failed = await service.callback_redirect(
        code="auth-code",
        state=service._create_state(),
        error="",
    )
    assert failed.headers["location"] == "/status?oauth_error=exchange_failed"


def test_status_access_requires_linuxdo_session_when_enabled():
    _settings, _client, service = build_service()
    with pytest.raises(LinuxDoAuthenticationRequired):
        service.require_status_access(request_with_cookie(), admin_authenticated=False)
    service.require_status_access(request_with_cookie(), admin_authenticated=True)
    token = service._session_token(
        {"id": "42", "username": "demo", "name": "Demo"}
    )
    service.require_status_access(
        request_with_cookie(token), admin_authenticated=False
    )


def test_disabled_oauth_leaves_public_status_open():
    _settings, _client, service = build_service(enabled=False)
    service.require_status_access(request_with_cookie(), admin_authenticated=False)


@pytest.mark.asyncio
async def test_linuxdo_authentication_required_uses_distinct_code():
    app = FastAPI()
    install_exception_handlers(app)
    handler = app.exception_handlers[LinuxDoAuthenticationRequired]
    response = await handler(request_with_cookie(), LinuxDoAuthenticationRequired())
    assert isinstance(response, JSONResponse)
    assert response.status_code == 401
    assert json.loads(response.body) == {
        "detail": "请先使用 Linux DO 登录",
        "code": "linuxdo_oauth_required",
    }
