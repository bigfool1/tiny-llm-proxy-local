from collections.abc import Callable
from typing import TypeVar

from pytest import MonkeyPatch

from app.config import Settings
from app.memory.backends import FakeMemoryBackend, Mem0Backend, _build_mem0_config
from app.memory.service import MemoryService

T = TypeVar("T")


async def test_memory_service_maps_scope_filters() -> None:
    backend = FakeMemoryBackend(
        results=[
            {
                "memory": "用户喜欢中文回答",
                "score": 0.8,
                "id": "mem_1",
                "metadata": {"scope": "user"},
            }
        ]
    )
    service = MemoryService(backend=backend)

    blocks = await service.retrieve(
        query="继续刚才的项目",
        workspace_id=1,
        user_id=2,
        conversation_id=3,
        top_k=5,
    )

    assert backend.last_filters == {
        "workspace_id": "1",
        "user_id": "2",
        "conversation_id": "3",
    }
    assert blocks[0].content == "用户喜欢中文回答"
    assert blocks[0].scope == "user"


async def test_memory_service_maps_backend_results() -> None:
    backend = FakeMemoryBackend(
        results=[
            {
                "memory": "项目使用 FastAPI",
                "score": 0.9,
                "id": "mem_2",
                "metadata": {"scope": "conversation"},
            }
        ]
    )
    service = MemoryService(backend=backend)

    blocks = await service.retrieve(
        query="项目栈是什么",
        workspace_id=10,
        user_id=20,
        conversation_id=30,
        top_k=1,
    )

    assert len(blocks) == 1
    assert blocks[0].scope == "conversation"
    assert blocks[0].content == "项目使用 FastAPI"
    assert blocks[0].score == 0.9
    assert blocks[0].backend_memory_id == "mem_2"


async def test_memory_service_add_from_exchange_passes_messages_and_metadata() -> None:
    backend = FakeMemoryBackend()
    service = MemoryService(backend=backend)

    await service.add_from_exchange(
        user_message="请记住我偏好中文",
        assistant_message="好的，我会优先用中文回答。",
        workspace_id=7,
        user_id=8,
        conversation_id=9,
    )

    assert backend.last_messages == [
        {"role": "user", "content": "请记住我偏好中文"},
        {"role": "assistant", "content": "好的，我会优先用中文回答。"},
    ]
    assert backend.last_user_id == "8"
    assert backend.last_metadata == {
        "workspace_id": "7",
        "user_id": "8",
        "conversation_id": "9",
        "scope": "conversation",
    }


class FakeMem0Memory:
    def __init__(self) -> None:
        self.search_calls: list[dict[str, object]] = []
        self.add_calls: list[dict[str, object]] = []

    def search(
        self,
        query: str,
        filters: dict[str, object],
        top_k: int,
    ) -> dict[str, list[dict[str, object]]]:
        self.search_calls.append({"query": query, "filters": filters, "top_k": top_k})
        return {
            "results": [
                {
                    "memory": "用户偏好短回答",
                    "score": 0.7,
                    "id": "mem_sync_1",
                    "metadata": {"scope": "user"},
                }
            ]
        }

    def add(
        self,
        messages: list[dict[str, str]],
        user_id: str,
        metadata: dict[str, object],
    ) -> None:
        self.add_calls.append(
            {"messages": messages, "user_id": user_id, "metadata": metadata}
        )


async def test_mem0_backend_maps_injected_memory_through_thread(
    monkeypatch: MonkeyPatch,
) -> None:
    memory = FakeMem0Memory()
    backend = Mem0Backend(memory=memory)
    thread_calls: list[str] = []

    async def fake_to_thread(
        func: Callable[..., T],
        /,
        *args: object,
        **kwargs: object,
    ) -> T:
        thread_calls.append(func.__name__)
        return func(*args, **kwargs)

    monkeypatch.setattr("app.memory.backends.asyncio.to_thread", fake_to_thread)

    blocks = await backend.search(
        query="偏好是什么",
        filters={"user_id": "2"},
        top_k=3,
    )
    await backend.add(
        messages=[{"role": "user", "content": "记住短回答"}],
        user_id="2",
        metadata={"scope": "user"},
    )

    assert memory.search_calls == [
        {"query": "偏好是什么", "filters": {"user_id": "2"}, "top_k": 3}
    ]
    assert blocks[0].content == "用户偏好短回答"
    assert blocks[0].backend_memory_id == "mem_sync_1"
    assert memory.add_calls == [
        {
            "messages": [{"role": "user", "content": "记住短回答"}],
            "user_id": "2",
            "metadata": {"scope": "user"},
        }
    ]
    assert thread_calls == ["search", "add"]


def test_build_mem0_config_uses_project_settings() -> None:
    settings = Settings(
        qdrant_url="http://qdrant.local:6333",
        qdrant_collection="project_memories",
        default_model="deepseek-chat",
        model_api_base_url="https://deepseek.local",
        model_api_key="secret",
        siliconflow_base_url="https://sf.local/v1",
        siliconflow_api_key="sf-secret",
        embedding_model="BAAI/bge-m3",
        rerank_model="BAAI/bge-reranker-v2-m3",
        mem0_history_db_path=".mem0/test-history.db",
    )

    config = _build_mem0_config(settings)

    assert config["vector_store"] == {
        "provider": "qdrant",
        "config": {
            "url": "http://qdrant.local:6333",
            "collection_name": "project_memories",
        },
    }
    assert config["llm"] == {
        "provider": "deepseek",
        "config": {
            "model": "deepseek-chat",
            "api_key": "secret",
            "deepseek_base_url": "https://deepseek.local",
        },
    }
    assert config["embedder"] == {
        "provider": "openai",
        "config": {
            "model": "BAAI/bge-m3",
            "api_key": "sf-secret",
            "openai_base_url": "https://sf.local/v1",
        },
    }
    assert "reranker" not in config
    assert config["history_db_path"] == ".mem0/test-history.db"
