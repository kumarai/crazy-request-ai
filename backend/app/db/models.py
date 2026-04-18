from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.config import settings


class Base(DeclarativeBase):
    pass


# ------------------------------------------------------------------
# Credentials — encrypted at rest
# ------------------------------------------------------------------
class Credential(Base):
    __tablename__ = "credentials"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    credential_type: Mapped[str] = mapped_column(
        Text, nullable=False, default="token"
    )  # "token" | "ssh"
    encrypted_value: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def to_dict(self, include_value: bool = False) -> dict:
        d = {
            "id": self.id,
            "name": self.name,
            "credential_type": self.credential_type,
            "description": self.description,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if include_value:
            d["encrypted_value"] = self.encrypted_value
        return d


# ------------------------------------------------------------------
# Sources
# ------------------------------------------------------------------
class Source(Base):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    credential_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credentials.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    credential: Mapped[Credential | None] = relationship()
    chunks: Mapped[list[CodeChunk]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )
    dependencies: Mapped[list[CodeDependency]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )
    index_checkpoint: Mapped[IndexCheckpoint | None] = relationship(
        back_populates="source", cascade="all, delete-orphan", uselist=False
    )
    wiki_checkpoints: Mapped[list[WikiCheckpoint]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )
    jobs: Mapped[list[IndexJob]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


# ------------------------------------------------------------------
# Code chunks
# ------------------------------------------------------------------
class CodeChunk(Base):
    __tablename__ = "code_chunks"
    __table_args__ = (
        UniqueConstraint("source_id", "file_path", "name", "start_line", name="uq_chunk_source_file_name_line"),
        Index("idx_chunks_source_id", "source_id"),
        Index("idx_chunks_file_path", "file_path"),
        Index("idx_chunks_name", "name"),
        Index("idx_chunks_source_type", "source_type", "language", "chunk_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE")
    )
    source_type: Mapped[str] = mapped_column(Text, nullable=False, default="code")
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    repo_root: Mapped[str] = mapped_column(Text, nullable=False, default="")
    language: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_type: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    qualified_name: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_with_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_line: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    reuse_signal: Mapped[str | None] = mapped_column(Text, nullable=True)
    domain_tags: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text), nullable=True
    )
    complexity: Mapped[str | None] = mapped_column(Text, nullable=True)
    imports_used: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text), nullable=True
    )
    # The exact text that was embedded (stored for debugging + re-embed flows).
    embed_input: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Extra summarizer fields used in embed_input.
    side_effects: Mapped[str | None] = mapped_column(Text, nullable=True)
    example_call: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding = mapped_column(
        Vector(settings.llm_embedding_dimensions), nullable=True
    )
    summary_embedding = mapped_column(
        Vector(settings.llm_embedding_dimensions), nullable=True
    )
    metadata_: Mapped[dict | None] = mapped_column(
        "metadata", JSONB, default=dict
    )
    commit_sha: Mapped[str | None] = mapped_column(Text, nullable=True)
    wiki_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    indexed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    source: Mapped[Source | None] = relationship(back_populates="chunks")

    _EXCLUDE_FROM_DICT = frozenset({"metadata", "embedding", "summary_embedding"})

    def to_dict(self) -> dict:
        return {
            c.key: getattr(self, c.key)
            for c in self.__table__.columns
            if c.key not in self._EXCLUDE_FROM_DICT
        } | {"metadata": self.metadata_}


# ------------------------------------------------------------------
# Code dependencies
# ------------------------------------------------------------------
class CodeDependency(Base):
    __tablename__ = "code_dependencies"
    __table_args__ = (
        UniqueConstraint("source_id", "from_file", "to_file", name="uq_dep_source_from_to"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE")
    )
    from_file: Mapped[str] = mapped_column(Text, nullable=False)
    to_file: Mapped[str] = mapped_column(Text, nullable=False)
    import_names: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text), default=list
    )
    dep_type: Mapped[str | None] = mapped_column(Text, default="import")

    source: Mapped[Source | None] = relationship(back_populates="dependencies")


# ------------------------------------------------------------------
# Checkpoints
# ------------------------------------------------------------------
class IndexCheckpoint(Base):
    __tablename__ = "index_checkpoint"

    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        primary_key=True,
    )
    last_commit_sha: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_indexed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    source: Mapped[Source] = relationship(back_populates="index_checkpoint")


class WikiCheckpoint(Base):
    __tablename__ = "wiki_checkpoint"
    __table_args__ = ({"extend_existing": True},)

    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        primary_key=True,
    )
    page_slug: Mapped[str] = mapped_column(Text, primary_key=True)
    gitlab_page_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    indexed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    source: Mapped[Source] = relationship(back_populates="wiki_checkpoints")


# ------------------------------------------------------------------
# Index jobs
# ------------------------------------------------------------------
class IndexJob(Base):
    __tablename__ = "index_jobs"
    __table_args__ = (
        Index("idx_jobs_source_id", "source_id", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE")
    )
    celery_task_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str | None] = mapped_column(Text, default="pending")
    triggered_by: Mapped[str | None] = mapped_column(Text, default="schedule")
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    stats: Mapped[dict | None] = mapped_column(JSONB, default=dict)

    source: Mapped[Source | None] = relationship(back_populates="jobs")

    def to_dict(self) -> dict:
        return {c.key: getattr(self, c.key) for c in self.__table__.columns}


# ------------------------------------------------------------------
# Customer-support conversations
# ------------------------------------------------------------------
class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        Index("idx_conversations_customer_id", "customer_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    customer_id: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False, default="web")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_specialist: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_handoff_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    rolling_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    unresolved_facts_json: Mapped[list | None] = mapped_column(
        JSONB, nullable=True, server_default="[]"
    )
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    pending_intent_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    tool_calls: Mapped[list[ToolCallRecord]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )

    def to_dict(self) -> dict:
        return {c.key: getattr(self, c.key) for c in self.__table__.columns}


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("idx_messages_conversation_id", "conversation_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)  # user | assistant | system | action
    content: Mapped[str] = mapped_column(Text, nullable=False)
    specialist_used: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    citations_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # LLM usage + USD cost for assistant messages. Null on user/action rows
    # and on any assistant row where the provider didn't report usage.
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 6), nullable=True)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    tool_calls: Mapped[list[ToolCallRecord]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )

    def to_dict(self) -> dict:
        return {c.key: getattr(self, c.key) for c in self.__table__.columns}


class ToolCallRecord(Base):
    __tablename__ = "tool_calls"
    __table_args__ = (
        Index("idx_tool_calls_conversation_id", "conversation_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    tool_name: Mapped[str] = mapped_column(Text, nullable=False)
    input_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    output_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    conversation: Mapped[Conversation] = relationship(back_populates="tool_calls")
    message: Mapped[Message | None] = relationship(back_populates="tool_calls")

    def to_dict(self) -> dict:
        return {c.key: getattr(self, c.key) for c in self.__table__.columns}
