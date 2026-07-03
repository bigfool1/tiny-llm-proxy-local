import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.schemas import (
    ChatRequest,
    ChatResponse,
    ChatStreamEvent,
    ConversationCreateResponse,
    ConversationResponse,
)
from app.chat.service import ChatService
from app.db.session import get_db_session

router = APIRouter(prefix="/chat", tags=["chat"])


def get_chat_service() -> ChatService:
    return ChatService()


ChatSession = Annotated[AsyncSession, Depends(get_db_session)]
ChatServiceDep = Annotated[ChatService, Depends(get_chat_service)]


@router.post("/conversations")
async def create_conversation(
    session: ChatSession,
    service: ChatServiceDep,
) -> ConversationCreateResponse:
    conversation = await service.create_conversation(session)
    return ConversationCreateResponse(
        conversation_id=conversation.id,
        title=conversation.title,
    )


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: int,
    session: ChatSession,
    service: ChatServiceDep,
) -> ConversationResponse:
    response = await service.get_conversation(session, conversation_id)
    if response is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return response


@router.post("/conversations/{conversation_id}/messages", response_model=None)
async def send_message(
    conversation_id: int,
    request: ChatRequest,
    session: ChatSession,
    service: ChatServiceDep,
) -> ChatResponse | StreamingResponse:
    if request.stream:
        return StreamingResponse(
            _sse(service.stream_message(session, conversation_id, request.content)),
            media_type="text/event-stream",
        )
    try:
        return await service.send_message(session, conversation_id, request.content)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


async def _sse(events: AsyncIterator[ChatStreamEvent]) -> AsyncIterator[str]:
    try:
        async for event in events:
            yield f"event: {event.event}\n"
            yield f"data: {json.dumps(event.data, ensure_ascii=False)}\n\n"
    except ValueError as exc:
        yield "event: error\n"
        yield f"data: {json.dumps({'detail': str(exc)}, ensure_ascii=False)}\n\n"
