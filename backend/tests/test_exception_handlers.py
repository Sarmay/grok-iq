from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.requests import Request

from app.integrations.grok2api.client import IntegrationError
from app.integrations.proxy1024.client import Proxy1024Error
from app.persistence.probe_repository import QueueFullError, RunStateError
from app.services.auth_service import AuthenticationError
from app.services.chat_service import ChatUpstreamError
from app.web.exception_handlers import install_exception_handlers


def build_test_app() -> FastAPI:
    app = FastAPI()
    install_exception_handlers(app)
    return app


def request(path: str = "/test") -> Request:
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "client": None,
            "server": ("testserver", 80),
        }
    )


async def handle(app: FastAPI, exc: Exception) -> JSONResponse:
    handler = next(
        app.exception_handlers[exception_type]
        for exception_type in type(exc).__mro__
        if exception_type in app.exception_handlers
    )
    response = await handler(request(), exc)
    assert isinstance(response, JSONResponse)
    return response


def response_json(response: JSONResponse) -> dict[str, str]:
    return json.loads(response.body)


async def test_global_exception_handlers_preserve_business_status_codes():
    app = build_test_app()
    cases = [
        (ValueError("参数无效"), 400, "参数无效"),
        (AuthenticationError("登录已过期"), 401, "登录已过期"),
        (QueueFullError("队列已满"), 429, "队列已满"),
        (RunStateError("任务状态冲突"), 409, "任务状态冲突"),
        (IntegrationError("上游不可用"), 502, "上游不可用"),
        (Proxy1024Error("1024proxy 提取失败"), 502, "1024proxy 提取失败"),
        (ChatUpstreamError("上游限流", status_code=429), 429, "上游限流"),
    ]
    for exc, status_code, detail in cases:
        response = await handle(app, exc)
        assert response.status_code == status_code
        assert response_json(response) == {"detail": detail}


async def test_unexpected_exception_is_logged_but_not_exposed():
    response = await handle(
        build_test_app(),
        RuntimeError("不能暴露的内部详情"),
    )

    assert response.status_code == 500
    assert response_json(response) == {"detail": "服务器内部错误"}
    assert "不能暴露的内部详情" not in response.body.decode()
