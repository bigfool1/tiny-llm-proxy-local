from pydantic import BaseModel

from app.model_gateway.schemas import ModelUsage


class ChatMessageResponse(BaseModel):
    id: int | None = None
    role: str
    content: str


class RoutingResponse(BaseModel):
    skill_used: bool
    skill_id: int | None = None
    skill_version_id: int | None = None
    skill_name: str | None = None
    reason: str
    confidence: float


class MemoryResponse(BaseModel):
    hit_count: int


class ChatResponse(BaseModel):
    message: ChatMessageResponse
    routing: RoutingResponse
    memory: MemoryResponse
    usage: ModelUsage


class ChatRequest(BaseModel):
    content: str
    stream: bool = False


class ChatStreamEvent(BaseModel):
    event: str
    data: dict[str, object]


class ConversationCreateResponse(BaseModel):
    conversation_id: int
    title: str | None = None


class ConversationResponse(BaseModel):
    conversation_id: int
    title: str | None = None
    messages: list[ChatMessageResponse]
