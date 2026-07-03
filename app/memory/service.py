from app.config import settings
from app.memory.backends import FakeMemoryBackend, Mem0Backend, MemoryBackend
from app.memory.schemas import MemoryBlock


class MemoryService:
    def __init__(self, backend: MemoryBackend | None = None) -> None:
        if backend is not None:
            self.backend = backend
        elif settings.mem0_backend == "fake":
            self.backend = FakeMemoryBackend()
        else:
            self.backend = Mem0Backend()

    async def retrieve(
        self,
        query: str,
        workspace_id: int,
        user_id: int,
        conversation_id: int,
        top_k: int,
    ) -> list[MemoryBlock]:
        filters: dict[str, object] = {
            "workspace_id": str(workspace_id),
            "user_id": str(user_id),
            "conversation_id": str(conversation_id),
        }
        return await self.backend.search(query=query, filters=filters, top_k=top_k)

    async def add_from_exchange(
        self,
        user_message: str,
        assistant_message: str,
        workspace_id: int,
        user_id: int,
        conversation_id: int,
    ) -> None:
        messages = [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": assistant_message},
        ]
        metadata: dict[str, object] = {
            "workspace_id": str(workspace_id),
            "user_id": str(user_id),
            "conversation_id": str(conversation_id),
            "scope": "conversation",
        }
        await self.backend.add(
            messages=messages,
            user_id=str(user_id),
            metadata=metadata,
        )
