from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    """返回带 UTC 时区信息的当前时间。"""

    return datetime.now(timezone.utc)


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    users: Mapped[list[User]] = relationship(back_populates="workspace")
    conversations: Mapped[list[Conversation]] = relationship(back_populates="workspace")
    owned_skills: Mapped[list[Skill]] = relationship(back_populates="owner_workspace")
    installed_skills: Mapped[list[WorkspaceSkillInstall]] = relationship(
        back_populates="workspace"
    )
    memory_events: Mapped[list[MemoryEvent]] = relationship(back_populates="workspace")
    skill_invocations: Mapped[list[SkillInvocation]] = relationship(
        back_populates="workspace"
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    email: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    workspace: Mapped[Workspace] = relationship(back_populates="users")
    conversations: Mapped[list[Conversation]] = relationship(back_populates="user")
    memory_events: Mapped[list[MemoryEvent]] = relationship(back_populates="user")
    skill_invocations: Mapped[list[SkillInvocation]] = relationship(
        back_populates="user"
    )


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow,
        onupdate=utcnow,
    )

    workspace: Mapped[Workspace] = relationship(back_populates="conversations")
    user: Mapped[User] = relationship(back_populates="conversations")
    messages: Mapped[list[Message]] = relationship(back_populates="conversation")
    memory_events: Mapped[list[MemoryEvent]] = relationship(
        back_populates="conversation"
    )
    skill_invocations: Mapped[list[SkillInvocation]] = relationship(
        back_populates="conversation"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    skill_invocations: Mapped[list[SkillInvocation]] = relationship(
        back_populates="message"
    )


class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    trigger_examples: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow,
        onupdate=utcnow,
    )

    owner_workspace: Mapped[Workspace] = relationship(back_populates="owned_skills")
    versions: Mapped[list[SkillVersion]] = relationship(back_populates="skill")
    installs: Mapped[list[WorkspaceSkillInstall]] = relationship(back_populates="skill")
    memory_events: Mapped[list[MemoryEvent]] = relationship(back_populates="skill")
    skill_invocations: Mapped[list[SkillInvocation]] = relationship(
        back_populates="skill"
    )


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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    skill: Mapped[Skill] = relationship(back_populates="versions")
    installs: Mapped[list[WorkspaceSkillInstall]] = relationship(
        back_populates="enabled_version"
    )
    skill_invocations: Mapped[list[SkillInvocation]] = relationship(
        back_populates="skill_version"
    )


class WorkspaceSkillInstall(Base):
    __tablename__ = "workspace_skill_installs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id"), nullable=False
    )
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id"), nullable=False)
    enabled_version_id: Mapped[int] = mapped_column(
        ForeignKey("skill_versions.id"),
        nullable=False,
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    installed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    workspace: Mapped[Workspace] = relationship(back_populates="installed_skills")
    skill: Mapped[Skill] = relationship(back_populates="installs")
    enabled_version: Mapped[SkillVersion] = relationship(back_populates="installs")


class MemoryEvent(Base):
    __tablename__ = "memory_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id"))
    skill_id: Mapped[int | None] = mapped_column(ForeignKey("skills.id"))
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    backend: Mapped[str] = mapped_column(String(64), nullable=False)
    backend_memory_id: Mapped[str | None] = mapped_column(String(128))
    memory_preview: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    workspace: Mapped[Workspace] = relationship(back_populates="memory_events")
    user: Mapped[User] = relationship(back_populates="memory_events")
    conversation: Mapped[Conversation | None] = relationship(
        back_populates="memory_events"
    )
    skill: Mapped[Skill | None] = relationship(back_populates="memory_events")


class SkillInvocation(Base):
    __tablename__ = "skill_invocations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id"),
        nullable=False,
    )
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), nullable=False)
    skill_id: Mapped[int | None] = mapped_column(ForeignKey("skills.id"))
    skill_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("skill_versions.id")
    )
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
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)

    workspace: Mapped[Workspace] = relationship(back_populates="skill_invocations")
    user: Mapped[User] = relationship(back_populates="skill_invocations")
    conversation: Mapped[Conversation] = relationship(
        back_populates="skill_invocations"
    )
    message: Mapped[Message] = relationship(back_populates="skill_invocations")
    skill: Mapped[Skill | None] = relationship(back_populates="skill_invocations")
    skill_version: Mapped[SkillVersion | None] = relationship(
        back_populates="skill_invocations"
    )
