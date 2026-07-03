from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base, Conversation, Message, User, Workspace


async def test_workspace_user_conversation_message_roundtrip() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as session:
        workspace = Workspace(name="default")
        user = User(workspace=workspace, name="Local User", email="local@example.com")
        conversation = Conversation(workspace=workspace, user=user, title="Demo")
        message = Message(conversation=conversation, role="user", content="你好")
        session.add(message)
        await session.commit()

        result = await session.execute(select(Message).where(Message.content == "你好"))
        saved = result.scalar_one()

    assert saved.role == "user"
    assert saved.conversation_id == conversation.id
    await engine.dispose()
