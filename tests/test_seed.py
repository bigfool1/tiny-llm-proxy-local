from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import (
    Base,
    Skill,
    SkillVersion,
    User,
    Workspace,
    WorkspaceSkillInstall,
)
from app.seed import build_demo_skill_payload, seed_dev_data


def test_build_demo_skill_payload_contains_contract_skill() -> None:
    payload = build_demo_skill_payload()

    assert payload["name"] == "contract-reviewer"
    assert isinstance(payload["tags"], list)
    assert "合同" in payload["tags"]
    assert "private_prompt" in payload
    assert "routing_hint" in payload
    assert "output_expectation" in payload


async def test_seed_dev_data_is_idempotent(monkeypatch) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("app.seed.AsyncSessionMaker", sessionmaker)

    await seed_dev_data()
    await seed_dev_data()

    async with sessionmaker() as session:
        workspace_count = await session.scalar(select(func.count(Workspace.id)))
        user_count = await session.scalar(select(func.count(User.id)))
        skill_count = await session.scalar(select(func.count(Skill.id)))
        version_count = await session.scalar(select(func.count(SkillVersion.id)))
        install_count = await session.scalar(
            select(func.count(WorkspaceSkillInstall.id))
        )
        skill = (
            await session.execute(
                select(Skill).where(Skill.name == "contract-reviewer")
            )
        ).scalar_one()
        version = (
            await session.execute(
                select(SkillVersion).where(
                    SkillVersion.skill_id == skill.id,
                    SkillVersion.version == "1.0.0",
                )
            )
        ).scalar_one()
        install = (
            await session.execute(
                select(WorkspaceSkillInstall).where(
                    WorkspaceSkillInstall.skill_id == skill.id
                )
            )
        ).scalar_one()

    assert workspace_count == 1
    assert user_count == 1
    assert skill_count == 1
    assert version_count == 1
    assert install_count == 1
    assert skill.tags == ["合同", "风险", "条款"]
    assert version.private_prompt.startswith("你是合同审查专家")
    assert install.enabled_version_id == version.id
    await engine.dispose()
