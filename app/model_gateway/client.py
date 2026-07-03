import json
from collections.abc import AsyncIterator

import httpx

from app.config import settings
from app.model_gateway.schemas import (
    ChatMessage,
    ModelCompletion,
    ModelStreamChunk,
    ModelUsage,
)


class ModelGateway:
    def __init__(
        self,
        api_base_url: str = settings.anthropic_base_url,
        api_key: str = settings.anthropic_api_key,
        default_model: str = settings.default_model,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.api_key = api_key
        self.default_model = default_model
        self.transport = transport

    async def complete(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
    ) -> ModelCompletion:
        payload = self._payload(messages=messages, model=model, stream=False)
        async with httpx.AsyncClient(
            transport=self.transport,
            timeout=settings.model_timeout_seconds,
        ) as client:
            response = await client.post(
                f"{self.api_base_url}/v1/messages",
                headers=self._headers(),
                json=payload,
            )
            response.raise_for_status()

        data = response.json()
        usage_data = data.get("usage") or {}
        return ModelCompletion(
            text=self._text_from_content(data.get("content")),
            usage=ModelUsage(
                prompt_tokens=int(usage_data.get("input_tokens", 0)),
                completion_tokens=int(usage_data.get("output_tokens", 0)),
            ),
            model=str(payload["model"]),
        )

    async def stream(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
    ) -> AsyncIterator[ModelStreamChunk]:
        payload = self._payload(messages=messages, model=model, stream=True)
        prompt_tokens = 0
        async with httpx.AsyncClient(
            transport=self.transport,
            timeout=settings.model_timeout_seconds,
        ) as client:
            async with client.stream(
                "POST",
                f"{self.api_base_url}/v1/messages",
                headers=self._headers(),
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue

                    data = json.loads(line.removeprefix("data: ").strip())
                    event_type = data.get("type")
                    if event_type == "message_start":
                        usage_data = data.get("message", {}).get("usage", {})
                        prompt_tokens = int(usage_data.get("input_tokens", 0))
                    elif event_type == "content_block_delta":
                        delta = data.get("delta") or {}
                        text = delta.get("text") or ""
                        if text:
                            yield ModelStreamChunk(text=text)
                    elif event_type == "message_delta":
                        usage_data = data.get("usage") or {}
                        yield ModelStreamChunk(
                            usage=ModelUsage(
                                prompt_tokens=prompt_tokens,
                                completion_tokens=int(
                                    usage_data.get("output_tokens", 0),
                                ),
                            )
                        )

    def _payload(
        self,
        messages: list[ChatMessage],
        model: str | None,
        stream: bool,
    ) -> dict[str, object]:
        system_parts = [
            message.content for message in messages if message.role == "system"
        ]
        conversation_messages = [
            message.model_dump()
            for message in messages
            if message.role in {"user", "assistant"}
        ]
        payload: dict[str, object] = {
            "model": model or self.default_model,
            "max_tokens": settings.anthropic_max_tokens,
            "messages": conversation_messages,
            "stream": stream,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        return payload

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": settings.anthropic_version,
        }
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    def _text_from_content(self, content: object) -> str:
        if not isinstance(content, list):
            return ""
        return "".join(
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )
