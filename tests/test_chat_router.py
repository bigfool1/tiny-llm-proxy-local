from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.chat.router import get_chat_service
from app.chat.service import ChatService
from app.db.models import Base
from app.db.session import get_db_session
from app.main import create_app
from app.memory.backends import FakeMemoryBackend
from app.memory.service import MemoryService
from app.model_gateway.schemas import (
    ChatMessage,
    ModelCompletion,
    ModelStreamChunk,
    ModelUsage,
)
from app.skills.router import SkillRouter


class FakeModelGateway:
    async def complete(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
    ) -> ModelCompletion:
        return ModelCompletion(
            text="路由回答",
            usage=ModelUsage(prompt_tokens=3, completion_tokens=2),
            model=model or "fake",
        )

    async def stream(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
    ) -> AsyncIterator[ModelStreamChunk]:
        yield ModelStreamChunk(text="你")
        yield ModelStreamChunk(text="好")
        yield ModelStreamChunk(usage=ModelUsage(prompt_tokens=3, completion_tokens=2))


def test_chat_page_route_exists() -> None:
    from fastapi.testclient import TestClient

    client = TestClient(create_app())

    response = client.get("/")

    assert response.status_code == 200
    assert "Skill Runtime" in response.text


@pytest.mark.asyncio
async def test_chat_routes_create_get_send_and_stream() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app()

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_chat_service] = lambda: ChatService(
        skill_router=SkillRouter(model_gateway=None),
        memory_service=MemoryService(backend=FakeMemoryBackend()),
        model_gateway=FakeModelGateway(),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post("/chat/conversations")
        assert create_response.status_code == 200
        conversation_id = create_response.json()["conversation_id"]

        message_response = await client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={"content": "你好"},
        )
        assert message_response.status_code == 200
        assert message_response.json()["message"]["content"] == "路由回答"

        get_response = await client.get(f"/chat/conversations/{conversation_id}")
        assert get_response.status_code == 200
        assert [item["role"] for item in get_response.json()["messages"]] == [
            "user",
            "assistant",
        ]

        stream_response = await client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={"content": "继续", "stream": True},
        )
        assert stream_response.status_code == 200
        assert stream_response.headers["content-type"].startswith("text/event-stream")
        body = stream_response.text

    assert "event: routing" in body
    assert 'data: {"hit_count": 0}' in body
    assert "event: delta" in body
    assert "event: done" in body
    assert "\\u4f60" not in body
    await engine.dispose()
