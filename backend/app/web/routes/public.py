from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import RedirectResponse

from app.integrations.grok2api.client import Grok2APIClient
from app.services.account_service import AccountService
from app.services.auth_service import AuthenticationError, AuthService
from app.services.client_key_quota_service import ClientKeyQuotaService
from app.services.client_key_usage_service import ClientKeyUsageService
from app.services.linuxdo_oauth import LinuxDoOAuthService
from app.web.schemas import ClientKeyQuotaInput, ClientKeyUsageInput

from ._shared import disable_client_cache


def build_public_router(
    account_service: AccountService,
    client: Grok2APIClient,
    auth_service: AuthService,
    linuxdo_oauth: LinuxDoOAuthService,
) -> APIRouter:
    router = APIRouter()
    quota_service = ClientKeyQuotaService(client)
    usage_service = ClientKeyUsageService(client, quota_service=quota_service)

    def require_public_status_access(request: Request) -> None:
        linuxdo_oauth.require_status_access(
            request,
            admin_authenticated=_has_admin_session(
                auth_service, request.headers.get("authorization") or ""
            ),
        )

    @router.get("/public/linuxdo/status")
    def public_linuxdo_status(request: Request, response: Response) -> dict[str, Any]:
        disable_client_cache(response)
        return linuxdo_oauth.public_status(request)

    @router.get("/public/linuxdo/login")
    def public_linuxdo_login() -> RedirectResponse:
        return linuxdo_oauth.login_redirect()

    @router.get("/public/linuxdo/callback")
    async def public_linuxdo_callback(
        code: str = "",
        state: str = "",
        error: str = "",
    ) -> RedirectResponse:
        return await linuxdo_oauth.callback_redirect(
            code=code,
            state=state,
            error=error,
        )

    @router.get("/public/linuxdo/logout")
    def public_linuxdo_logout() -> RedirectResponse:
        return linuxdo_oauth.logout_redirect()

    @router.get("/public/upstream-accounts")
    async def public_upstream_accounts(
        request: Request,
        response: Response,
    ) -> dict[str, Any]:
        disable_client_cache(response)
        require_public_status_access(request)
        return await account_service.public_upstream_account_summary(
            include_inventory=_has_admin_session(
                auth_service, request.headers.get("authorization") or ""
            )
        )

    @router.post("/public/client-key-quota")
    async def public_client_key_quota(
        payload: ClientKeyQuotaInput,
        request: Request,
        response: Response,
    ) -> dict[str, Any]:
        disable_client_cache(response)
        require_public_status_access(request)
        return await quota_service.lookup(payload.api_key, client_ip=_client_ip(request))

    @router.post("/public/client-key-usage")
    async def public_client_key_usage(
        payload: ClientKeyUsageInput,
        request: Request,
        response: Response,
    ) -> dict[str, Any]:
        disable_client_cache(response)
        require_public_status_access(request)
        return await usage_service.lookup_public_usage(
            payload.api_key,
            client_ip=_client_ip(request),
            period=payload.period,
            start=payload.start,
            end=payload.end,
        )

    @router.get("/public/upstream-usage")
    async def public_upstream_usage(
        request: Request,
        response: Response,
        period: str = Query(default="24h"),
        timezone: str = Query(default="Asia/Shanghai"),
        refresh: str = Query(default=""),
    ) -> dict[str, Any]:
        disable_client_cache(response)
        require_public_status_access(request)
        return await usage_service.public_usage_overview(
            period=period,
            timezone=timezone,
            refresh=refresh.strip().lower() in {"1", "true", "yes"},
        )

    return router


def _client_ip(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",", 1)[0].strip()
    if forwarded:
        return forwarded
    real_ip = (request.headers.get("x-real-ip") or "").strip()
    if real_ip:
        return real_ip
    host = request.client.host if request.client else ""
    return host or "unknown"


def _has_admin_session(auth_service: AuthService, authorization: str) -> bool:
    if not authorization.strip():
        return False
    try:
        auth_service.authenticate_authorization(authorization)
    except AuthenticationError:
        return False
    return True
