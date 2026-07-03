from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Skill, SkillVersion, WorkspaceSkillInstall
from app.skills.schemas import InstalledSkill


class SkillRegistry:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_installed(self, workspace_id: int) -> list[InstalledSkill]:
        statement: Select[tuple[WorkspaceSkillInstall, Skill, SkillVersion]] = (
            select(WorkspaceSkillInstall, Skill, SkillVersion)
            .join(Skill, WorkspaceSkillInstall.skill_id == Skill.id)
            .join(
                SkillVersion,
                WorkspaceSkillInstall.enabled_version_id == SkillVersion.id,
            )
            .where(
                WorkspaceSkillInstall.workspace_id == workspace_id,
                WorkspaceSkillInstall.is_enabled.is_(True),
                Skill.is_active.is_(True),
                SkillVersion.is_published.is_(True),
            )
            .order_by(WorkspaceSkillInstall.id.asc())
        )
        result = await self.session.execute(statement)
        installed: list[InstalledSkill] = []
        for _, skill, version in result.all():
            installed.append(
                InstalledSkill(
                    skill_id=skill.id,
                    skill_version_id=version.id,
                    name=skill.name,
                    description=skill.description,
                    tags=list(skill.tags),
                    trigger_examples=list(skill.trigger_examples),
                    private_prompt=version.private_prompt,
                    output_expectation=version.output_expectation,
                )
            )
        return installed
