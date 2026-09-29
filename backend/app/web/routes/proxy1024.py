from __future__ import annotations

import hmac
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.security import HTTPAuthorizationCredentials

from app.core.config import Settings
from app.integrations.proxy1024.client import Proxy1024Client
from app.services.auth_service import AuthenticationError, AuthService
from app.web.auth import AdminAuthenticationRequired, admin_bearer

from ._shared import disable_client_cache


def authorize_proxy_white_request(
    request: Request,
    *,
    settings: Settings,
    auth_service: AuthService,
    credentials: HTTPAuthorizationCredentials | None,
) -> None:
    """Allow the admin session or the grok-register integration token."""

    authorization = (
        f"{credentials.scheme} {credentials.credentials}" if credentials else ""
    )
    admin_error: AuthenticationError | None = None
    if authorization:
        try:
            request.state.auth_user = auth_service.authenticate_authorization(
                authorization
            )
        except AuthenticationError as exc:
            admin_error = exc
        else:
            return

    supplied = request.headers.get("x-grokiq-token", "").strip()
    if supplied:
        expected = settings.grok_register_webhook_token.strip()
        if not expected:
            raise HTTPException(
                status_code=503,
                detail="grok-register 联动令牌尚未配置",
            )
        if hmac.compare_digest(supplied, expected):
            return
        raise HTTPException(status_code=401, detail="联动令牌无效")

    setup_required = auth_service.setup_required()
    if admin_error is not None and not setup_required:
        message = str(admin_error)
    else:
        message = "请先创建管理员账号" if setup_required else "需要管理员登录"
    raise AdminAuthenticationRequired(message, setup_required=setup_required)


def build_proxy_white_router(
    client: Proxy1024Client | None = None,
    *,
    settings: Settings | None = None,
    auth_service: AuthService | None = None,
) -> APIRouter:
    proxy_client = client or Proxy1024Client()

    def require_caller(
        request: Request,
        credentials: Annotated[
            HTTPAuthorizationCredentials | None,
            Depends(admin_bearer),
        ],
    ) -> None:
        if settings is None or auth_service is None:
            raise HTTPException(status_code=503, detail="代理提取尚未配置")
        authorize_proxy_white_request(
            request,
            settings=settings,
            auth_service=auth_service,
            credentials=credentials,
        )

    router = APIRouter(dependencies=[Depends(require_caller)])

    @router.get("/proxy/white")
    async def extract_white_proxy(
        response: Response,
        region: str = Query(min_length=1, max_length=16),
        format: str = Query(min_length=1, max_length=8),
        time: int = Query(ge=1, le=10080),
        num: str | None = Query(default=None, max_length=8),
        type: str | None = Query(default=None, max_length=8),
    ) -> dict[str, Any]:
        """Extract proxy IP and port pairs from the 1024proxy whitelist API."""

        disable_client_cache(response)
        proxies = await proxy_client.extract(
            region=region,
            data_format=format,
            session_time=time,
            num=num,
            data_type=type,
        )
        return {"proxies": proxies}

    return router
