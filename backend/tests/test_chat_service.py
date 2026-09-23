from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from curl_cffi.const import CurlHttpVersion, CurlOpt

from app.core.config import Settings
from app.integrations.grok2api.http_session import abort_curl_stream, streaming_curl_options
from app.services.chat_service import ChatService


@pytest.mark.asyncio
async def test_open_completion_uses_identity_encoding_and_streaming() -> None:
    settings = MagicMock(grok2api_http_impersonate="chrome")
    providers = MagicMock()
    providers.get_default.return_value = {
        "id": "provider-1",
        "base_url": "https://api.test/v1/responses",
        "api_key": "key",
        "enabled": True,
    }
    response = MagicMock(status_code=200, headers={"content-type": "text/event-stream"})
    session = MagicMock()
    session.post = AsyncMock(return_value=response)
    session.close = AsyncMock()

    with patch("app.services.chat_service.open_curl_session", return_value=session) as factory:
        stream = await ChatService(settings=settings, providers=providers).open_completion(
            provider_id="", body=b'{"messages":[]}', request_headers={}
        )

    session.post.assert_awaited_once()
    kwargs = session.post.await_args.kwargs
    assert kwargs["stream"] is True
    assert kwargs["accept_encoding"] == "identity"
    assert session.post.await_args.args[0] == "https://api.test/v1/responses"
    assert stream.response is response
    factory.assert_called_once()
    assert factory.call_args.kwargs["base_url"] == "https://api.test/v1/responses"


def test_responses_provider_payload_translates_messages_to_input() -> None:
    body = json.dumps(
        {
            "model": "grok-4.5",
            "stream": True,
            "messages": [
                {"role": "system", "content": "Be concise."},
                {"role": "user", "content": "hello"},
            ],
        }
    ).encode()

    result = ChatService._prepare_completion_body(body, "https://api.test/v1/responses")
    payload = json.loads(result)

    assert payload["instructions"] == "Be concise."
    assert payload["input"] == [{"role": "user", "content": "hello"}]
    assert payload["stream"] is True
    assert payload["store"] is False
    assert payload["reasoning"] == {"summary": "auto"}
    assert "messages" not in payload


def test_responses_provider_payload_keeps_explicit_store_and_reasoning() -> None:
    body = json.dumps(
        {
            "model": "grok-4.5",
            "stream": True,
            "store": True,
            "reasoning": {"effort": "high"},
            "messages": [{"role": "user", "content": "hello"}],
        }
    ).encode()

    payload = json.loads(
        ChatService._prepare_completion_body(body, "https://api.test/v1/responses")
    )

    assert payload["store"] is True
    assert payload["reasoning"] == {"effort": "high"}


def test_chat_provider_payload_is_unchanged_for_chat_completions() -> None:
    body = b'{"model":"grok-4.5","messages":[]}'
    assert ChatService._prepare_completion_body(body, "https://api.test/v1/chat/completions") == body


def test_responses_provider_payload_converts_token_limit() -> None:
    body = json.dumps(
        {
            "model": "grok-4.5",
            "stream": True,
            "max_tokens": 128,
            "messages": [{"role": "user", "content": "hello"}],
        }
    ).encode()

    payload = json.loads(
        ChatService._prepare_completion_body(body, "https://api.test/v1/responses")
    )

    assert payload["max_output_tokens"] == 128
    assert "max_tokens" not in payload
    assert "messages" not in payload


def test_completion_url_defaults_to_responses() -> None:
    assert ChatService._completion_url("https://api.test/v1/responses") == "https://api.test/v1/responses"
    assert ChatService._completion_url("https://api.test") == "https://api.test/v1/responses"
    assert (
        ChatService._completion_url("https://api.test/v1/chat/completions")
        == "https://api.test/v1/chat/completions"
    )


def test_bootstrap_points_default_gateway_at_live_grok2api_url() -> None:
    settings = Settings(
        _env_file=None,
        grok2api_base_url="http://107.174.124.163:8000",
    )
    providers = _MemoryProviders(
        [
            {
                "id": "gateway",
                "name": "默认网关",
                "base_url": "http://127.0.0.1:8000",
                "is_default": True,
                "enabled": True,
                "models": [],
                "api_key_configured": False,
            }
        ]
    )

    ChatService(settings=settings, providers=providers).bootstrap()

    assert providers.rows[0]["base_url"] == "http://107.174.124.163:8000"


def test_bootstrap_repairs_placeholder_default_provider() -> None:
    settings = Settings(
        _env_file=None,
        grok2api_base_url="http://107.174.124.163:8000",
    )
    providers = _MemoryProviders(
        [
            {
                "id": "gateway",
                "name": "生产网关",
                "base_url": "http://host.docker.internal:8000",
                "is_default": True,
                "enabled": True,
                "models": [],
                "api_key_configured": False,
            }
        ]
    )

    ChatService(settings=settings, providers=providers).bootstrap()

    assert providers.rows[0]["base_url"] == "http://107.174.124.163:8000"


def test_bootstrap_leaves_custom_provider_url() -> None:
    settings = Settings(
        _env_file=None,
        grok2api_base_url="http://107.174.124.163:8000",
    )
    providers = _MemoryProviders(
        [
            {
                "id": "custom",
                "name": "其他网关",
                "base_url": "https://api.example.test/v1",
                "is_default": True,
                "enabled": True,
                "models": [],
                "api_key_configured": False,
            }
        ]
    )

    ChatService(settings=settings, providers=providers).bootstrap()

    assert providers.rows[0]["base_url"] == "https://api.example.test/v1"


@pytest.mark.asyncio
async def test_default_gateway_lists_models_from_live_grok2api_url() -> None:
    settings = Settings(
        _env_file=None,
        grok2api_base_url="http://107.174.124.163:8000",
    )
    providers = MagicMock()
    providers.get_default.return_value = {
        "id": "gateway",
        "name": "默认网关",
        "base_url": "http://127.0.0.1:8000",
        "api_key": "",
        "models": [],
        "enabled": True,
    }
    response = MagicMock(status_code=200, text="")
    response.json.return_value = {"data": [{"id": "grok-4.5"}]}
    session = MagicMock()
    session.get = AsyncMock(return_value=response)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.chat_service.CurlAsyncSession", return_value=session):
        models = await ChatService(settings=settings, providers=providers).list_models("")

    assert models == [
        {"id": "grok-4.5", "name": "grok-4.5", "owned_by": "默认网关"}
    ]
    assert session.get.await_args.args[0] == "http://107.174.124.163:8000/v1/models"


@pytest.mark.asyncio
async def test_custom_provider_lists_models_from_its_own_url() -> None:
    settings = Settings(
        _env_file=None,
        grok2api_base_url="http://107.174.124.163:8000",
    )
    providers = MagicMock()
    providers.get.return_value = {
        "id": "custom",
        "name": "其他网关",
        "base_url": "https://api.example.test/v1",
        "api_key": "",
        "models": [],
        "enabled": True,
    }
    response = MagicMock(status_code=200, text="")
    response.json.return_value = {"data": [{"id": "other"}]}
    session = MagicMock()
    session.get = AsyncMock(return_value=response)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.chat_service.CurlAsyncSession", return_value=session):
        await ChatService(settings=settings, providers=providers).list_models("custom")

    assert session.get.await_args.args[0] == "https://api.example.test/v1/models"


class _MemoryProviders:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def list(self) -> list[dict[str, object]]:
        return list(self.rows)

    def update(self, provider_id: str, values: dict[str, object]) -> dict[str, object] | None:
        for row in self.rows:
            if row["id"] == provider_id:
                row.update(values)
                return row
        return None


def test_root_provider_uses_responses_only() -> None:
    assert ChatService._completion_urls("https://api.test") == [
        "https://api.test/v1/responses",
    ]


def test_streaming_curl_options_force_http11_only_for_cleartext() -> None:
    cleartext = streaming_curl_options("http://127.0.0.1:8000")
    https = streaming_curl_options("https://api.lvyrix.com")

    assert cleartext[CurlOpt.ACCEPT_ENCODING] == b"identity"
    assert cleartext[CurlOpt.HTTP_VERSION] == CurlHttpVersion.V1_1
    assert CurlOpt.HTTP_VERSION not in https
    assert https[CurlOpt.TCP_NODELAY] == 1


@pytest.mark.asyncio
async def test_abort_curl_stream_prefers_aclose_over_handle_close() -> None:
    response = MagicMock()
    response.quit_now = MagicMock()
    response.curl = MagicMock()
    response.aclose = AsyncMock()
    response.astream_task = None

    await abort_curl_stream(response)

    response.quit_now.set.assert_called_once()
    response.aclose.assert_awaited_once()
    response.curl.close.assert_not_called()


@pytest.mark.asyncio
async def test_abort_curl_stream_closes_handle_when_aclose_missing() -> None:
    response = MagicMock()
    response.quit_now = MagicMock()
    response.curl = MagicMock()
    response.aclose = None
    response.astream_task = None

    await abort_curl_stream(response)

    response.quit_now.set.assert_called_once()
    response.curl.close.assert_called_once()


@pytest.mark.asyncio
async def test_abort_curl_stream_cancels_hanging_astream_task() -> None:
    async def hang() -> None:
        await asyncio.sleep(30)

    response = MagicMock()
    response.quit_now = MagicMock()
    response.curl = MagicMock()
    response.aclose = AsyncMock(side_effect=lambda: asyncio.sleep(30))
    response.astream_task = asyncio.create_task(hang())

    await asyncio.wait_for(abort_curl_stream(response), timeout=1)

    response.quit_now.set.assert_called_once()
    assert response.astream_task.cancelled() or response.astream_task.done()
    response.curl.close.assert_not_called()


@pytest.mark.asyncio
async def test_abort_curl_stream_does_not_wait_for_hanging_aclose() -> None:
    async def hang_aclose() -> None:
        await asyncio.sleep(30)

    response = MagicMock()
    response.quit_now = MagicMock()
    response.curl = MagicMock()
    response.aclose = hang_aclose
    response.astream_task = None

    await asyncio.wait_for(abort_curl_stream(response), timeout=1)

    response.quit_now.set.assert_called_once()
    response.curl.close.assert_called_once()

