from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Response

from app.integrations.proxy1024.client import Proxy1024Client

from ._shared import disable_client_cache


def build_proxy_white_router(
    client: Proxy1024Client | None = None,
) -> APIRouter:
    proxy_client = client or Proxy1024Client()
    router = APIRouter()

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
