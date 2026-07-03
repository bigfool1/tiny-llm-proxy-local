import json

import httpx
import pytest

from app.model_gateway.client import ModelGateway
from app.model_gateway.schemas import ChatMessage


@pytest.mark.asyncio
async def test_model_gateway_complete_uses_anthropic_messages_api() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request.url == "https://example.test/v1/messages"
        assert request.headers["x-api-key"] == "test-key"
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert payload["model"] == "deepseek-chat"
        assert payload["system"] == "system prompt"
        assert payload["messages"] == [{"role": "user", "content": "你好"}]
        assert payload["stream"] is False
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "你好，我在。"}],
                "usage": {"input_tokens": 10, "output_tokens": 4},
            },
        )

    transport = httpx.MockTransport(handler)
    gateway = ModelGateway(
        api_base_url="https://example.test",
        api_key="test-key",
        default_model="deepseek-chat",
        transport=transport,
    )

    result = await gateway.complete(
        [
            ChatMessage(role="system", content="system prompt"),
            ChatMessage(role="user", content="你好"),
        ]
    )

    assert result.text == "你好，我在。"
    assert result.usage.prompt_tokens == 10
    assert result.usage.completion_tokens == 4


@pytest.mark.asyncio
async def test_model_gateway_stream_yields_delta_and_usage() -> None:
    first_delta = json.dumps(
        {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "你"},
        },
        ensure_ascii=False,
    )
    second_delta = json.dumps(
        {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "好"},
        },
        ensure_ascii=False,
    )
    chunks = [
        f"event: content_block_delta\ndata: {first_delta}\n\n",
        f"event: content_block_delta\ndata: {second_delta}\n\n",
        "event: message_delta\n"
        'data: {"type":"message_delta","usage":{"output_tokens":2}}\n\n',
        'event: message_stop\ndata: {"type":"message_stop"}\n\n',
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request.url == "https://example.test/v1/messages"
        assert payload["stream"] is True
        return httpx.Response(200, content="".join(chunks))

    gateway = ModelGateway(
        api_base_url="https://example.test",
        api_key="test-key",
        default_model="deepseek-chat",
        transport=httpx.MockTransport(handler),
    )

    result = [
        chunk
        async for chunk in gateway.stream([ChatMessage(role="user", content="hi")])
    ]

    assert [chunk.text for chunk in result if chunk.text] == ["你", "好"]
    assert result[-1].usage is not None
    assert result[-1].usage.completion_tokens == 2
