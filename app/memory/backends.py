import asyncio
from typing import Any, Protocol, cast

from mem0 import Memory

from app.config import Settings, settings
from app.memory.schemas import MemoryBlock


class MemoryBackend(Protocol):
    async def search(
        self,
        query: str,
        filters: dict[str, object],
        top_k: int,
    ) -> list[MemoryBlock]: ...

    async def add(
        self,
        messages: list[dict[str, str]],
        user_id: str,
        metadata: dict[str, object],
    ) -> None: ...


class FakeMemoryBackend:
    def __init__(self, results: list[dict[str, object]] | None = None) -> None:
        self.results = results or []
        self.last_query: str | None = None
        self.last_filters: dict[str, object] = {}
        self.last_top_k: int | None = None
        self.last_messages: list[dict[str, str]] = []
        self.last_user_id: str | None = None
        self.last_metadata: dict[str, object] = {}

    async def search(
        self,
        query: str,
        filters: dict[str, object],
        top_k: int,
    ) -> list[MemoryBlock]:
        self.last_query = query
        self.last_filters = filters
        self.last_top_k = top_k
        return [_memory_block_from_item(item) for item in self.results[:top_k]]

    async def add(
        self,
        messages: list[dict[str, str]],
        user_id: str,
        metadata: dict[str, object],
    ) -> None:
        self.last_messages = messages
        self.last_user_id = user_id
        self.last_metadata = metadata


class Mem0Backend:
    def __init__(self, memory: Any | None = None) -> None:
        self.memory = memory or Memory.from_config(_build_mem0_config(settings))

    async def search(
        self,
        query: str,
        filters: dict[str, object],
        top_k: int,
    ) -> list[MemoryBlock]:
        raw = await asyncio.to_thread(
            self.memory.search,
            query=query,
            filters=filters,
            top_k=top_k,
        )
        raw_results = raw.get("results", raw) if isinstance(raw, dict) else raw
        results = cast(list[dict[str, object]], raw_results)
        return [_memory_block_from_item(item) for item in results]

    async def add(
        self,
        messages: list[dict[str, str]],
        user_id: str,
        metadata: dict[str, object],
    ) -> None:
        await asyncio.to_thread(
            self.memory.add,
            messages,
            user_id=user_id,
            metadata=metadata,
        )


def _build_mem0_config(app_settings: Settings) -> dict[str, object]:
    return {
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "url": app_settings.qdrant_url,
                "collection_name": app_settings.qdrant_collection,
            },
        },
        "llm": {
            "provider": "deepseek",
            "config": {
                "model": app_settings.default_model,
                "api_key": app_settings.model_api_key,
                "deepseek_base_url": app_settings.model_api_base_url,
            },
        },
        "embedder": {
            "provider": "openai",
            "config": {
                "model": app_settings.embedding_model,
                "api_key": app_settings.siliconflow_api_key,
                "openai_base_url": app_settings.siliconflow_base_url,
            },
        },
        "history_db_path": app_settings.mem0_history_db_path,
    }


def _memory_block_from_item(item: dict[str, object]) -> MemoryBlock:
    metadata = item.get("metadata")
    metadata_dict = metadata if isinstance(metadata, dict) else {}
    typed_metadata = cast(dict[str, Any], metadata_dict)
    memory_id = item.get("id")
    score = item.get("score", 0.0)
    return MemoryBlock(
        scope=str(typed_metadata.get("scope", "user")),
        content=str(item.get("memory", "")),
        score=float(score) if isinstance(score, int | float | str) else 0.0,
        backend_memory_id=str(memory_id) if memory_id else None,
    )
