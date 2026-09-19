from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import jwt
from fastapi import Request
from fastapi.responses import RedirectResponse
from jwt import InvalidTokenError

from app.core.config import Settings
from app.integrations.linuxdo.client import LinuxDoConnectClient, LinuxDoOAuthError
from app.services.auth_service import JWT_ALGORITHM, JWT_ISSUER

SESSION_COOKIE = "grokiq_linuxdo"
SESSION_AUDIENCE = "grok-iq-linuxdo"
STATE_AUDIENCE = "grok-iq-linuxdo-state"
STATUS_PATH = "/status"
STATE_TTL_SECONDS = 10 * 60


class LinuxDoAuthenticationRequired(Exception):
    """Raised when the public status page requires a Linux DO session."""

    def __init__(self, message: str = "请先使用 Linux DO 登录"):
        super().__init__(message)
        self.message = message


class LinuxDoOAuthService:
    """Issues Linux DO Connect logins for the public /status page."""

    def __init__(
        self,
        settings: Settings,
        secret: str,
        client: LinuxDoConnectClient | None = None,
    ):
        self.settings = settings
        self.secret = secret
        self.client = client or LinuxDoConnectClient()

    def configured(self) -> bool:
        return bool(
            self.settings.linuxdo_oauth_client_id.strip()
            and self.settings.linuxdo_oauth_client_secret.strip()
            and self.settings.linuxdo_oauth_redirect_uri.strip()
        )

    def public_status(self, request: Request) -> dict[str, Any]:
        user = self.authenticate_request(request)
        return {
            "enabled": bool(self.settings.linuxdo_oauth_enabled),
            "configured": self.configured(),
            "authenticated": user is not None,
            "user": user,
        }

    def login_redirect(self) -> RedirectResponse:
        if not self.settings.linuxdo_oauth_enabled or not self.configured():
            return self._status_redirect("not_configured")
        state = self._create_state()
        url = self.client.authorize_url(
            client_id=self.settings.linuxdo_oauth_client_id,
            redirect_uri=self.settings.linuxdo_oauth_redirect_uri,
            state=state,
        )
        return _no_store_redirect(url)

    async def callback_redirect(
        self,
        *,
        code: str,
        state: str,
        error: str,
    ) -> RedirectResponse:
        if error.strip():
            return self._status_redirect("denied")
        if not self.settings.linuxdo_oauth_enabled or not self.configured():
            return self._status_redirect("not_configured")
        if not code.strip() or not self._verify_state(state):
            return self._status_redirect("invalid_state")
        try:
            user = await self.client.fetch_user(
                client_id=self.settings.linuxdo_oauth_client_id,
                client_secret=self.settings.linuxdo_oauth_client_secret,
                redirect_uri=self.settings.linuxdo_oauth_redirect_uri,
                code=code.strip(),
            )
        except LinuxDoOAuthError:
            return self._status_redirect("exchange_failed")
        response = self._status_redirect()
        self._set_session_cookie(response, self._session_token(user))
        return response

    def logout_redirect(self) -> RedirectResponse:
        response = self._status_redirect()
        response.delete_cookie(
            SESSION_COOKIE,
            path="/",
            samesite="lax",
            secure=self._secure_cookie(),
        )
        return response

    def authenticate_request(self, request: Request) -> dict[str, Any] | None:
        token = str(request.cookies.get(SESSION_COOKIE) or "").strip()
        if not token:
            return None
        return self.authenticate_token(token)

    def authenticate_token(self, token: str) -> dict[str, Any] | None:
        try:
            payload = jwt.decode(
                token,
                self.secret,
                algorithms=[JWT_ALGORITHM],
                audience=SESSION_AUDIENCE,
                issuer=JWT_ISSUER,
                options={"require": ["exp", "iat", "nbf", "sub", "iss", "aud", "jti"]},
            )
        except (InvalidTokenError, KeyError, TypeError, ValueError):
            return None
        user_id = str(payload.get("sub") or "").strip()
        username = str(payload.get("username") or "").strip()
        if not user_id or not username:
            return None
        return {
            "id": user_id,
            "username": username,
            "name": str(payload.get("name") or username).strip() or username,
        }

    def require_status_access(
        self,
        request: Request,
        *,
        admin_authenticated: bool,
    ) -> None:
        if not self.settings.linuxdo_oauth_enabled:
            return
        if admin_authenticated or self.authenticate_request(request) is not None:
            return
        raise LinuxDoAuthenticationRequired()

    def _create_state(self) -> str:
        issued_at = datetime.now(UTC)
        return jwt.encode(
            {
                "purpose": "linuxdo_oauth",
                "iat": issued_at,
                "nbf": issued_at,
                "exp": issued_at + timedelta(seconds=STATE_TTL_SECONDS),
                "iss": JWT_ISSUER,
                "aud": STATE_AUDIENCE,
                "jti": uuid.uuid4().hex,
            },
            self.secret,
            algorithm=JWT_ALGORITHM,
        )

    def _verify_state(self, state: str) -> bool:
        try:
            payload = jwt.decode(
                str(state or "").strip(),
                self.secret,
                algorithms=[JWT_ALGORITHM],
                audience=STATE_AUDIENCE,
                issuer=JWT_ISSUER,
                options={"require": ["exp", "iat", "nbf", "iss", "aud", "jti"]},
            )
        except (InvalidTokenError, KeyError, TypeError, ValueError):
            return False
        return str(payload.get("purpose") or "") == "linuxdo_oauth"

    def _session_token(self, user: dict[str, Any]) -> str:
        issued_at = datetime.now(UTC)
        expires_at = issued_at + timedelta(seconds=self.settings.jwt_ttl_seconds)
        return jwt.encode(
            {
                "sub": str(user["id"]),
                "username": str(user["username"]),
                "name": str(user.get("name") or user["username"]),
                "iat": issued_at,
                "nbf": issued_at,
                "exp": expires_at,
                "iss": JWT_ISSUER,
                "aud": SESSION_AUDIENCE,
                "jti": uuid.uuid4().hex,
            },
            self.secret,
            algorithm=JWT_ALGORITHM,
        )

    def _set_session_cookie(self, response: RedirectResponse, token: str) -> None:
        response.set_cookie(
            key=SESSION_COOKIE,
            value=token,
            max_age=self.settings.jwt_ttl_seconds,
            httponly=True,
            samesite="lax",
            secure=self._secure_cookie(),
            path="/",
        )

    def _secure_cookie(self) -> bool:
        return self.settings.linuxdo_oauth_redirect_uri.strip().lower().startswith(
            "https://"
        )

    def _status_redirect(self, error: str | None = None) -> RedirectResponse:
        if error:
            return _no_store_redirect(f"{STATUS_PATH}?{urlencode({'oauth_error': error})}")
        return _no_store_redirect(STATUS_PATH)


def _no_store_redirect(url: str) -> RedirectResponse:
    response = RedirectResponse(url, status_code=302)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response
