from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User, Workspace


async def get_or_create_default_workspace(session: AsyncSession) -> Workspace:
    result = await session.execute(select(Workspace).where(Workspace.name == "default"))
    workspace = result.scalar_one_or_none()
    if workspace is not None:
        return workspace

    workspace = Workspace(name="default")
    session.add(workspace)
    await session.flush()
    return workspace


async def get_or_create_default_user(
    session: AsyncSession,
    workspace: Workspace,
) -> User:
    result = await session.execute(
        select(User).where(
            User.workspace_id == workspace.id,
            User.email == "local@example.com",
        )
    )
    user = result.scalar_one_or_none()
    if user is not None:
        return user

    user = User(
        workspace_id=workspace.id,
        name="Local User",
        email="local@example.com",
    )
    session.add(user)
    await session.flush()
    return user
