# Web Chat Skill Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first `single_call` Web Chat Prompt Skill Runtime: web chat UI, skill routing, mem0-backed memory retrieval, skill-aware context assembly, SSE streaming, model call, and invocation logging.

**Architecture:** Keep the product control plane in this app: skill registry, routing, memory scope mapping, context assembly, trimming, chat orchestration, and invocation audit. Use mem0 OSS as an in-process memory backend, Qdrant as vector store, and the existing provider direction for single model calls. The first version does not use Claude Agent SDK, LangGraph, WebSocket, script/tool execution, MCP, or a separate mem0 server.

**Tech Stack:** Python >=3.10, FastAPI, SQLAlchemy 2.0 async, Alembic, MySQL 8.4 via asyncmy, mem0ai OSS library, Qdrant from `~/dev-services`, httpx, pydantic-settings, pytest, ruff, pyright, plain HTML/CSS/JS with `fetch + ReadableStream`.

## Global Constraints

- Follow `AGENTS.md`: Python >=3.10, uv, ruff, pyright, pytest.
- Code comments are Chinese and only explain non-obvious WHY.
- All function parameters and return values must have explicit type annotations.
- SQLAlchemy uses 2.0 async style: `select(Model)`, `await session.execute(...)`, `await session.commit()`.
- First version implements only `execution_mode = "single_call"` and `execution_engine = "model_gateway"`.
- Do not implement agent-loop, Claude Agent SDK, LangGraph, script/tool execution, MCP facade, marketplace payment, complete Admin Web UI, WebSocket, resumable stream, mem0 cloud, or separate mem0 server.
- Local development reuses `~/dev-services`: MySQL on `localhost:3306`, Qdrant on `localhost:6333` / `6334`, Redis on `localhost:6379` but Redis is not used in v1.
- Web streaming uses `text/event-stream`; frontend consumes POST response via `fetch + ReadableStream`.
- Keep each module focused; do not add framework abstractions beyond the interfaces named here.

---

## File Structure

Create or modify these files:

- `pyproject.toml`: add `mem0ai`, `qdrant-client`, `aiosqlite` for async DB tests.
- `app/config.py`: add Qdrant, mem0, model gateway, context budget, and dev defaults.
- `app/main.py`: FastAPI app factory, router registration, health endpoint, web page route.
- `app/db/session.py`: async engine/session factory and FastAPI dependency.
- `app/db/models.py`: SQLAlchemy models for workspace, user, conversation, messages, skills, versions, installs, memory events, invocations.
- `app/db/repositories.py`: small repository helpers used by services.
- `alembic.ini`, `alembic/env.py`, `alembic/versions/20260703_0001_web_chat_skill_runtime.py`: schema migration.
- `app/model_gateway/schemas.py`: message, usage, completion result, stream chunk types.
- `app/model_gateway/client.py`: single-call and streaming model gateway.
- `app/skills/schemas.py`: skill manifest and routing result schemas.
- `app/skills/registry.py`: installed skill lookup and private prompt loading.
- `app/skills/router.py`: automatic skill routing.
- `app/skills/admin.py`: Admin API for skill/version/install/invocation read.
- `app/memory/schemas.py`: memory block and event schemas.
- `app/memory/backends.py`: `MemoryBackend`, `Mem0Backend`, `FakeMemoryBackend`.
- `app/memory/service.py`: scope mapping, search, async add, memory event logging.
- `app/context/assembler.py`: build ordered context parts.
- `app/context/trimmer.py`: deterministic budget trimming.
- `app/chat/schemas.py`: chat API request/response/SSE event schemas.
- `app/chat/service.py`: main orchestration.
- `app/chat/router.py`: REST and streaming chat endpoints.
- `app/web/index.html`: minimal Web Chat page.
- `tests/`: focused tests for each component.

---

### Task 1: Dependencies, Settings, and App Factory

**Files:**
- Modify: `pyproject.toml`
- Modify: `app/config.py`
- Create: `app/main.py`
- Test: `tests/test_app_config.py`

**Interfaces:**
- Produces: `Settings` fields used by all later tasks.
- Produces: `create_app() -> FastAPI`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_app_config.py`:

```python
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_settings_have_dev_services_defaults() -> None:
    settings = Settings()

    assert settings.database_url == "mysql+asyncmy://root@localhost:3306/llm_proxy"
    assert settings.test_database_url == "sqlite+aiosqlite:///:memory:"
    assert settings.qdrant_url == "http://localhost:6333"
    assert settings.mem0_backend == "mem0"
    assert settings.context_token_budget == 12000


def test_health_endpoint_returns_ok() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
uv run python -m pytest tests/test_app_config.py -v
```

Expected: failure because `app.main` and new settings do not exist.

- [ ] **Step 3: Add dependencies and settings**

Modify `pyproject.toml` dependencies:

```toml
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "sqlalchemy[asyncio]>=2.0.0",
    "asyncmy>=0.2.9",
    "httpx>=0.27.0",
    "onnxruntime<1.24; python_version < '3.11'",
    "chromadb>=0.5.0",
    "pydantic-settings>=2.5.0",
    "alembic>=1.13.0",
    "python-dotenv>=1.0.0",
    "mem0ai>=0.1.114",
    "qdrant-client>=1.12.0",
]
```

Add to dev dependency group:

```toml
    "aiosqlite>=0.20.0",
```

Modify `app/config.py`:

```python
"""应用配置，所有可变项通过环境变量注入。"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "mysql+asyncmy://root@localhost:3306/llm_proxy"
    test_database_url: str = "sqlite+aiosqlite:///:memory:"

    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "llm_proxy_memories"

    mem0_backend: str = "mem0"

    admin_key: str = "dev-admin-key"

    default_model: str = "deepseek-chat"
    model_api_base_url: str = "https://api.deepseek.com"
    model_api_key: str = ""
    model_timeout_seconds: float = 60.0

    context_token_budget: int = 12000
    history_message_limit: int = 12
    memory_top_k: int = 5

    app_host: str = "127.0.0.1"
    app_port: int = 8000


settings = Settings()
```

Create `app/main.py`:

```python
from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="LLM Proxy Skill Runtime")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
```

- [ ] **Step 4: Run tests**

Run:

```bash
uv run python -m pytest tests/test_app_config.py -v
```

Expected: both tests pass.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml app/config.py app/main.py tests/test_app_config.py
git commit -m "Add skill runtime app configuration"
```

---

### Task 2: Database Models, Session, and Migration

**Files:**
- Create: `app/db/session.py`
- Create: `app/db/models.py`
- Create: `app/db/repositories.py`
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/versions/20260703_0001_web_chat_skill_runtime.py`
- Test: `tests/test_db_models.py`

**Interfaces:**
- Consumes: `settings.database_url`.
- Produces: `get_db_session() -> AsyncIterator[AsyncSession]`.
- Produces models: `Workspace`, `User`, `Conversation`, `Message`, `Skill`, `SkillVersion`, `WorkspaceSkillInstall`, `MemoryEvent`, `SkillInvocation`.

- [ ] **Step 1: Write failing model test**

Create `tests/test_db_models.py`:

```python
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
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run python -m pytest tests/test_db_models.py -v
```

Expected: failure because `app.db.models` does not exist.

- [ ] **Step 3: Add ORM models**

Create `app/db/models.py` with these columns and relationships:

```python
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    users: Mapped[list[User]] = relationship(back_populates="workspace")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    email: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    workspace: Mapped[Workspace] = relationship(back_populates="users")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    workspace: Mapped[Workspace] = relationship()
    user: Mapped[User] = relationship()
    messages: Mapped[list[Message]] = relationship(back_populates="conversation")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
```

Then add the remaining models in the same file:

```python
class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    trigger_examples: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SkillVersion(Base):
    __tablename__ = "skill_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id"), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    private_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    routing_hint: Mapped[str | None] = mapped_column(Text)
    output_expectation: Mapped[str | None] = mapped_column(Text)
    execution_mode: Mapped[str] = mapped_column(String(32), default="single_call")
    execution_engine: Mapped[str] = mapped_column(String(64), default="model_gateway")
    is_published: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class WorkspaceSkillInstall(Base):
    __tablename__ = "workspace_skill_installs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id"), nullable=False)
    enabled_version_id: Mapped[int] = mapped_column(
        ForeignKey("skill_versions.id"), nullable=False
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    installed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MemoryEvent(Base):
    __tablename__ = "memory_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id"))
    skill_id: Mapped[int | None] = mapped_column(ForeignKey("skills.id"))
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    backend: Mapped[str] = mapped_column(String(64), nullable=False)
    backend_memory_id: Mapped[str | None] = mapped_column(String(128))
    memory_preview: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SkillInvocation(Base):
    __tablename__ = "skill_invocations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id"), nullable=False
    )
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), nullable=False)
    skill_id: Mapped[int | None] = mapped_column(ForeignKey("skills.id"))
    skill_version_id: Mapped[int | None] = mapped_column(ForeignKey("skill_versions.id"))
    routing_mode: Mapped[str] = mapped_column(String(32), default="auto")
    routing_reason: Mapped[str | None] = mapped_column(Text)
    routing_confidence: Mapped[float | None] = mapped_column(Float)
    execution_mode: Mapped[str] = mapped_column(String(32), default="single_call")
    execution_engine: Mapped[str] = mapped_column(String(64), default="model_gateway")
    memory_hit_count: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="success")
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
```

- [ ] **Step 4: Add DB session and repositories**

Create `app/db/session.py`:

```python
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
AsyncSessionMaker = async_sessionmaker(engine, expire_on_commit=False)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionMaker() as session:
        yield session
```

Create `app/db/repositories.py` with at least:

```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation, Message, User, Workspace


async def get_or_create_default_workspace(session: AsyncSession) -> Workspace:
    result = await session.execute(select(Workspace).where(Workspace.name == "default"))
    workspace = result.scalar_one_or_none()
    if workspace is not None:
        return workspace

    workspace = Workspace(name="default")
    session.add(workspace)
    await session.flush()
    return workspace


async def get_or_create_default_user(session: AsyncSession, workspace: Workspace) -> User:
    result = await session.execute(
        select(User).where(User.workspace_id == workspace.id, User.email == "local@example.com")
    )
    user = result.scalar_one_or_none()
    if user is not None:
        return user

    user = User(workspace_id=workspace.id, name="Local User", email="local@example.com")
    session.add(user)
    await session.flush()
    return user
```

- [ ] **Step 5: Add Alembic**

Create `alembic.ini`, `alembic/env.py`, and a migration creating all tables from the model list. The migration must use `op.create_table(...)` for each table and `op.drop_table(...)` in reverse order.

- [ ] **Step 6: Run model tests**

```bash
uv run python -m pytest tests/test_db_models.py -v
```

Expected: pass.

- [ ] **Step 7: Run migration against local dev MySQL**

Start shared services if needed:

```bash
cd ~/dev-services
docker compose up -d mysql qdrant
```

Then run:

```bash
uv run alembic upgrade head
```

Expected: migration applies to `llm_proxy`.

- [ ] **Step 8: Commit**

```bash
git add app/db alembic.ini alembic tests/test_db_models.py
git commit -m "Add web chat runtime database schema"
```

---

### Task 3: Model Gateway with Single Call and Streaming

**Files:**
- Create: `app/model_gateway/schemas.py`
- Create: `app/model_gateway/client.py`
- Test: `tests/test_model_gateway.py`

**Interfaces:**
- Produces: `ChatMessage(role: str, content: str)`.
- Produces: `ModelUsage(prompt_tokens: int, completion_tokens: int)`.
- Produces: `ModelGateway.complete(messages: list[ChatMessage], model: str | None = None) -> ModelCompletion`.
- Produces: `ModelGateway.stream(...) -> AsyncIterator[ModelStreamChunk]`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_model_gateway.py`:

```python
import json

import httpx
import pytest

from app.model_gateway.client import ModelGateway
from app.model_gateway.schemas import ChatMessage


@pytest.mark.asyncio
async def test_model_gateway_complete_parses_openai_compatible_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["model"] == "deepseek-chat"
        assert payload["messages"][0]["role"] == "user"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "你好，我在。"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4},
            },
        )

    transport = httpx.MockTransport(handler)
    gateway = ModelGateway(
        api_base_url="https://example.test",
        api_key="test-key",
        default_model="deepseek-chat",
        transport=transport,
    )

    result = await gateway.complete([ChatMessage(role="user", content="你好")])

    assert result.text == "你好，我在。"
    assert result.usage.prompt_tokens == 10
    assert result.usage.completion_tokens == 4


@pytest.mark.asyncio
async def test_model_gateway_stream_yields_delta_and_usage() -> None:
    chunks = [
        'data: {"choices":[{"delta":{"content":"你"}}]}\n\n',
        'data: {"choices":[{"delta":{"content":"好"}}]}\n\n',
        'data: {"usage":{"prompt_tokens":5,"completion_tokens":2},"choices":[{"delta":{}}]}\n\n',
        "data: [DONE]\n\n",
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content="".join(chunks))

    gateway = ModelGateway(
        api_base_url="https://example.test",
        api_key="test-key",
        default_model="deepseek-chat",
        transport=httpx.MockTransport(handler),
    )

    result = [chunk async for chunk in gateway.stream([ChatMessage(role="user", content="hi")])]

    assert [chunk.text for chunk in result if chunk.text] == ["你", "好"]
    assert result[-1].usage is not None
    assert result[-1].usage.completion_tokens == 2
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run python -m pytest tests/test_model_gateway.py -v
```

Expected: failure because gateway files do not exist.

- [ ] **Step 3: Implement schemas**

Create `app/model_gateway/schemas.py`:

```python
from pydantic import BaseModel


class ChatMessage(BaseModel):
    role: str
    content: str


class ModelUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0


class ModelCompletion(BaseModel):
    text: str
    usage: ModelUsage
    model: str


class ModelStreamChunk(BaseModel):
    text: str = ""
    usage: ModelUsage | None = None
```

- [ ] **Step 4: Implement model gateway**

Create `app/model_gateway/client.py`:

```python
from collections.abc import AsyncIterator
import json

import httpx

from app.config import settings
from app.model_gateway.schemas import (
    ChatMessage,
    ModelCompletion,
    ModelStreamChunk,
    ModelUsage,
)


class ModelGateway:
    def __init__(
        self,
        api_base_url: str = settings.model_api_base_url,
        api_key: str = settings.model_api_key,
        default_model: str = settings.default_model,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.api_key = api_key
        self.default_model = default_model
        self.transport = transport

    async def complete(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
    ) -> ModelCompletion:
        payload = {
            "model": model or self.default_model,
            "messages": [message.model_dump() for message in messages],
            "stream": False,
        }
        async with httpx.AsyncClient(transport=self.transport, timeout=settings.model_timeout_seconds) as client:
            response = await client.post(
                f"{self.api_base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            )
            response.raise_for_status()
        data = response.json()
        usage_data = data.get("usage") or {}
        return ModelCompletion(
            text=data["choices"][0]["message"]["content"],
            usage=ModelUsage(
                prompt_tokens=int(usage_data.get("prompt_tokens", 0)),
                completion_tokens=int(usage_data.get("completion_tokens", 0)),
            ),
            model=payload["model"],
        )

    async def stream(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
    ) -> AsyncIterator[ModelStreamChunk]:
        payload = {
            "model": model or self.default_model,
            "messages": [message.model_dump() for message in messages],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        async with httpx.AsyncClient(transport=self.transport, timeout=settings.model_timeout_seconds) as client:
            async with client.stream(
                "POST",
                f"{self.api_base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    raw = line.removeprefix("data: ").strip()
                    if raw == "[DONE]":
                        break
                    data = json.loads(raw)
                    usage_data = data.get("usage")
                    if usage_data:
                        yield ModelStreamChunk(
                            usage=ModelUsage(
                                prompt_tokens=int(usage_data.get("prompt_tokens", 0)),
                                completion_tokens=int(usage_data.get("completion_tokens", 0)),
                            )
                        )
                        continue
                    delta = data["choices"][0].get("delta") or {}
                    text = delta.get("content") or ""
                    if text:
                        yield ModelStreamChunk(text=text)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers
```

- [ ] **Step 5: Run gateway tests**

```bash
uv run python -m pytest tests/test_model_gateway.py -v
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add app/model_gateway tests/test_model_gateway.py
git commit -m "Add single call model gateway"
```

---

### Task 4: Skill Registry, Admin API, and Skill Router

**Files:**
- Create: `app/skills/schemas.py`
- Create: `app/skills/registry.py`
- Create: `app/skills/router.py`
- Create: `app/skills/admin.py`
- Modify: `app/main.py`
- Test: `tests/test_skills.py`

**Interfaces:**
- Consumes: SQLAlchemy models from Task 2.
- Consumes: `ModelGateway.complete` from Task 3.
- Produces: `SkillManifest`, `InstalledSkill`, `SkillRoutingResult`.
- Produces: `SkillRegistry.list_installed(workspace_id: int) -> list[InstalledSkill]`.
- Produces: `SkillRouter.route(message: str, skills: list[InstalledSkill]) -> SkillRoutingResult`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_skills.py`:

```python
from app.skills.router import SkillRouter
from app.skills.schemas import InstalledSkill


async def test_skill_router_returns_no_skill_without_candidates() -> None:
    router = SkillRouter(model_gateway=None)

    result = await router.route("随便聊聊", [])

    assert result.skill_id is None
    assert result.skill_version_id is None
    assert result.reason == "没有可用 skill"
    assert result.confidence == 0.0


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
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run python -m pytest tests/test_skills.py -v
```

Expected: failure because skill modules do not exist.

- [ ] **Step 3: Implement schemas**

Create `app/skills/schemas.py`:

```python
from pydantic import BaseModel


class SkillManifest(BaseModel):
    skill_id: int
    skill_version_id: int
    name: str
    description: str
    tags: list[str]
    trigger_examples: list[str]
    output_expectation: str | None = None


class InstalledSkill(SkillManifest):
    private_prompt: str


class SkillRoutingResult(BaseModel):
    skill_id: int | None
    skill_version_id: int | None
    reason: str
    confidence: float
```

- [ ] **Step 4: Implement keyword-first router**

Create `app/skills/router.py`:

```python
import json

from app.model_gateway.schemas import ChatMessage
from app.model_gateway.client import ModelGateway
from app.skills.schemas import InstalledSkill, SkillRoutingResult


class SkillRouter:
    def __init__(self, model_gateway: ModelGateway | None) -> None:
        self.model_gateway = model_gateway

    async def route(
        self,
        message: str,
        skills: list[InstalledSkill],
    ) -> SkillRoutingResult:
        if not skills:
            return SkillRoutingResult(
                skill_id=None,
                skill_version_id=None,
                reason="没有可用 skill",
                confidence=0.0,
            )

        lowered = message.lower()
        for skill in skills:
            for tag in skill.tags:
                if tag.lower() in lowered:
                    return SkillRoutingResult(
                        skill_id=skill.skill_id,
                        skill_version_id=skill.skill_version_id,
                        reason=f"命中 skill tag: {tag}",
                        confidence=0.75,
                    )

        if self.model_gateway is not None:
            manifest = [
                {
                    "skill_id": skill.skill_id,
                    "skill_version_id": skill.skill_version_id,
                    "name": skill.name,
                    "description": skill.description,
                    "tags": skill.tags,
                    "trigger_examples": skill.trigger_examples,
                }
                for skill in skills
            ]
            prompt = (
                "根据用户消息和可用 skill manifest 选择最合适的 skill。"
                "如果没有明显匹配，返回 no_skill。只输出 JSON。"
                f"\n用户消息：{message}\n可用 skill：{json.dumps(manifest, ensure_ascii=False)}"
            )
            completion = await self.model_gateway.complete(
                [ChatMessage(role="user", content=prompt)]
            )
            try:
                data = json.loads(completion.text)
            except json.JSONDecodeError:
                data = {"skill_id": None, "reason": "routing JSON 解析失败", "confidence": 0.0}

            selected_id = data.get("skill_id")
            for skill in skills:
                if selected_id == skill.skill_id:
                    return SkillRoutingResult(
                        skill_id=skill.skill_id,
                        skill_version_id=skill.skill_version_id,
                        reason=str(data.get("reason", "LLM routing 命中 skill")),
                        confidence=float(data.get("confidence", 0.5)),
                    )

        return SkillRoutingResult(
            skill_id=None,
            skill_version_id=None,
            reason="未命中任何 skill tag",
            confidence=0.0,
        )
```

Keyword routing stays first so tests and local demos remain deterministic; the LLM branch handles cases where tag matching does not trigger.

- [ ] **Step 5: Implement registry and Admin API**

Create `app/skills/registry.py` with `list_installed(session, workspace_id)` using `select(...)` over `WorkspaceSkillInstall`, `Skill`, `SkillVersion`.

Create `app/skills/admin.py` with:

```python
from fastapi import APIRouter, Header, HTTPException

from app.config import settings

router = APIRouter(prefix="/admin", tags=["admin"])


def require_admin_key(x_admin_key: str | None = Header(default=None)) -> None:
    if x_admin_key != settings.admin_key:
        raise HTTPException(status_code=403, detail="invalid admin key")
```

Add endpoints:

- `POST /admin/skills`
- `GET /admin/skills`
- `POST /admin/skills/{skill_id}/versions`
- `POST /admin/workspaces/{workspace_id}/skills/{skill_id}/install`
- `PUT /admin/workspaces/{workspace_id}/skills/{skill_id}`
- `GET /admin/skill-invocations`

Each endpoint should commit DB changes and return JSON with created IDs.

- [ ] **Step 6: Register admin router**

Modify `app/main.py`:

```python
from fastapi import FastAPI

from app.skills.admin import router as skills_admin_router


def create_app() -> FastAPI:
    app = FastAPI(title="LLM Proxy Skill Runtime")
    app.include_router(skills_admin_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
```

- [ ] **Step 7: Run skill tests**

```bash
uv run python -m pytest tests/test_skills.py -v
```

Expected: pass.

- [ ] **Step 8: Commit**

```bash
git add app/skills app/main.py tests/test_skills.py
git commit -m "Add prompt skill registry and router"
```

---

### Task 5: Mem0-backed Memory Service

**Files:**
- Create: `app/memory/schemas.py`
- Create: `app/memory/backends.py`
- Create: `app/memory/service.py`
- Test: `tests/test_memory_service.py`

**Interfaces:**
- Produces: `MemoryBlock(scope: str, content: str, score: float, backend_memory_id: str | None)`.
- Produces: `MemoryBackend.search(query: str, filters: dict[str, object], top_k: int) -> list[MemoryBlock]`.
- Produces: `MemoryService.retrieve(...) -> list[MemoryBlock]`.
- Produces: `MemoryService.add_from_exchange(...) -> None`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_memory_service.py`:

```python
from app.memory.backends import FakeMemoryBackend
from app.memory.service import MemoryService


async def test_memory_service_maps_scope_filters() -> None:
    backend = FakeMemoryBackend(
        results=[
            {
                "memory": "用户喜欢中文回答",
                "score": 0.8,
                "id": "mem_1",
                "metadata": {"scope": "user"},
            }
        ]
    )
    service = MemoryService(backend=backend)

    blocks = await service.retrieve(
        query="继续刚才的项目",
        workspace_id=1,
        user_id=2,
        conversation_id=3,
        top_k=5,
    )

    assert backend.last_filters == {
        "workspace_id": "1",
        "user_id": "2",
        "conversation_id": "3",
    }
    assert blocks[0].content == "用户喜欢中文回答"
    assert blocks[0].scope == "user"
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run python -m pytest tests/test_memory_service.py -v
```

Expected: failure because memory modules do not exist.

- [ ] **Step 3: Implement schemas and fake backend**

Create `app/memory/schemas.py`:

```python
from pydantic import BaseModel


class MemoryBlock(BaseModel):
    scope: str
    content: str
    score: float = 0.0
    backend_memory_id: str | None = None
```

Create `app/memory/backends.py`:

```python
from typing import Protocol

from app.memory.schemas import MemoryBlock


class MemoryBackend(Protocol):
    async def search(
        self,
        query: str,
        filters: dict[str, object],
        top_k: int,
    ) -> list[MemoryBlock]:
        ...

    async def add(
        self,
        messages: list[dict[str, str]],
        user_id: str,
        metadata: dict[str, object],
    ) -> None:
        ...


class FakeMemoryBackend:
    def __init__(self, results: list[dict[str, object]] | None = None) -> None:
        self.results = results or []
        self.last_filters: dict[str, object] = {}

    async def search(
        self,
        query: str,
        filters: dict[str, object],
        top_k: int,
    ) -> list[MemoryBlock]:
        self.last_filters = filters
        blocks: list[MemoryBlock] = []
        for item in self.results[:top_k]:
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            blocks.append(
                MemoryBlock(
                    scope=str(metadata.get("scope", "user")),
                    content=str(item.get("memory", "")),
                    score=float(item.get("score", 0.0)),
                    backend_memory_id=str(item.get("id")) if item.get("id") else None,
                )
            )
        return blocks

    async def add(
        self,
        messages: list[dict[str, str]],
        user_id: str,
        metadata: dict[str, object],
    ) -> None:
        return None
```

- [ ] **Step 4: Implement Mem0 backend**

Append to `app/memory/backends.py`:

```python
from mem0 import Memory


class Mem0Backend:
    def __init__(self, memory: Memory | None = None) -> None:
        self.memory = memory or Memory()

    async def search(
        self,
        query: str,
        filters: dict[str, object],
        top_k: int,
    ) -> list[MemoryBlock]:
        raw = self.memory.search(query=query, filters=filters, top_k=top_k)
        results = raw.get("results", raw) if isinstance(raw, dict) else raw
        blocks: list[MemoryBlock] = []
        for item in results:
            metadata = item.get("metadata") or {}
            blocks.append(
                MemoryBlock(
                    scope=str(metadata.get("scope", "user")),
                    content=str(item.get("memory", "")),
                    score=float(item.get("score", 0.0)),
                    backend_memory_id=str(item.get("id")) if item.get("id") else None,
                )
            )
        return blocks

    async def add(
        self,
        messages: list[dict[str, str]],
        user_id: str,
        metadata: dict[str, object],
    ) -> None:
        self.memory.add(messages, user_id=user_id, metadata=metadata)
```

- [ ] **Step 5: Implement MemoryService**

Create `app/memory/service.py`:

```python
from app.config import settings
from app.memory.backends import FakeMemoryBackend, Mem0Backend, MemoryBackend
from app.memory.schemas import MemoryBlock


class MemoryService:
    def __init__(self, backend: MemoryBackend | None = None) -> None:
        if backend is not None:
            self.backend = backend
        elif settings.mem0_backend == "fake":
            self.backend = FakeMemoryBackend()
        else:
            self.backend = Mem0Backend()

    async def retrieve(
        self,
        query: str,
        workspace_id: int,
        user_id: int,
        conversation_id: int,
        top_k: int,
    ) -> list[MemoryBlock]:
        filters = {
            "workspace_id": str(workspace_id),
            "user_id": str(user_id),
            "conversation_id": str(conversation_id),
        }
        return await self.backend.search(query=query, filters=filters, top_k=top_k)

    async def add_from_exchange(
        self,
        user_message: str,
        assistant_message: str,
        workspace_id: int,
        user_id: int,
        conversation_id: int,
    ) -> None:
        messages = [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": assistant_message},
        ]
        metadata = {
            "workspace_id": str(workspace_id),
            "user_id": str(user_id),
            "conversation_id": str(conversation_id),
            "scope": "conversation",
        }
        await self.backend.add(messages=messages, user_id=str(user_id), metadata=metadata)
```

- [ ] **Step 6: Run memory tests**

```bash
uv run python -m pytest tests/test_memory_service.py -v
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add app/memory tests/test_memory_service.py
git commit -m "Add mem0 memory service"
```

---

### Task 6: Context Assembly and Trimming

**Files:**
- Create: `app/context/assembler.py`
- Create: `app/context/trimmer.py`
- Test: `tests/test_context.py`

**Interfaces:**
- Consumes: `ChatMessage`.
- Consumes: `InstalledSkill | None`.
- Consumes: `MemoryBlock`.
- Produces: `ContextAssembler.assemble(...) -> list[ChatMessage]`.
- Produces: `ContextTrimmer.trim(messages: list[ChatMessage]) -> list[ChatMessage]`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_context.py`:

```python
from app.context.assembler import ContextAssembler
from app.context.trimmer import ContextTrimmer
from app.memory.schemas import MemoryBlock
from app.model_gateway.schemas import ChatMessage
from app.skills.schemas import InstalledSkill


def test_context_assembler_orders_system_skill_memory_history_user() -> None:
    skill = InstalledSkill(
        skill_id=1,
        skill_version_id=2,
        name="contract-reviewer",
        description="审合同",
        tags=["合同"],
        trigger_examples=[],
        private_prompt="你是合同审查专家。",
        output_expectation="输出风险清单。",
    )
    assembler = ContextAssembler(base_prompt="你是 Web Chat Runtime。")

    messages = assembler.assemble(
        skill=skill,
        memories=[MemoryBlock(scope="user", content="用户偏好中文。", score=0.8)],
        history=[ChatMessage(role="assistant", content="上一轮回复")],
        user_message="请审查合同",
    )

    assert messages[0].role == "system"
    assert "你是 Web Chat Runtime。" in messages[0].content
    assert "你是合同审查专家。" in messages[0].content
    assert "<memories>" in messages[0].content
    assert messages[-1] == ChatMessage(role="user", content="请审查合同")


def test_context_trimmer_keeps_system_and_current_user() -> None:
    trimmer = ContextTrimmer(max_chars=30)
    messages = [
        ChatMessage(role="system", content="system prompt"),
        ChatMessage(role="user", content="old message that is very long"),
        ChatMessage(role="assistant", content="old answer that is very long"),
        ChatMessage(role="user", content="current"),
    ]

    trimmed = trimmer.trim(messages)

    assert trimmed[0].role == "system"
    assert trimmed[-1].content == "current"
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run python -m pytest tests/test_context.py -v
```

Expected: failure because context modules do not exist.

- [ ] **Step 3: Implement assembler**

Create `app/context/assembler.py`:

```python
from app.memory.schemas import MemoryBlock
from app.model_gateway.schemas import ChatMessage
from app.skills.schemas import InstalledSkill


class ContextAssembler:
    def __init__(self, base_prompt: str) -> None:
        self.base_prompt = base_prompt

    def assemble(
        self,
        skill: InstalledSkill | None,
        memories: list[MemoryBlock],
        history: list[ChatMessage],
        user_message: str,
    ) -> list[ChatMessage]:
        system_parts = [self.base_prompt]
        if skill is not None:
            system_parts.append(skill.private_prompt)
            if skill.output_expectation:
                system_parts.append(f"输出要求：{skill.output_expectation}")
        if memories:
            memory_lines = "\n".join(f"- [{memory.scope}] {memory.content}" for memory in memories)
            system_parts.append(f"<memories>\n{memory_lines}\n</memories>")

        return [
            ChatMessage(role="system", content="\n\n".join(system_parts)),
            *history,
            ChatMessage(role="user", content=user_message),
        ]
```

- [ ] **Step 4: Implement trimmer**

Create `app/context/trimmer.py`:

```python
from app.model_gateway.schemas import ChatMessage


class ContextTrimmer:
    def __init__(self, max_chars: int) -> None:
        self.max_chars = max_chars

    def trim(self, messages: list[ChatMessage]) -> list[ChatMessage]:
        if len(messages) <= 2:
            return messages

        system = messages[0]
        current_user = messages[-1]
        kept_middle: list[ChatMessage] = []
        total = len(system.content) + len(current_user.content)

        for message in reversed(messages[1:-1]):
            candidate_total = total + len(message.content)
            if candidate_total > self.max_chars:
                continue
            kept_middle.append(message)
            total = candidate_total

        kept_middle.reverse()
        return [system, *kept_middle, current_user]
```

- [ ] **Step 5: Run context tests**

```bash
uv run python -m pytest tests/test_context.py -v
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add app/context tests/test_context.py
git commit -m "Add skill aware context assembly"
```

---

### Task 7: Chat Service, REST API, and SSE Streaming

**Files:**
- Create: `app/chat/schemas.py`
- Create: `app/chat/service.py`
- Create: `app/chat/router.py`
- Modify: `app/main.py`
- Test: `tests/test_chat_service.py`
- Test: `tests/test_chat_router.py`

**Interfaces:**
- Consumes: repositories, `SkillRegistry`, `SkillRouter`, `MemoryService`, `ContextAssembler`, `ContextTrimmer`, `ModelGateway`.
- Produces: `ChatService.send_message(...) -> ChatResponse`.
- Produces: `ChatService.stream_message(...) -> AsyncIterator[ChatStreamEvent]`.
- Produces routes: `/chat/conversations`, `/chat/conversations/{conversation_id}`, `/chat/conversations/{conversation_id}/messages`.

- [ ] **Step 1: Write failing service test**

Create `tests/test_chat_service.py` with fakes:

```python
from app.chat.service import ChatService
from app.memory.backends import FakeMemoryBackend
from app.memory.service import MemoryService
from app.model_gateway.schemas import ModelCompletion, ModelUsage
from app.skills.router import SkillRouter


class FakeModelGateway:
    async def complete(self, messages, model=None):
        return ModelCompletion(
            text="这是回答",
            usage=ModelUsage(prompt_tokens=10, completion_tokens=4),
            model="fake",
        )


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
```

- [ ] **Step 2: Write failing router smoke test**

Create `tests/test_chat_router.py`:

```python
from fastapi.testclient import TestClient

from app.main import create_app


def test_chat_page_route_exists() -> None:
    client = TestClient(create_app())

    response = client.get("/")

    assert response.status_code == 200
    assert "Skill Runtime" in response.text
```

- [ ] **Step 3: Run tests to verify failure**

```bash
uv run python -m pytest tests/test_chat_service.py tests/test_chat_router.py -v
```

Expected: failure because chat modules and `/` route do not exist.

- [ ] **Step 4: Implement chat schemas**

Create `app/chat/schemas.py`:

```python
from pydantic import BaseModel

from app.model_gateway.schemas import ModelUsage


class ChatMessageResponse(BaseModel):
    role: str
    content: str


class RoutingResponse(BaseModel):
    skill_used: bool
    skill_name: str | None = None
    reason: str
    confidence: float


class MemoryResponse(BaseModel):
    hit_count: int


class ChatResponse(BaseModel):
    message: ChatMessageResponse
    routing: RoutingResponse
    memory: MemoryResponse
    usage: ModelUsage


class ChatRequest(BaseModel):
    content: str
    stream: bool = False


class ChatStreamEvent(BaseModel):
    event: str
    data: dict[str, object]
```

- [ ] **Step 5: Implement testable ChatService core**

Create `app/chat/service.py`:

```python
from collections.abc import AsyncIterator

from app.chat.schemas import (
    ChatMessageResponse,
    ChatResponse,
    ChatStreamEvent,
    MemoryResponse,
    RoutingResponse,
)
from app.config import settings
from app.context.assembler import ContextAssembler
from app.context.trimmer import ContextTrimmer
from app.memory.service import MemoryService
from app.model_gateway.client import ModelGateway
from app.model_gateway.schemas import ChatMessage, ModelUsage
from app.skills.router import SkillRouter


class ChatService:
    def __init__(
        self,
        skill_router: SkillRouter | None = None,
        memory_service: MemoryService | None = None,
        model_gateway: ModelGateway | None = None,
    ) -> None:
        self.skill_router = skill_router or SkillRouter(model_gateway=None)
        self.memory_service = memory_service or MemoryService()
        self.model_gateway = model_gateway or ModelGateway()
        self.assembler = ContextAssembler(base_prompt="你是一个支持托管 prompt skill 的 Web Chat Runtime。")
        self.trimmer = ContextTrimmer(max_chars=settings.context_token_budget * 4)

    async def send_message_for_test(self, content: str) -> ChatResponse:
        memories = await self.memory_service.retrieve(
            query=content,
            workspace_id=1,
            user_id=1,
            conversation_id=1,
            top_k=settings.memory_top_k,
        )
        routing = await self.skill_router.route(content, [])
        context = self.assembler.assemble(
            skill=None,
            memories=memories,
            history=[],
            user_message=content,
        )
        trimmed = self.trimmer.trim(context)
        completion = await self.model_gateway.complete(trimmed)
        return ChatResponse(
            message=ChatMessageResponse(role="assistant", content=completion.text),
            routing=RoutingResponse(
                skill_used=False,
                reason=routing.reason,
                confidence=routing.confidence,
            ),
            memory=MemoryResponse(hit_count=len(memories)),
            usage=completion.usage,
        )

    async def stream_for_test(self, content: str) -> AsyncIterator[ChatStreamEvent]:
        yield ChatStreamEvent(event="routing", data={"skill_used": False, "reason": "没有可用 skill", "confidence": 0.0})
        yield ChatStreamEvent(event="memory", data={"hit_count": 0})
        async for chunk in self.model_gateway.stream([ChatMessage(role="user", content=content)]):
            if chunk.text:
                yield ChatStreamEvent(event="delta", data={"text": chunk.text})
            if chunk.usage:
                yield ChatStreamEvent(event="done", data={"invocation_id": 0, "usage": chunk.usage.model_dump()})
                return
        yield ChatStreamEvent(event="done", data={"invocation_id": 0, "usage": ModelUsage().model_dump()})
```

- [ ] **Step 6: Implement chat router and SSE formatting**

Create `app/chat/router.py`:

```python
from collections.abc import AsyncIterator
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.chat.schemas import ChatRequest
from app.chat.service import ChatService

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/conversations/{conversation_id}/messages")
async def send_message(conversation_id: int, request: ChatRequest):
    service = ChatService()
    if request.stream:
        return StreamingResponse(
            _sse(service, request.content),
            media_type="text/event-stream",
        )
    return await service.send_message_for_test(request.content)


async def _sse(service: ChatService, content: str) -> AsyncIterator[str]:
    async for event in service.stream_for_test(content):
        yield f"event: {event.event}\n"
        yield f"data: {json.dumps(event.data, ensure_ascii=False)}\n\n"
```

Modify `app/main.py` to include `chat.router`.

- [ ] **Step 7: Add production DB persistence**

In `ChatService`, add these production methods. The private `_build_response` helper is shared by non-streaming and streaming paths so the service does not duplicate routing, memory, and context assembly logic.

```python
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation, Message, SkillInvocation
from app.db.repositories import get_or_create_default_user, get_or_create_default_workspace


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


async def send_message(
    self,
    session: AsyncSession,
    conversation_id: int,
    content: str,
) -> ChatResponse:
    response, assistant_text, invocation = await self._build_response(
        session=session,
        conversation_id=conversation_id,
        content=content,
    )
    session.add(Message(conversation_id=conversation_id, role="assistant", content=assistant_text))
    invocation.finished_at = datetime.utcnow()
    session.add(invocation)
    await session.commit()
    return response


async def _load_history(
    self,
    session: AsyncSession,
    conversation_id: int,
) -> list[ChatMessage]:
    result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id.desc())
        .limit(settings.history_message_limit)
    )
    rows = list(reversed(result.scalars().all()))
    return [ChatMessage(role=row.role, content=row.content) for row in rows]
```

The production path must:

1. Load default workspace/user.
2. Save user message.
3. Load last `settings.history_message_limit` messages.
4. List installed skills.
5. Route skill.
6. Retrieve memory.
7. Assemble and trim context.
8. Call model gateway.
9. Save assistant message.
10. Add memory asynchronously after completion.
11. Insert `SkillInvocation`.
12. Return response or SSE events.

- [ ] **Step 8: Run chat tests**

```bash
uv run python -m pytest tests/test_chat_service.py tests/test_chat_router.py -v
```

Expected: pass.

- [ ] **Step 9: Commit**

```bash
git add app/chat app/main.py tests/test_chat_service.py tests/test_chat_router.py
git commit -m "Add web chat API and streaming"
```

---

### Task 8: Minimal Web Chat Page

**Files:**
- Create: `app/web/index.html`
- Modify: `app/main.py`
- Test: `tests/test_web_page.py`

**Interfaces:**
- Consumes: `/chat/conversations/{conversation_id}/messages` with `stream=true`.
- Produces: root route `/` returning HTML.

- [ ] **Step 1: Write failing page test**

Create `tests/test_web_page.py`:

```python
from fastapi.testclient import TestClient

from app.main import create_app


def test_root_serves_minimal_web_chat() -> None:
    client = TestClient(create_app())

    response = client.get("/")

    assert response.status_code == 200
    assert "Skill Runtime" in response.text
    assert "ReadableStream" in response.text
    assert "routing" in response.text
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run python -m pytest tests/test_web_page.py -v
```

Expected: failure until the page is added.

- [ ] **Step 3: Add minimal HTML**

Create `app/web/index.html`:

```html
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Skill Runtime</title>
    <style>
      body { margin: 0; font-family: system-ui, sans-serif; background: #f7f7f8; color: #1f2328; }
      .app { display: grid; grid-template-columns: 260px 1fr; min-height: 100vh; }
      aside { border-right: 1px solid #ddd; padding: 16px; background: #fff; }
      main { display: grid; grid-template-rows: 1fr auto; min-width: 0; }
      #messages { padding: 24px; overflow: auto; }
      .msg { max-width: 840px; margin: 0 auto 16px; padding: 12px 14px; border-radius: 8px; background: #fff; border: 1px solid #ddd; }
      .user { background: #eef6ff; }
      .meta { color: #59636e; font-size: 12px; margin-top: 8px; }
      form { display: flex; gap: 8px; padding: 16px; background: #fff; border-top: 1px solid #ddd; }
      textarea { flex: 1; min-height: 48px; resize: vertical; font: inherit; padding: 10px; border: 1px solid #bbb; border-radius: 8px; }
      button { padding: 0 16px; border: 0; border-radius: 8px; background: #1f6feb; color: #fff; font-weight: 600; }
    </style>
  </head>
  <body>
    <div class="app">
      <aside>
        <h1>Skill Runtime</h1>
        <p>Default conversation</p>
      </aside>
      <main>
        <section id="messages"></section>
        <form id="chat-form">
          <textarea id="input" placeholder="输入消息"></textarea>
          <button type="submit">发送</button>
        </form>
      </main>
    </div>
    <script>
      const messages = document.querySelector("#messages");
      const form = document.querySelector("#chat-form");
      const input = document.querySelector("#input");

      function appendMessage(role, text) {
        const el = document.createElement("div");
        el.className = `msg ${role}`;
        el.innerHTML = `<div class="content"></div><div class="meta"></div>`;
        el.querySelector(".content").textContent = text;
        messages.appendChild(el);
        messages.scrollTop = messages.scrollHeight;
        return el;
      }

      function parseSse(buffer, onEvent) {
        const frames = buffer.split("\n\n");
        const rest = frames.pop();
        for (const frame of frames) {
          const event = frame.split("\n").find((line) => line.startsWith("event: "))?.slice(7);
          const data = frame.split("\n").find((line) => line.startsWith("data: "))?.slice(6);
          if (event && data) onEvent(event, JSON.parse(data));
        }
        return rest;
      }

      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const text = input.value.trim();
        if (!text) return;
        input.value = "";
        appendMessage("user", text);
        const assistant = appendMessage("assistant", "");
        const response = await fetch("/chat/conversations/1/messages", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({content: text, stream: true})
        });
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (true) {
          const {done, value} = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, {stream: true});
          buffer = parseSse(buffer, (eventName, data) => {
            if (eventName === "delta") {
              assistant.querySelector(".content").textContent += data.text;
            }
            if (eventName === "routing") {
              assistant.querySelector(".meta").textContent = `routing: ${data.reason}`;
            }
            if (eventName === "memory") {
              assistant.querySelector(".meta").textContent += ` · memory: ${data.hit_count}`;
            }
            if (eventName === "done") {
              assistant.querySelector(".meta").textContent += ` · tokens: ${data.usage.prompt_tokens}/${data.usage.completion_tokens}`;
            }
          });
        }
      });
    </script>
  </body>
</html>
```

- [ ] **Step 4: Serve page**

Modify `app/main.py`:

```python
from pathlib import Path

from fastapi.responses import HTMLResponse

WEB_INDEX = Path(__file__).parent / "web" / "index.html"


@app.get("/", response_class=HTMLResponse)
async def web_chat() -> str:
    return WEB_INDEX.read_text(encoding="utf-8")
```

Keep this route inside `create_app()` or register through a small `app/web/router.py`. Use the style that keeps `app/main.py` readable after current router registration.

- [ ] **Step 5: Run page test**

```bash
uv run python -m pytest tests/test_web_page.py -v
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add app/web app/main.py tests/test_web_page.py
git commit -m "Add minimal web chat page"
```

---

### Task 9: Seed Data, End-to-End Smoke, and Quality Gates

**Files:**
- Create: `app/seed.py`
- Create: `tests/test_seed.py`
- Modify: `README.md` if it exists after implementation; otherwise add notes to `docs/plans/2026-07-03-web-chat-skill-runtime-smoke.md`.

**Interfaces:**
- Produces: `seed_dev_data() -> None`.
- Produces one default workspace, user, skill, skill version, and install for local demo.

- [ ] **Step 1: Write failing seed test**

Create `tests/test_seed.py`:

```python
from app.seed import build_demo_skill_payload


def test_build_demo_skill_payload_contains_contract_skill() -> None:
    payload = build_demo_skill_payload()

    assert payload["name"] == "contract-reviewer"
    assert "合同" in payload["tags"]
    assert "private_prompt" in payload
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run python -m pytest tests/test_seed.py -v
```

Expected: failure because `app.seed` does not exist.

- [ ] **Step 3: Add seed helper**

Create `app/seed.py`:

```python
def build_demo_skill_payload() -> dict[str, object]:
    return {
        "name": "contract-reviewer",
        "description": "审查合同条款风险，输出风险点和修改建议。",
        "tags": ["合同", "风险", "条款"],
        "trigger_examples": ["帮我审查这份合同", "这个合同有什么风险"],
        "private_prompt": "你是合同审查专家。重点检查付款、违约、自动续约、保密、责任限制和管辖条款。",
        "routing_hint": "当用户请求审查合同、条款、协议或法律风险时使用。",
        "output_expectation": "输出风险等级、原文依据和建议改法。",
    }
```

Add an async `seed_dev_data()` that inserts this payload into DB using models from Task 2. It must be idempotent by checking skill name and version before insert.

- [ ] **Step 4: Run seed test**

```bash
uv run python -m pytest tests/test_seed.py -v
```

Expected: pass.

- [ ] **Step 5: Run full quality gates**

Run:

```bash
uv run ruff check . --fix
uv run ruff format .
uv run pyright
uv run python -m pytest
```

Expected: all pass.

- [ ] **Step 6: Local smoke**

Start services:

```bash
cd ~/dev-services
docker compose up -d mysql qdrant
```

Run migrations and app:

```bash
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

Smoke in browser:

- Open `http://127.0.0.1:8000/`.
- Send `帮我审查这份合同有没有风险：付款后不可退款，违约责任由乙方单独承担。`
- Expected page behavior: assistant response streams, metadata shows routing reason and memory count.

- [ ] **Step 7: Commit**

```bash
git add app/seed.py tests/test_seed.py docs/plans/2026-07-03-web-chat-skill-runtime-smoke.md
git commit -m "Add demo seed data and smoke notes"
```

---

## Self-Review

**Spec coverage:**

- Web Chat page: Task 8.
- Chat endpoints: Task 7.
- SSE streaming with `text/event-stream`: Task 7 and Task 8.
- Skill registry and install: Task 4.
- Automatic skill routing: Task 4.
- Private prompt injection: Task 6 and Task 7.
- mem0 OSS library backend: Task 5.
- Context assembly and trimming: Task 6.
- Model single call and streaming: Task 3.
- Invocation logging: Task 2 schema and Task 7 orchestration.
- Local `~/dev-services` MySQL/Qdrant: Task 2 and Task 9.
- No Claude Agent SDK / LangGraph / MCP / WebSocket: Global constraints and no task implements them.

**Placeholder scan:** The plan avoids placeholder markers, open-ended validation instructions, and unspecified task references.

**Type consistency:** Core names are stable across tasks: `ChatMessage`, `ModelUsage`, `ModelGateway`, `InstalledSkill`, `SkillRoutingResult`, `MemoryBlock`, `MemoryService`, `ContextAssembler`, `ContextTrimmer`, `ChatService`, `ChatResponse`, `ChatStreamEvent`.
