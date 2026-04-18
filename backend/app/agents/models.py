from __future__ import annotations

from pydantic import BaseModel


class RetrievedChunk(BaseModel):
    id: str
    source_id: str
    source_type: str
    file_path: str
    repo_root: str
    language: str
    chunk_type: str
    name: str
    qualified_name: str
    content: str
    content_with_context: str | None
    start_line: int
    end_line: int
    summary: str | None
    purpose: str | None
    signature: str | None
    reuse_signal: str | None
    domain_tags: list[str]
    complexity: str | None
    imports_used: list[str]
    metadata: dict
    score: float = 0.0
    source_name: str = ""


class RAGResult(BaseModel):
    chunks: list[RetrievedChunk]
    scope_confidence: float
    total_searched: int


class FaithfulnessResult(BaseModel):
    passed: bool
    reason: str
    confidence: float


class FollowupQuestionModel(BaseModel):
    question: str
    category: str  # dig_deeper | adjacent_concern | architecture


class FollowupResult(BaseModel):
    questions: list[FollowupQuestionModel]
