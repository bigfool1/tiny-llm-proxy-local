from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from time import perf_counter
from typing import Protocol, cast

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.schemas import (
    ChatMessageResponse,
    ChatResponse,
    ChatStreamEvent,
    ConversationResponse,
    MemoryResponse,
    RoutingResponse,
)
from app.config import settings
from app.context.assembler import ContextAssembler
from app.context.trimmer import ContextTrimmer
from app.db.models import Conversation, Message, SkillInvocation, utcnow
from app.db.repositories import (
    get_or_create_default_user,
    get_or_create_default_workspace,
)
from app.memory.schemas import MemoryBlock
from app.memory.service import MemoryService
from app.model_gateway.client import ModelGateway
from app.model_gateway.schemas import (
    ChatMessage,
    ModelCompletion,
    ModelStreamChunk,
    ModelUsage,
)
from app.skills.registry import SkillRegistry
from app.skills.router import SkillRouter
from app.skills.schemas import InstalledSkill, SkillRoutingResult


class ModelGatewayProtocol(Protocol):
    async def complete(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
    ) -> ModelCompletion: ...

    def stream(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
    ) -> AsyncIterator[ModelStreamChunk]: ...


@dataclass
class _PreparedCall:
    messages: list[ChatMessage]
    routing: SkillRoutingResult
    selected_skill: InstalledSkill | None
    memory_hit_count: int


@dataclass
class _BuiltResponse:
    response: ChatResponse
    assistant_text: str
    routing: SkillRoutingResult
    memory_hit_count: int
    latency_ms: int


class ChatService:
    def __init__(
        self,
        skill_router: SkillRouter | None = None,
        memory_service: MemoryService | None = None,
        model_gateway: ModelGatewayProtocol | None = None,
        assembler: ContextAssembler | None = None,
        trimmer: ContextTrimmer | None = None,
    ) -> None:
        self.model_gateway = model_gateway or ModelGateway()
        self.skill_router = skill_router or SkillRouter(
            model_gateway=cast(ModelGateway, self.model_gateway)
        )
        self._memory_service = memory_service
        self.assembler = assembler or ContextAssembler(
            base_prompt="你是一个支持托管 prompt skill 的 Web Chat Runtime。"
        )
        self.trimmer = trimmer or ContextTrimmer(
            max_chars=settings.context_token_budget * 4
        )

    @property
    def memory_service(self) -> MemoryService:
        if self._memory_service is None:
            self._memory_service = MemoryService()
        return self._memory_service

    async def create_conversation(self, session: AsyncSession) -> Conversation:
        workspace = await get_or_create_default_workspace(session)
        user = await get_or_create_default_user(session, workspace)
        conversation = Conversation(
            workspace_id=workspace.id,
            user_id=user.id,
            title="New conversation",
        )
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation)
        return conversation

    async def get_conversation(
        self,
        session: AsyncSession,
        conversation_id: int,
    ) -> ConversationResponse | None:
        conversation = await self._get_conversation(session, conversation_id)
        if conversation is None:
            return None

        result = await session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id.asc())
        )
        messages = [
            ChatMessageResponse(
                id=message.id,
                role=message.role,
                content=message.content,
            )
            for message in result.scalars().all()
        ]
        return ConversationResponse(
            conversation_id=conversation.id,
            title=conversation.title,
            messages=messages,
        )

    async def send_message_for_test(self, content: str) -> ChatResponse:
        built = await self._build_response(
            content=content,
            workspace_id=1,
            user_id=1,
            conversation_id=1,
            history=[],
            installed_skills=[],
        )
        return built.response

    async def send_message(
        self,
        session: AsyncSession,
        conversation_id: int,
        content: str,
    ) -> ChatResponse:
        conversation = await self._get_conversation(session, conversation_id)
        if conversation is None:
            raise ValueError("conversation not found")

        user_message = Message(
            conversation_id=conversation_id,
            role="user",
            content=content,
        )
        session.add(user_message)
        await session.flush()

        history = await self._load_history(
            session=session,
            conversation_id=conversation_id,
            exclude_message_id=user_message.id,
        )
        installed_skills = await SkillRegistry(session).list_installed(
            conversation.workspace_id
        )
        built = await self._build_response(
            content=content,
            workspace_id=conversation.workspace_id,
            user_id=conversation.user_id,
            conversation_id=conversation_id,
            history=history,
            installed_skills=installed_skills,
        )

        assistant_message = Message(
            conversation_id=conversation_id,
            role="assistant",
            content=built.assistant_text,
        )
        session.add(assistant_message)
        invocation = self._make_invocation(
            conversation=conversation,
            message_id=user_message.id,
            built=built,
        )
        invocation.finished_at = utcnow()
        session.add(invocation)
        await session.commit()
        await self._add_memory_from_exchange(
            user_message=content,
            assistant_message=built.assistant_text,
            workspace_id=conversation.workspace_id,
            user_id=conversation.user_id,
            conversation_id=conversation_id,
        )
        return built.response

    async def stream_for_test(self, content: str) -> AsyncIterator[ChatStreamEvent]:
        async for event in self._stream_events(
            content=content,
            workspace_id=1,
            user_id=1,
            conversation_id=1,
            history=[],
            installed_skills=[],
        ):
            yield event

    async def stream_message(
        self,
        session: AsyncSession,
        conversation_id: int,
        content: str,
    ) -> AsyncIterator[ChatStreamEvent]:
        conversation = await self._get_conversation(session, conversation_id)
        if conversation is None:
            raise ValueError("conversation not found")

        user_message = Message(
            conversation_id=conversation_id,
            role="user",
            content=content,
        )
        session.add(user_message)
        await session.flush()

        history = await self._load_history(
            session=session,
            conversation_id=conversation_id,
            exclude_message_id=user_message.id,
        )
        installed_skills = await SkillRegistry(session).list_installed(
            conversation.workspace_id
        )
        prepared = await self._prepare_call(
            content=content,
            workspace_id=conversation.workspace_id,
            user_id=conversation.user_id,
            conversation_id=conversation_id,
            history=history,
            installed_skills=installed_skills,
        )
        yield self._routing_event(prepared.routing, prepared.selected_skill)
        yield ChatStreamEvent(
            event="memory",
            data={"hit_count": prepared.memory_hit_count},
        )

        started = perf_counter()
        usage = ModelUsage()
        assistant_parts: list[str] = []
        async for chunk in self.model_gateway.stream(prepared.messages):
            if chunk.text:
                assistant_parts.append(chunk.text)
                yield ChatStreamEvent(event="delta", data={"text": chunk.text})
            if chunk.usage is not None:
                usage = chunk.usage

        assistant_text = "".join(assistant_parts)
        latency_ms = int((perf_counter() - started) * 1000)
        assistant_message = Message(
            conversation_id=conversation_id,
            role="assistant",
            content=assistant_text,
        )
        session.add(assistant_message)
        built = self._built_from_stream(
            assistant_text=assistant_text,
            prepared=prepared,
            usage=usage,
            latency_ms=latency_ms,
        )
        invocation = self._make_invocation(
            conversation=conversation,
            message_id=user_message.id,
            built=built,
        )
        invocation.finished_at = utcnow()
        session.add(invocation)
        await session.commit()
        await session.refresh(invocation)
        await self._add_memory_from_exchange(
            user_message=content,
            assistant_message=assistant_text,
            workspace_id=conversation.workspace_id,
            user_id=conversation.user_id,
            conversation_id=conversation_id,
        )
        yield ChatStreamEvent(
            event="done",
            data={
                "invocation_id": invocation.id,
                "usage": usage.model_dump(),
            },
        )

    async def _build_response(
        self,
        content: str,
        workspace_id: int,
        user_id: int,
        conversation_id: int,
        history: list[ChatMessage],
        installed_skills: list[InstalledSkill],
    ) -> _BuiltResponse:
        prepared = await self._prepare_call(
            content=content,
            workspace_id=workspace_id,
            user_id=user_id,
            conversation_id=conversation_id,
            history=history,
            installed_skills=installed_skills,
        )
        started = perf_counter()
        completion = await self.model_gateway.complete(prepared.messages)
        latency_ms = int((perf_counter() - started) * 1000)
        response = ChatResponse(
            message=ChatMessageResponse(role="assistant", content=completion.text),
            routing=self._routing_response(prepared.routing, prepared.selected_skill),
            memory=MemoryResponse(hit_count=prepared.memory_hit_count),
            usage=completion.usage,
        )
        return _BuiltResponse(
            response=response,
            assistant_text=completion.text,
            routing=prepared.routing,
            memory_hit_count=prepared.memory_hit_count,
            latency_ms=latency_ms,
        )

    async def _prepare_call(
        self,
        content: str,
        workspace_id: int,
        user_id: int,
        conversation_id: int,
        history: list[ChatMessage],
        installed_skills: list[InstalledSkill],
    ) -> _PreparedCall:
        routing = await self.skill_router.route(content, installed_skills)
        selected_skill = self._select_skill(installed_skills, routing)
        memories = await self._retrieve_memory(
            content=content,
            workspace_id=workspace_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        context = self.assembler.assemble(
            skill=selected_skill,
            memories=memories,
            history=history,
            user_message=content,
        )
        return _PreparedCall(
            messages=self.trimmer.trim(context),
            routing=routing,
            selected_skill=selected_skill,
            memory_hit_count=len(memories),
        )

    async def _retrieve_memory(
        self,
        content: str,
        workspace_id: int,
        user_id: int,
        conversation_id: int,
    ) -> list[MemoryBlock]:
        try:
            return await self.memory_service.retrieve(
                query=content,
                workspace_id=workspace_id,
                user_id=user_id,
                conversation_id=conversation_id,
                top_k=settings.memory_top_k,
            )
        except Exception:
            return []

    async def _add_memory_from_exchange(
        self,
        user_message: str,
        assistant_message: str,
        workspace_id: int,
        user_id: int,
        conversation_id: int,
    ) -> None:
        try:
            await self.memory_service.add_from_exchange(
                user_message=user_message,
                assistant_message=assistant_message,
                workspace_id=workspace_id,
                user_id=user_id,
                conversation_id=conversation_id,
            )
        except Exception:
            return None

    async def _stream_events(
        self,
        content: str,
        workspace_id: int,
        user_id: int,
        conversation_id: int,
        history: list[ChatMessage],
        installed_skills: list[InstalledSkill],
    ) -> AsyncIterator[ChatStreamEvent]:
        prepared = await self._prepare_call(
            content=content,
            workspace_id=workspace_id,
            user_id=user_id,
            conversation_id=conversation_id,
            history=history,
            installed_skills=installed_skills,
        )
        yield self._routing_event(prepared.routing, prepared.selected_skill)
        yield ChatStreamEvent(
            event="memory",
            data={"hit_count": prepared.memory_hit_count},
        )
        usage = ModelUsage()
        async for chunk in self.model_gateway.stream(prepared.messages):
            if chunk.text:
                yield ChatStreamEvent(event="delta", data={"text": chunk.text})
            if chunk.usage is not None:
                usage = chunk.usage
        yield ChatStreamEvent(
            event="done",
            data={"invocation_id": 0, "usage": usage.model_dump()},
        )

    async def _load_history(
        self,
        session: AsyncSession,
        conversation_id: int,
        exclude_message_id: int | None = None,
    ) -> list[ChatMessage]:
        statement: Select[tuple[Message]] = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id.desc())
            .limit(settings.history_message_limit)
        )
        result = await session.execute(statement)
        rows = list(reversed(result.scalars().all()))
        return [
            ChatMessage(role=row.role, content=row.content)
            for row in rows
            if row.id != exclude_message_id
        ]

    async def _get_conversation(
        self,
        session: AsyncSession,
        conversation_id: int,
    ) -> Conversation | None:
        result = await session.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        return result.scalar_one_or_none()

    def _make_invocation(
        self,
        conversation: Conversation,
        message_id: int,
        built: _BuiltResponse,
    ) -> SkillInvocation:
        return SkillInvocation(
            workspace_id=conversation.workspace_id,
            user_id=conversation.user_id,
            conversation_id=conversation.id,
            message_id=message_id,
            skill_id=built.routing.skill_id,
            skill_version_id=built.routing.skill_version_id,
            routing_reason=built.routing.reason,
            routing_confidence=built.routing.confidence,
            memory_hit_count=built.memory_hit_count,
            prompt_tokens=built.response.usage.prompt_tokens,
            completion_tokens=built.response.usage.completion_tokens,
            latency_ms=built.latency_ms,
            status="success",
        )

    def _built_from_stream(
        self,
        assistant_text: str,
        prepared: _PreparedCall,
        usage: ModelUsage,
        latency_ms: int,
    ) -> _BuiltResponse:
        response = ChatResponse(
            message=ChatMessageResponse(role="assistant", content=assistant_text),
            routing=self._routing_response(prepared.routing, prepared.selected_skill),
            memory=MemoryResponse(hit_count=prepared.memory_hit_count),
            usage=usage,
        )
        return _BuiltResponse(
            response=response,
            assistant_text=assistant_text,
            routing=prepared.routing,
            memory_hit_count=prepared.memory_hit_count,
            latency_ms=latency_ms,
        )

    def _routing_event(
        self,
        routing: SkillRoutingResult,
        selected_skill: InstalledSkill | None,
    ) -> ChatStreamEvent:
        return ChatStreamEvent(
            event="routing",
            data=self._routing_response(routing, selected_skill).model_dump(),
        )

    def _routing_response(
        self,
        routing: SkillRoutingResult,
        selected_skill: InstalledSkill | None,
    ) -> RoutingResponse:
        return RoutingResponse(
            skill_used=selected_skill is not None,
            skill_id=routing.skill_id,
            skill_version_id=routing.skill_version_id,
            skill_name=selected_skill.name if selected_skill is not None else None,
            reason=routing.reason,
            confidence=routing.confidence,
        )

    def _select_skill(
        self,
        skills: list[InstalledSkill],
        routing: SkillRoutingResult,
    ) -> InstalledSkill | None:
        for skill in skills:
            if (
                skill.skill_id == routing.skill_id
                and skill.skill_version_id == routing.skill_version_id
            ):
                return skill
        return None
