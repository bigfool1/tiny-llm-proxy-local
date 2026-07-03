from __future__ import annotations

import asyncio
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Skill, SkillVersion, Workspace, WorkspaceSkillInstall
from app.db.repositories import (
    get_or_create_default_user,
    get_or_create_default_workspace,
)
from app.db.session import AsyncSessionMaker

DEMO_SKILL_VERSION = "1.0.0"


def build_demo_skill_payload() -> dict[str, object]:
    return {
        "name": "contract-reviewer",
        "description": "审查合同条款风险，输出风险点和修改建议。",
        "tags": ["合同", "风险", "条款"],
        "trigger_examples": ["帮我审查这份合同", "这个合同有什么风险"],
        "private_prompt": (
            "你是合同审查专家。重点检查付款、违约、自动续约、保密、责任限制和管辖条款。"
        ),
        "routing_hint": "当用户请求审查合同、条款、协议或法律风险时使用。",
        "output_expectation": "输出风险等级、原文依据和建议改法。",
    }


async def seed_dev_data() -> None:
    async with AsyncSessionMaker() as session:
        workspace = await get_or_create_default_workspace(session)
        await get_or_create_default_user(session, workspace)
        skill = await get_or_create_demo_skill(session, workspace)
        version = await get_or_create_demo_skill_version(session, skill)
        await get_or_create_demo_install(session, workspace, skill, version)
        await session.commit()


async def get_or_create_demo_skill(
    session: AsyncSession,
    workspace: Workspace,
) -> Skill:
    payload = build_demo_skill_payload()
    result = await session.execute(
        select(Skill).where(
            Skill.owner_workspace_id == workspace.id,
            Skill.name == payload["name"],
        )
    )
    skill = result.scalar_one_or_none()
    if skill is not None:
        return skill

    tags = cast(list[str], payload["tags"])
    trigger_examples = cast(list[str], payload["trigger_examples"])
    skill = Skill(
        owner_workspace_id=workspace.id,
        name=str(payload["name"]),
        description=str(payload["description"]),
        tags=tags,
        trigger_examples=trigger_examples,
    )
    session.add(skill)
    await session.flush()
    return skill


async def get_or_create_demo_skill_version(
    session: AsyncSession,
    skill: Skill,
) -> SkillVersion:
    payload = build_demo_skill_payload()
    result = await session.execute(
        select(SkillVersion).where(
            SkillVersion.skill_id == skill.id,
            SkillVersion.version == DEMO_SKILL_VERSION,
        )
    )
    version = result.scalar_one_or_none()
    if version is not None:
        return version

    version = SkillVersion(
        skill_id=skill.id,
        version=DEMO_SKILL_VERSION,
        private_prompt=str(payload["private_prompt"]),
        routing_hint=str(payload["routing_hint"]),
        output_expectation=str(payload["output_expectation"]),
        execution_mode="single_call",
        execution_engine="model_gateway",
        is_published=True,
    )
    session.add(version)
    await session.flush()
    return version


async def get_or_create_demo_install(
    session: AsyncSession,
    workspace: Workspace,
    skill: Skill,
    version: SkillVersion,
) -> WorkspaceSkillInstall:
    result = await session.execute(
        select(WorkspaceSkillInstall).where(
            WorkspaceSkillInstall.workspace_id == workspace.id,
            WorkspaceSkillInstall.skill_id == skill.id,
        )
    )
    install = result.scalar_one_or_none()
    if install is not None:
        return install

    install = WorkspaceSkillInstall(
        workspace_id=workspace.id,
        skill_id=skill.id,
        enabled_version_id=version.id,
        is_enabled=True,
    )
    session.add(install)
    await session.flush()
    return install


if __name__ == "__main__":
    asyncio.run(seed_dev_data())
