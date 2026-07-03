from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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
from app.main import create_app
from app.model_gateway.schemas import ChatMessage, ModelCompletion, ModelUsage
from app.skills.registry import SkillRegistry
from app.skills.router import SkillRouter
from app.skills.schemas import InstalledSkill


class StubModelGateway:
    def __init__(self, text: str) -> None:
        self.text = text

    async def complete(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
    ) -> ModelCompletion:
        return ModelCompletion(
            text=self.text,
            usage=ModelUsage(),
            model=model or "test-model",
        )


@pytest.mark.asyncio
async def test_skill_router_returns_no_skill_without_candidates() -> None:
    router = SkillRouter(model_gateway=None)

    result = await router.route("随便聊聊", [])

    assert result.skill_id is None
    assert result.skill_version_id is None
    assert result.reason == "没有可用 skill"
    assert result.confidence == 0.0


@pytest.mark.asyncio
async def test_skill_router_keyword_prefers_matching_tag() -> None:
    router = SkillRouter(model_gateway=None)
    skill = InstalledSkill(
        skill_id=1,
        skill_version_id=10,
        name="contract-reviewer",
        description="审查合同风险",
        tags=["合同", "风险"],
        trigger_examples=["帮我审查合同"],
        private_prompt="private",
        output_expectation="输出风险清单",
    )

    result = await router.route("帮我看一下这份合同有没有风险", [skill])

    assert result.skill_id == 1
    assert result.skill_version_id == 10
    assert result.reason == "命中 skill tag: 合同"
    assert result.confidence == 0.75


@pytest.mark.asyncio
async def test_skill_router_preserves_model_no_skill_result() -> None:
    router = SkillRouter(
        model_gateway=StubModelGateway(
            '{"skill_id": null, "reason": "模型判断无需 skill", "confidence": 0.2}'
        )
    )
    skill = InstalledSkill(
        skill_id=1,
        skill_version_id=10,
        name="contract-reviewer",
        description="审查合同风险",
        tags=["合同"],
        trigger_examples=["帮我审查合同"],
        private_prompt="private",
        output_expectation="输出风险清单",
    )

    result = await router.route("写一首诗", [skill])

    assert result.skill_id is None
    assert result.skill_version_id is None
    assert result.reason == "模型判断无需 skill"
    assert result.confidence == 0.2


@pytest.mark.asyncio
async def test_skill_router_preserves_json_parse_failure_reason() -> None:
    router = SkillRouter(model_gateway=StubModelGateway("not-json"))
    skill = InstalledSkill(
        skill_id=1,
        skill_version_id=10,
        name="contract-reviewer",
        description="审查合同风险",
        tags=["合同"],
        trigger_examples=["帮我审查合同"],
        private_prompt="private",
        output_expectation="输出风险清单",
    )

    result = await router.route("写一首诗", [skill])

    assert result.skill_id is None
    assert result.skill_version_id is None
    assert result.reason == "routing JSON 解析失败"
    assert result.confidence == 0.0


@pytest.mark.asyncio
async def test_skill_registry_lists_enabled_installed_skills() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as session:
        workspace = Workspace(name="demo")
        skill = Skill(
            owner_workspace=workspace,
            name="contract-reviewer",
            description="审查合同风险",
            tags=["合同"],
            trigger_examples=["帮我看合同"],
        )
        version = SkillVersion(
            skill=skill,
            version="1.0.0",
            private_prompt="private",
            output_expectation="风险清单",
        )
        session.add(
            WorkspaceSkillInstall(
                workspace=workspace,
                skill=skill,
                enabled_version=version,
                is_enabled=True,
            )
        )
        await session.commit()

        registry = SkillRegistry(session)
        installed = await registry.list_installed(workspace.id)

    assert len(installed) == 1
    assert installed[0].skill_id == skill.id
    assert installed[0].skill_version_id == version.id
    assert installed[0].private_prompt == "private"
    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_skill_endpoints_create_list_install_and_update() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as seed_session:
        workspace = Workspace(name="admin-space")
        seed_session.add(workspace)
        await seed_session.commit()
        workspace_id = workspace.id

    app = create_app()

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    from app.db.session import get_db_session

    app.dependency_overrides[get_db_session] = override_session

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        headers = {"x-admin-key": "dev-admin-key"}
        create_response = await client.post(
            "/admin/skills",
            headers=headers,
            json={
                "owner_workspace_id": workspace_id,
                "name": "contract-reviewer",
                "description": "审查合同风险",
                "tags": ["合同", "法务"],
                "trigger_examples": ["帮我看合同"],
            },
        )
        assert create_response.status_code == 200
        skill_id = create_response.json()["skill_id"]

        version_response = await client.post(
            f"/admin/skills/{skill_id}/versions",
            headers=headers,
            json={
                "version": "1.0.0",
                "private_prompt": "private",
                "output_expectation": "输出风险清单",
            },
        )
        assert version_response.status_code == 200
        version_id = version_response.json()["skill_version_id"]

        install_response = await client.post(
            f"/admin/workspaces/{workspace_id}/skills/{skill_id}/install",
            headers=headers,
            json={"enabled_version_id": version_id},
        )
        assert install_response.status_code == 200
        assert install_response.json()["enabled_version_id"] == version_id

        update_response = await client.put(
            f"/admin/workspaces/{workspace_id}/skills/{skill_id}",
            headers=headers,
            json={"enabled_version_id": version_id, "is_enabled": False},
        )
        assert update_response.status_code == 200
        assert update_response.json()["is_enabled"] is False

        list_response = await client.get("/admin/skills", headers=headers)
        assert list_response.status_code == 200
        assert list_response.json()["items"][0]["skill_id"] == skill_id

    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_skill_endpoints_reject_invalid_admin_key() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app()

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    from app.db.session import get_db_session

    app.dependency_overrides[get_db_session] = override_session

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/admin/skills", headers={"x-admin-key": "bad-key"})

    assert response.status_code == 403
    assert response.json()["detail"] == "invalid admin key"
    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_skill_install_rejects_invalid_and_duplicate() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as seed_session:
        workspace = Workspace(name="admin-space")
        skill_a = Skill(
            owner_workspace=workspace,
            name="contract-reviewer",
            description="审查合同风险",
            tags=["合同"],
            trigger_examples=["帮我看合同"],
        )
        skill_b = Skill(
            owner_workspace=workspace,
            name="poet",
            description="写诗",
            tags=["诗歌"],
            trigger_examples=["写首诗"],
        )
        version_a = SkillVersion(
            skill=skill_a,
            version="1.0.0",
            private_prompt="private-a",
        )
        version_b = SkillVersion(
            skill=skill_b,
            version="1.0.0",
            private_prompt="private-b",
        )
        seed_session.add_all([workspace, skill_a, skill_b, version_a, version_b])
        await seed_session.commit()
        workspace_id = workspace.id
        skill_a_id = skill_a.id
        version_a_id = version_a.id
        version_b_id = version_b.id

    app = create_app()

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    from app.db.session import get_db_session

    app.dependency_overrides[get_db_session] = override_session

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        headers = {"x-admin-key": "dev-admin-key"}

        mismatch_response = await client.post(
            f"/admin/workspaces/{workspace_id}/skills/{skill_a_id}/install",
            headers=headers,
            json={"enabled_version_id": version_b_id},
        )
        assert mismatch_response.status_code == 400

        install_response = await client.post(
            f"/admin/workspaces/{workspace_id}/skills/{skill_a_id}/install",
            headers=headers,
            json={"enabled_version_id": version_a_id},
        )
        assert install_response.status_code == 200

        duplicate_response = await client.post(
            f"/admin/workspaces/{workspace_id}/skills/{skill_a_id}/install",
            headers=headers,
            json={"enabled_version_id": version_a_id},
        )
        assert duplicate_response.status_code == 409

    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_skill_update_rejects_mismatched_version() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as seed_session:
        workspace = Workspace(name="admin-space")
        skill_a = Skill(
            owner_workspace=workspace,
            name="contract-reviewer",
            description="审查合同风险",
            tags=["合同"],
            trigger_examples=["帮我看合同"],
        )
        skill_b = Skill(
            owner_workspace=workspace,
            name="poet",
            description="写诗",
            tags=["诗歌"],
            trigger_examples=["写首诗"],
        )
        version_a = SkillVersion(
            skill=skill_a,
            version="1.0.0",
            private_prompt="private-a",
        )
        version_b = SkillVersion(
            skill=skill_b,
            version="1.0.0",
            private_prompt="private-b",
        )
        install = WorkspaceSkillInstall(
            workspace=workspace,
            skill=skill_a,
            enabled_version=version_a,
            is_enabled=True,
        )
        seed_session.add_all(
            [workspace, skill_a, skill_b, version_a, version_b, install]
        )
        await seed_session.commit()
        workspace_id = workspace.id
        skill_a_id = skill_a.id
        version_b_id = version_b.id

    app = create_app()

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    from app.db.session import get_db_session

    app.dependency_overrides[get_db_session] = override_session

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.put(
            f"/admin/workspaces/{workspace_id}/skills/{skill_a_id}",
            headers={"x-admin-key": "dev-admin-key"},
            json={"enabled_version_id": version_b_id, "is_enabled": False},
        )

    assert response.status_code == 400
    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_skill_invocations_endpoint_returns_records() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as session:
        workspace = Workspace(name="demo")
        user = User(workspace=workspace, name="User", email="user@example.com")
        conversation = Conversation(workspace=workspace, user=user, title="chat")
        message = Message(conversation=conversation, role="user", content="帮我看合同")
        skill = Skill(
            owner_workspace=workspace,
            name="contract-reviewer",
            description="审查合同风险",
            tags=["合同"],
            trigger_examples=["帮我看合同"],
        )
        version = SkillVersion(
            skill=skill,
            version="1.0.0",
            private_prompt="private",
            output_expectation="风险清单",
        )
        invocation = SkillInvocation(
            workspace=workspace,
            user=user,
            conversation=conversation,
            message=message,
            skill=skill,
            skill_version=version,
            routing_reason="命中 skill tag: 合同",
            routing_confidence=0.75,
        )
        session.add(invocation)
        await session.commit()

    app = create_app()

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    from app.db.session import get_db_session

    app.dependency_overrides[get_db_session] = override_session

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/admin/skill-invocations",
            headers={"x-admin-key": "dev-admin-key"},
        )

    assert response.status_code == 200
    assert response.json()["items"][0]["skill_id"] == skill.id
    assert response.json()["items"][0]["routing_reason"] == "命中 skill tag: 合同"
    await engine.dispose()
