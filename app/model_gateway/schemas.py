from pydantic import BaseModel


class ChatMessage(BaseModel):
    role: str
    content: str


class ModelUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0


class ModelCompletion(BaseModel):
    text: str
    usage: ModelUsage
    model: str


class ModelStreamChunk(BaseModel):
    text: str = ""
    usage: ModelUsage | None = None
