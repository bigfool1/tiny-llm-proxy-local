from collections.abc import AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.chat.service import ChatService
from app.db.models import (
    Base,
    Conversation,
    Message,
    Skill,
    SkillInvocation,
    SkillVersion,
    User,
    Workspace,
    WorkspaceSkillInstall,
)
from app.memory.backends import FakeMemoryBackend
from app.memory.schemas import MemoryBlock
from app.memory.service import MemoryService
from app.model_gateway.schemas import (
    ChatMessage,
    ModelCompletion,
    ModelStreamChunk,
    ModelUsage,
)
from app.skills.router import SkillRouter


class FakeModelGateway:
    def __init__(self) -> None:
        self.completed_messages: list[ChatMessage] = []
        self.streamed_messages: list[ChatMessage] = []

    async def complete(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
    ) -> ModelCompletion:
        self.completed_messages = messages
        return ModelCompletion(
            text="这是回答",
            usage=ModelUsage(prompt_tokens=10, completion_tokens=4),
            model=model or "fake",
        )

    async def stream(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
    ) -> AsyncIterator[ModelStreamChunk]:
        self.streamed_messages = messages
        yield ModelStreamChunk(text="这")
        yield ModelStreamChunk(text="是回答")
        yield ModelStreamChunk(usage=ModelUsage(prompt_tokens=11, completion_tokens=4))


class FakeRoutingModelGateway:
    def __init__(self) -> None:
        self.completed_messages: list[ChatMessage] = []
        self.routing_calls = 0

    async def complete(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
    ) -> ModelCompletion:
        if len(messages) == 1 and "可用 skill" in messages[0].content:
            self.routing_calls += 1
            return ModelCompletion(
                text='{"skill_id": 1, "reason": "语义匹配", "confidence": 0.8}',
                usage=ModelUsage(prompt_tokens=3, completion_tokens=1),
                model=model or "fake-router",
            )

        self.completed_messages = messages
        return ModelCompletion(
            text="合同审查回答",
            usage=ModelUsage(prompt_tokens=20, completion_tokens=5),
            model=model or "fake",
        )

    async def stream(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
    ) -> AsyncIterator[ModelStreamChunk]:
        yield ModelStreamChunk(text="合同")
        yield ModelStreamChunk(text="审查回答")
        yield ModelStreamChunk(usage=ModelUsage(prompt_tokens=21, completion_tokens=5))


class BrokenMemoryBackend:
    async def search(
        self,
        query: str,
        filters: dict[str, object],
        top_k: int,
    ) -> list[MemoryBlock]:
        raise RuntimeError("memory unavailable")

    async def add(
        self,
        messages: list[dict[str, str]],
        user_id: str,
        metadata: dict[str, object],
    ) -> None:
        raise RuntimeError("memory unavailable")


async def test_chat_service_returns_answer_with_no_skill() -> None:
    service = ChatService(
        skill_router=SkillRouter(model_gateway=None),
        memory_service=MemoryService(backend=FakeMemoryBackend()),
        model_gateway=FakeModelGateway(),
    )

    response = await service.send_message_for_test("你好")

    assert response.message.content == "这是回答"
    assert response.routing.skill_used is False
    assert response.usage.prompt_tokens == 10


async def test_chat_service_treats_memory_failure_as_empty_memory() -> None:
    service = ChatService(
        skill_router=SkillRouter(model_gateway=None),
        memory_service=MemoryService(backend=BrokenMemoryBackend()),
        model_gateway=FakeModelGateway(),
    )

    response = await service.send_message_for_test("你好")

    assert response.message.content == "这是回答"
    assert response.memory.hit_count == 0


async def test_create_conversation_does_not_initialize_memory_backend() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    service = ChatService(model_gateway=FakeModelGateway())

    async with sessionmaker() as session:
        conversation = await service.create_conversation(session)

    assert conversation.id is not None
    await engine.dispose()


async def test_default_skill_router_uses_model_gateway_for_production_routing() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    memory_backend = FakeMemoryBackend()
    model_gateway = FakeRoutingModelGateway()
    service = ChatService(
        memory_service=MemoryService(backend=memory_backend),
        model_gateway=model_gateway,
    )

    async with sessionmaker() as session:
        workspace = Workspace(name="demo")
        user = User(workspace=workspace, name="User", email="user@example.com")
        conversation = Conversation(workspace=workspace, user=user, title="chat")
        skill = Skill(
            owner_workspace=workspace,
            name="contract-reviewer",
            description="审查合同风险",
            tags=["contract-only-tag"],
            trigger_examples=["review a contract"],
        )
        version = SkillVersion(
            skill=skill,
            version="1.0.0",
            private_prompt="只输出合同风险分析。",
            output_expectation="列出风险和修改建议",
        )
        install = WorkspaceSkillInstall(
            workspace=workspace,
            skill=skill,
            enabled_version=version,
            is_enabled=True,
        )
        session.add(install)
        await session.commit()
        skill_id = skill.id
        version_id = version.id
        conversation_id = conversation.id

        response = await service.send_message(
            session,
            conversation_id,
            "请看看这个协议有没有问题",
        )
        invocations = (await session.execute(select(SkillInvocation))).scalars().all()

    system_prompt = model_gateway.completed_messages[0].content

    assert model_gateway.routing_calls == 1
    assert response.routing.skill_used is True
    assert response.routing.skill_id == skill_id
    assert response.routing.skill_version_id == version_id
    assert "只输出合同风险分析。" in system_prompt
    assert "输出要求：列出风险和修改建议" in system_prompt
    assert len(invocations) == 1
    assert invocations[0].skill_id == skill_id
    assert invocations[0].skill_version_id == version_id
    await engine.dispose()


async def test_send_message_persists_exchange_invocation_and_memory() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    memory_backend = FakeMemoryBackend(
        results=[
            {
                "memory": "用户喜欢中文回答",
                "score": 0.9,
                "id": "mem_1",
                "metadata": {"scope": "user"},
            }
        ]
    )
    service = ChatService(
        skill_router=SkillRouter(model_gateway=None),
        memory_service=MemoryService(backend=memory_backend),
        model_gateway=FakeModelGateway(),
    )

    async with sessionmaker() as session:
        conversation = await service.create_conversation(session)
        response = await service.send_message(session, conversation.id, "你好")

        messages = (
            (await session.execute(select(Message).order_by(Message.id.asc())))
            .scalars()
            .all()
        )
        invocations = (await session.execute(select(SkillInvocation))).scalars().all()

    assert response.message.content == "这是回答"
    assert response.memory.hit_count == 1
    assert [message.role for message in messages] == ["user", "assistant"]
    assert [message.content for message in messages] == ["你好", "这是回答"]
    assert len(invocations) == 1
    assert invocations[0].message_id == messages[0].id
    assert invocations[0].memory_hit_count == 1
    assert invocations[0].prompt_tokens == 10
    assert memory_backend.last_messages == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "这是回答"},
    ]
    await engine.dispose()


async def test_stream_message_yields_routing_memory_delta_done_events() -> None:
    service = ChatService(
        skill_router=SkillRouter(model_gateway=None),
        memory_service=MemoryService(backend=FakeMemoryBackend()),
        model_gateway=FakeModelGateway(),
    )

    events = [event async for event in service.stream_for_test("你好")]

    assert [event.event for event in events] == [
        "routing",
        "memory",
        "delta",
        "delta",
        "done",
    ]
    assert events[0].data["skill_used"] is False
    assert events[1].data["hit_count"] == 0
    assert events[-1].data["usage"] == {
        "prompt_tokens": 11,
        "completion_tokens": 4,
    }


async def test_stream_message_persists_exchange_invocation_and_memory() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    memory_backend = FakeMemoryBackend()
    model_gateway = FakeRoutingModelGateway()
    service = ChatService(
        memory_service=MemoryService(backend=memory_backend),
        model_gateway=model_gateway,
    )

    async with sessionmaker() as session:
        workspace = Workspace(name="demo")
        user = User(workspace=workspace, name="User", email="user@example.com")
        conversation = Conversation(workspace=workspace, user=user, title="chat")
        skill = Skill(
            owner_workspace=workspace,
            name="contract-reviewer",
            description="审查合同风险",
            tags=["contract-only-tag"],
            trigger_examples=["review a contract"],
        )
        version = SkillVersion(
            skill=skill,
            version="1.0.0",
            private_prompt="只输出合同风险分析。",
            output_expectation="列出风险和修改建议",
        )
        install = WorkspaceSkillInstall(
            workspace=workspace,
            skill=skill,
            enabled_version=version,
            is_enabled=True,
        )
        session.add(install)
        await session.commit()
        skill_id = skill.id
        version_id = version.id
        conversation_id = conversation.id

        events = [
            event
            async for event in service.stream_message(
                session,
                conversation_id,
                "请看看这个协议有没有问题",
            )
        ]
        messages = (
            (await session.execute(select(Message).order_by(Message.id.asc())))
            .scalars()
            .all()
        )
        invocations = (await session.execute(select(SkillInvocation))).scalars().all()

    assert [event.event for event in events] == [
        "routing",
        "memory",
        "delta",
        "delta",
        "done",
    ]
    assert events[0].data["skill_used"] is True
    assert events[-1].data["invocation_id"] == invocations[0].id
    assert [message.role for message in messages] == ["user", "assistant"]
    assert [message.content for message in messages] == [
        "请看看这个协议有没有问题",
        "合同审查回答",
    ]
    assert len(invocations) == 1
    assert invocations[0].skill_id == skill_id
    assert invocations[0].skill_version_id == version_id
    assert memory_backend.last_messages == [
        {"role": "user", "content": "请看看这个协议有没有问题"},
        {"role": "assistant", "content": "合同审查回答"},
    ]
    await engine.dispose()
