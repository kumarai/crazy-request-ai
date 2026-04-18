from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ParsedChunk:
    source_id: str
    source_type: str
    file_path: str
    language: str
    chunk_type: str
    name: str
    qualified_name: str
    content: str
    content_with_context: str
    start_line: int
    end_line: int
    metadata: dict = field(default_factory=dict)
    summary: str | None = None
    purpose: str | None = None
    signature: str | None = None
    reuse_signal: str | None = None
    domain_tags: list[str] = field(default_factory=list)
    complexity: str | None = None
    imports_used: list[str] = field(default_factory=list)
    embedding: list[float] | None = None
    summary_embedding: list[float] | None = None


class BaseParser(ABC):
    @abstractmethod
    def parse_file(
        self, file_path: str, source: str | None = None
    ) -> list[ParsedChunk]:
        ...

    @abstractmethod
    def supported_extensions(self) -> set[str]:
        ...
