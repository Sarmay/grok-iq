from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from curl_cffi.requests import AsyncSession as CurlAsyncSession

from app.core.config import Settings
from app.integrations.grok2api.client import IntegrationError
from app.integrations.grok2api.http_session import open_curl_session
from app.persistence.chat_provider_repository import ChatProviderRepository


@dataclass(slots=True)
class ChatStream:
    session: CurlAsyncSession
    response: Any


class ChatUpstreamError(IntegrationError):
    """An upstream provider response whose HTTP status should reach the UI."""


# Seeded once, then kept aligned with the live grok2api address. A provider
# under any other name keeps the Base URL saved in the playground.
DEFAULT_GATEWAY_PROVIDER_NAME = "默认网关"
_BOOTSTRAP_GATEWAY_URLS = frozenset(
    {
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://host.docker.internal:8000",
    }
)


class ChatService:
    """Manages OpenAI-compatible providers and proxies playground requests."""

    def __init__(
        self,
        *,
        settings: Settings,
        providers: ChatProviderRepository,
    ):
        self.settings = settings
        self.providers = providers

    def bootstrap(self) -> None:
        gateway = self.settings.normalized_gateway_base_url
        providers = self.providers.list()
        if not providers:
            self.providers.create(
                name=DEFAULT_GATEWAY_PROVIDER_NAME,
                base_url=gateway,
                api_key="",
                models=[],
                enabled=True,
                is_default=True,
            )
            return
        for provider in providers:
            stored = str(provider.get("base_url") or "").rstrip("/")
            if stored == gateway:
                continue
            if self._follows_gateway(provider) or self._has_stale_bootstrap_url(provider):
                self.providers.update(provider["id"], {"base_url": gateway})

    def list_providers(self) -> list[dict[str, Any]]:
        return [self._public(self._with_live_gateway(item)) for item in self.providers.list()]

    def create_provider(self, values: dict[str, Any]) -> dict[str, Any]:
        name = str(values["name"]).strip()
        provider = self.providers.create(
            name=name,
            base_url=self._provider_base_url(name, str(values["base_url"])),
            api_key=str(values.get("api_key") or "").strip(),
            models=self._normalize_models(values.get("models", [])),
            enabled=bool(values.get("enabled", True)),
            is_default=bool(values.get("is_default", False)),
        )
        return self._public(self._with_live_gateway(provider))

    def update_provider(
        self,
        provider_id: str,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        current = self.providers.get(provider_id)
        if current is None:
            raise ValueError("模型提供商不存在")
        changes: dict[str, Any] = {}
        if "name" in values:
            changes["name"] = str(values["name"]).strip()
        if "base_url" in values:
            changes["base_url"] = self._normalize_base_url(str(values["base_url"]))
        if "models" in values:
            changes["models"] = self._normalize_models(values["models"])
        for key in ("enabled", "is_default"):
            if key in values:
                changes[key] = bool(values[key])
        api_key = str(values.get("api_key") or "").strip()
        if api_key:
            changes["api_key"] = api_key
        elif values.get("clear_api_key"):
            changes["api_key"] = ""
        next_name = str(changes.get("name", current["name"]))
        if next_name == DEFAULT_GATEWAY_PROVIDER_NAME:
            changes["base_url"] = self.settings.normalized_gateway_base_url
        provider = self.providers.update(provider_id, changes)
        if provider is None:
            raise ValueError("模型提供商不存在")
        return self._public(self._with_live_gateway(provider))

    def delete_provider(self, provider_id: str) -> None:
        if not self.providers.delete(provider_id):
            raise ValueError("模型提供商不存在")

    def reveal_provider_api_key(self, provider_id: str) -> str:
        provider = self.providers.get(provider_id, reveal_secret=True)
        if provider is None:
            raise ValueError("模型提供商不存在")
        return str(provider.get("api_key") or "")

    async def list_models(self, provider_id: str = "") -> list[dict[str, Any]]:
        provider = self._resolve(provider_id)
        models = self._normalize_models(provider.get("models", []))
        if not models:
            models = await self._fetch_models(provider)
        return [
            {"id": model, "name": model, "owned_by": provider["name"]}
            for model in models
        ]

    async def sync_models(self, provider_id: str) -> dict[str, Any]:
        provider = self._resolve(provider_id)
        models = await self._fetch_models(provider)
        updated = self.providers.set_models(provider["id"], models)
        if updated is None:
            raise ValueError("模型提供商不存在")
        return self._public(self._with_live_gateway(updated))

    async def open_completion(
        self,
        *,
        provider_id: str,
        body: bytes,
        request_headers: Mapping[str, str],
    ) -> ChatStream:
        provider = self._resolve(provider_id)
        headers = self._upstream_headers(provider, accept="text/event-stream")
        for key in ("x-thread-id", "x-request-id"):
            value = request_headers.get(key, "").strip()
            if value:
                headers[key] = value
        candidates = self._completion_urls(provider["base_url"])
        last_error: ChatUpstreamError | None = None
        for upstream_url in candidates:
            session = open_curl_session(
                impersonate=self.settings.grok2api_http_impersonate,
                base_url=upstream_url,
            )
            upstream_body = self._prepare_completion_body(body, upstream_url)
            try:
                response = await session.post(
                    upstream_url,
                    headers=headers,
                    data=upstream_body,
                    stream=True,
                    # curl-cffi maps this to CURLOPT_ACCEPT_ENCODING. Identity
                    # avoids compression while preserving streaming reads.
                    # Impersonate still overwrites it unless curl_options is set.
                    accept_encoding="identity",
                    timeout=300,
                )
            except Exception as exc:
                await session.close()
                raise IntegrationError(f"模型提供商请求失败: {exc}") from exc
            if response.status_code < 300:
                return ChatStream(session=session, response=response)
            content = await response.acontent()
            await session.close()
            detail = content.decode("utf-8", "replace")
            last_error = ChatUpstreamError(
                f"模型提供商返回 HTTP {response.status_code}: {detail[:2000]}",
                status_code=response.status_code,
                response_body=detail[:4000],
            )
            if response.status_code not in {404, 405}:
                raise last_error
        if last_error is not None:
            raise last_error
        raise IntegrationError("模型提供商没有可用的聊天接口")

    async def _fetch_models(self, provider: dict[str, Any]) -> list[str]:
        headers = self._upstream_headers(provider, accept="application/json")
        url = self._resource_url(
            self._base_without_endpoint(provider["base_url"]),
            "models",
        )
        try:
            async with CurlAsyncSession(
                impersonate=self.settings.grok2api_http_impersonate
            ) as session:
                response = await session.get(
                    url,
                    headers=headers,
                    timeout=60,
                )
        except Exception as exc:
            raise IntegrationError(f"模型列表请求失败: {url}: {exc}") from exc
        if response.status_code >= 300:
            raise ChatUpstreamError(
                f"模型列表请求失败: {url} HTTP {response.status_code} {response.text[:1000]}",
                status_code=response.status_code,
                response_body=response.text[:4000],
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise IntegrationError("模型列表响应不是有效 JSON") from exc
        raw_items = (
            payload.get("data", payload) if isinstance(payload, dict) else payload
        )
        if not isinstance(raw_items, list):
            raise IntegrationError("模型列表响应缺少 data 数组")
        models = self._normalize_models([
            (item.get("id") or item.get("name"))
            if isinstance(item, dict)
            else item
            for item in raw_items
        ])
        return models

    def _resolve(self, provider_id: str) -> dict[str, Any]:
        provider = (
            self.providers.get(provider_id, reveal_secret=True)
            if provider_id.strip()
            else self.providers.get_default(reveal_secret=True)
        )
        if provider is None:
            raise ValueError("请先配置模型提供商")
        if not provider["enabled"]:
            raise ValueError("当前模型提供商已停用")
        return self._with_live_gateway(provider)

    def _provider_base_url(self, name: str, value: str) -> str:
        if name == DEFAULT_GATEWAY_PROVIDER_NAME:
            return self.settings.normalized_gateway_base_url
        return self._normalize_base_url(value)

    def _with_live_gateway(self, provider: dict[str, Any]) -> dict[str, Any]:
        if not self._follows_gateway(provider):
            return provider
        gateway = self.settings.normalized_gateway_base_url
        if str(provider.get("base_url") or "").rstrip("/") == gateway:
            return provider
        return {**provider, "base_url": gateway}

    @staticmethod
    def _follows_gateway(provider: dict[str, Any]) -> bool:
        return str(provider.get("name") or "") == DEFAULT_GATEWAY_PROVIDER_NAME

    def _has_stale_bootstrap_url(self, provider: dict[str, Any]) -> bool:
        """Repair a default provider still pointing at the old built-in address."""

        stored = str(provider.get("base_url") or "").rstrip("/")
        return (
            bool(provider.get("is_default"))
            and stored in _BOOTSTRAP_GATEWAY_URLS
            and stored != self.settings.normalized_gateway_base_url
        )

    @staticmethod
    def _upstream_headers(
        provider: dict[str, Any],
        *,
        accept: str,
    ) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": accept}
        if accept == "text/event-stream":
            headers["Accept-Encoding"] = "identity"
        api_key = str(provider.get("api_key") or "").strip()
        if api_key:
            headers["Authorization"] = (
                api_key if api_key.lower().startswith("bearer ") else f"Bearer {api_key}"
            )
        return headers

    @staticmethod
    def _normalize_base_url(value: str) -> str:
        normalized = value.strip().rstrip("/")
        for suffix in ("/chat/completions", "/responses", "/models"):
            if normalized.endswith(suffix):
                normalized = normalized[: -len(suffix)].rstrip("/")
                break
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Base URL 必须是有效的 HTTP(S) 地址")
        return normalized

    @classmethod
    def _completion_url(cls, base_url: str) -> str:
        return cls._completion_urls(base_url)[0]

    @classmethod
    def _completion_urls(cls, base_url: str) -> list[str]:
        normalized = base_url.rstrip("/")
        if normalized.endswith("/chat/completions"):
            return [normalized]
        if normalized.endswith("/responses"):
            return [normalized]
        return [cls._resource_url(normalized, "responses")]

    @staticmethod
    def _base_without_endpoint(base_url: str) -> str:
        normalized = base_url.rstrip("/")
        for suffix in ("/chat/completions", "/responses", "/models"):
            if normalized.endswith(suffix):
                return normalized[: -len(suffix)].rstrip("/")
        return normalized

    @staticmethod
    def _prepare_completion_body(body: bytes, upstream_url: str) -> bytes:
        """Translate the playground's chat payload for a Responses provider."""
        if not upstream_url.endswith("/responses"):
            return body
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return body
        if not isinstance(payload, dict):
            return body
        if "messages" in payload:
            messages = payload.pop("messages")
            if isinstance(messages, list):
                instructions: list[str] = []
                inputs: list[dict[str, object]] = []
                for message in messages:
                    if not isinstance(message, dict):
                        continue
                    role = str(message.get("role") or "user")
                    content = message.get("content", "")
                    if isinstance(content, list):
                        text = "".join(
                            str(part.get("text") or part.get("content") or "")
                            for part in content
                            if isinstance(part, dict)
                        )
                    else:
                        text = str(content or "")
                    if role == "system":
                        instructions.append(text)
                    else:
                        inputs.append({"role": role, "content": text})
                if instructions:
                    payload["instructions"] = "\n\n".join(instructions)
                payload["input"] = inputs
        payload.pop("stream_options", None)
        if "max_output_tokens" not in payload:
            limit = payload.pop("max_tokens", None)
            if limit is None:
                limit = payload.pop("max_completion_tokens", None)
            if limit is not None:
                payload["max_output_tokens"] = limit
        else:
            payload.pop("max_tokens", None)
            payload.pop("max_completion_tokens", None)
        payload["stream"] = True
        if "store" not in payload:
            payload["store"] = False
        if "reasoning" not in payload:
            payload["reasoning"] = {"summary": "auto"}
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    @staticmethod
    def _resource_url(base_url: str, resource: str) -> str:
        base = base_url.rstrip("/")
        return f"{base}/{resource}" if base.endswith("/v1") else f"{base}/v1/{resource}"

    @staticmethod
    def _normalize_models(values: Any) -> list[str]:
        if not isinstance(values, list):
            return []
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            model = str(value or "").strip()
            if not model or model in seen:
                continue
            result.append(model)
            seen.add(model)
        return result

    @staticmethod
    def _public(provider: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": provider["id"],
            "name": provider["name"],
            "baseUrl": provider["base_url"],
            "models": list(provider.get("models", [])),
            "enabled": bool(provider["enabled"]),
            "isDefault": bool(provider["is_default"]),
            "apiKeyConfigured": bool(provider["api_key_configured"]),
            "createdAt": provider["created_at"],
            "updatedAt": provider["updated_at"],
        }
