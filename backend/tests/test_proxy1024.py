from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.routing import APIRoute

from app.integrations.proxy1024.client import (
    WHITE_API_URL,
    Proxy1024Client,
    Proxy1024Error,
    parse_proxy_endpoints,
)
from app.web.routes.proxy1024 import build_proxy_white_router


def test_json_object_list_returns_ip_and_port() -> None:
    proxies = parse_proxy_endpoints(
        '{"code":200,"msg":"success","data":[{"ip":"203.0.113.10","port":"10001"}]}'
    )

    assert proxies == [{"ip": "203.0.113.10", "port": 10001}]


def test_plain_text_endpoints_split_on_newlines() -> None:
    proxies = parse_proxy_endpoints("203.0.113.10:10001\r\n203.0.113.11:10002\n")

    assert proxies == [
        {"ip": "203.0.113.10", "port": 10001},
        {"ip": "203.0.113.11", "port": 10002},
    ]


def test_whitelist_rejection_is_an_upstream_error() -> None:
    with pytest.raises(Proxy1024Error, match="not added to whitelist"):
        parse_proxy_endpoints("172.236.149.183 not added to whitelist")


def test_failed_json_code_is_an_upstream_error() -> None:
    with pytest.raises(Proxy1024Error, match="余额不足"):
        parse_proxy_endpoints('{"code":500,"msg":"余额不足","data":[]}')


def test_string_data_error_is_surfaced() -> None:
    with pytest.raises(Proxy1024Error, match="not added to whitelist"):
        parse_proxy_endpoints(
            '{"code":200,"data":"172.236.149.183 not added to whitelist"}'
        )


@pytest.mark.asyncio
async def test_invalid_region_and_format_are_rejected() -> None:
    client = Proxy1024Client()

    with pytest.raises(ValueError, match="region"):
        await client.extract(region="S G", data_format="n", session_time=10)
    with pytest.raises(ValueError, match="format"):
        await client.extract(region="SG", data_format="csv", session_time=10)
    with pytest.raises(ValueError, match="num"):
        await client.extract(
            region="SG", data_format="n", session_time=10, num="0"
        )
    with pytest.raises(ValueError, match="type"):
        await client.extract(
            region="SG", data_format="n", session_time=10, data_type="xml"
        )


@pytest.mark.asyncio
async def test_extract_calls_whitelist_api_and_normalizes_json() -> None:
    response = MagicMock(status_code=200)
    response.text = '{"data":[{"ip":"203.0.113.10","port":10001}]}'
    session = MagicMock()
    session.get = AsyncMock(return_value=response)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "app.integrations.proxy1024.client.CurlAsyncSession",
        return_value=session,
    ) as session_cls:
        proxies = await Proxy1024Client().extract(
            region="sg",
            data_format="1",
            session_time=10,
            num="1",
            data_type="json",
        )

    session_cls.assert_called_once_with(trust_env=False)
    assert session.get.await_args.args == (WHITE_API_URL,)
    assert session.get.await_args.kwargs["params"] == {
        "region": "SG",
        "format": "1",
        "time": "10",
        "num": "1",
        "type": "json",
    }
    assert proxies == [{"ip": "203.0.113.10", "port": 10001}]


@pytest.mark.asyncio
async def test_route_returns_extracted_proxies() -> None:
    class StubClient:
        async def extract(self, **kwargs: Any) -> list[dict[str, Any]]:
            self.kwargs = kwargs
            return [{"ip": "203.0.113.10", "port": 10001}]

    stub = StubClient()
    router = build_proxy_white_router(stub)  # type: ignore[arg-type]
    route = next(
        item
        for item in router.routes
        if isinstance(item, APIRoute) and item.path == "/proxy/white"
    )
    response = MagicMock()

    result = await route.endpoint(
        response=response,
        region="SG",
        format="1",
        time=10,
        num="1",
        type="json",
    )

    assert result == {"proxies": [{"ip": "203.0.113.10", "port": 10001}]}
    assert stub.kwargs == {
        "region": "SG",
        "data_format": "1",
        "session_time": 10,
        "num": "1",
        "data_type": "json",
    }
    assert response.headers.__setitem__.call_args_list[0].args == (
        "Cache-Control",
        "no-store",
    )
