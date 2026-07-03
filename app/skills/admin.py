from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Skill, SkillInvocation, SkillVersion, WorkspaceSkillInstall
from app.db.session import get_db_session

router = APIRouter(prefix="/admin", tags=["admin"])


def require_admin_key(
    x_admin_key: Annotated[str | None, Header()] = None,
) -> None:
    if x_admin_key != settings.admin_key:
        raise HTTPException(status_code=403, detail="invalid admin key")


async def get_admin_session(
    _: Annotated[None, Depends(require_admin_key)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AsyncIterator[AsyncSession]:
    yield session


AdminSession = Annotated[AsyncSession, Depends(get_admin_session)]


class CreateSkillRequest(BaseModel):
    owner_workspace_id: int
    name: str
    description: str
    tags: list[str]
    trigger_examples: list[str]


class CreateSkillVersionRequest(BaseModel):
    version: str
    private_prompt: str
    output_expectation: str | None = None
    routing_hint: str | None = None
    execution_mode: str = "single_call"
    execution_engine: str = "model_gateway"


class InstallSkillRequest(BaseModel):
    enabled_version_id: int


class UpdateInstalledSkillRequest(BaseModel):
    enabled_version_id: int
    is_enabled: bool


async def validate_enabled_version(
    session: AsyncSession,
    *,
    skill_id: int,
    enabled_version_id: int,
) -> None:
    result = await session.execute(
        select(SkillVersion.id).where(
            SkillVersion.id == enabled_version_id,
            SkillVersion.skill_id == skill_id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=400,
            detail="enabled version does not belong to skill",
        )


@router.post("/skills")
async def create_skill(
    payload: CreateSkillRequest,
    session: AdminSession,
) -> dict[str, int]:
    skill = Skill(
        owner_workspace_id=payload.owner_workspace_id,
        name=payload.name,
        description=payload.description,
        tags=payload.tags,
        trigger_examples=payload.trigger_examples,
    )
    session.add(skill)
    await session.commit()
    await session.refresh(skill)
    return {"skill_id": skill.id}


@router.get("/skills")
async def list_skills(
    session: AdminSession,
) -> dict[str, list[dict[str, object]]]:
    result = await session.execute(select(Skill).order_by(Skill.id.asc()))
    items = [
        {
            "skill_id": skill.id,
            "owner_workspace_id": skill.owner_workspace_id,
            "name": skill.name,
            "description": skill.description,
            "tags": list(skill.tags),
            "trigger_examples": list(skill.trigger_examples),
            "is_active": skill.is_active,
        }
        for skill in result.scalars().all()
    ]
    return {"items": items}


@router.post("/skills/{skill_id}/versions")
async def create_skill_version(
    skill_id: int,
    payload: CreateSkillVersionRequest,
    session: AdminSession,
) -> dict[str, int]:
    if payload.execution_mode != "single_call":
        raise HTTPException(status_code=400, detail="unsupported execution mode")
    if payload.execution_engine != "model_gateway":
        raise HTTPException(status_code=400, detail="unsupported execution engine")

    version = SkillVersion(
        skill_id=skill_id,
        version=payload.version,
        private_prompt=payload.private_prompt,
        routing_hint=payload.routing_hint,
        output_expectation=payload.output_expectation,
        execution_mode=payload.execution_mode,
        execution_engine=payload.execution_engine,
    )
    session.add(version)
    await session.commit()
    await session.refresh(version)
    return {"skill_version_id": version.id}


@router.post("/workspaces/{workspace_id}/skills/{skill_id}/install")
async def install_skill(
    workspace_id: int,
    skill_id: int,
    payload: InstallSkillRequest,
    session: AdminSession,
) -> dict[str, int | bool]:
    await validate_enabled_version(
        session,
        skill_id=skill_id,
        enabled_version_id=payload.enabled_version_id,
    )
    existing_result = await session.execute(
        select(WorkspaceSkillInstall).where(
            WorkspaceSkillInstall.workspace_id == workspace_id,
            WorkspaceSkillInstall.skill_id == skill_id,
        )
    )
    existing_install = existing_result.scalar_one_or_none()
    if existing_install is not None:
        raise HTTPException(status_code=409, detail="skill already installed")

    install = WorkspaceSkillInstall(
        workspace_id=workspace_id,
        skill_id=skill_id,
        enabled_version_id=payload.enabled_version_id,
        is_enabled=True,
    )
    session.add(install)
    await session.commit()
    await session.refresh(install)
    return {
        "install_id": install.id,
        "enabled_version_id": install.enabled_version_id,
        "is_enabled": install.is_enabled,
    }


@router.put("/workspaces/{workspace_id}/skills/{skill_id}")
async def update_installed_skill(
    workspace_id: int,
    skill_id: int,
    payload: UpdateInstalledSkillRequest,
    session: AdminSession,
) -> dict[str, int | bool]:
    result = await session.execute(
        select(WorkspaceSkillInstall).where(
            WorkspaceSkillInstall.workspace_id == workspace_id,
            WorkspaceSkillInstall.skill_id == skill_id,
        )
    )
    install = result.scalar_one_or_none()
    if install is None:
        raise HTTPException(status_code=404, detail="skill install not found")

    await validate_enabled_version(
        session,
        skill_id=skill_id,
        enabled_version_id=payload.enabled_version_id,
    )
    install.enabled_version_id = payload.enabled_version_id
    install.is_enabled = payload.is_enabled
    await session.commit()
    await session.refresh(install)
    return {
        "install_id": install.id,
        "enabled_version_id": install.enabled_version_id,
        "is_enabled": install.is_enabled,
    }


@router.get("/skill-invocations")
async def list_skill_invocations(
    session: AdminSession,
) -> dict[str, list[dict[str, object | None]]]:
    result = await session.execute(
        select(SkillInvocation).order_by(SkillInvocation.id.asc())
    )
    items = [
        {
            "skill_invocation_id": invocation.id,
            "workspace_id": invocation.workspace_id,
            "user_id": invocation.user_id,
            "conversation_id": invocation.conversation_id,
            "message_id": invocation.message_id,
            "skill_id": invocation.skill_id,
            "skill_version_id": invocation.skill_version_id,
            "routing_mode": invocation.routing_mode,
            "routing_reason": invocation.routing_reason,
            "routing_confidence": invocation.routing_confidence,
            "execution_mode": invocation.execution_mode,
            "execution_engine": invocation.execution_engine,
            "status": invocation.status,
        }
        for invocation in result.scalars().all()
    ]
    return {"items": items}
