from pydantic import BaseModel


class MemoryBlock(BaseModel):
    scope: str
    content: str
    score: float = 0.0
    backend_memory_id: str | None = None
