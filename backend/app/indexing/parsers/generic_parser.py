from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from app.indexing.parsers.base import BaseParser, ParsedChunk

logger = logging.getLogger("[indexing]")


class GenericParser(BaseParser):
    def __init__(self, source_id: str = "", source_name: str = "") -> None:
        self._source_id = source_id
        self._source_name = source_name

    def supported_extensions(self) -> set[str]:
        return {".json", ".txt", ".md", ".yaml", ".yml"}

    def parse_file(
        self, file_path: str, source: str | None = None
    ) -> list[ParsedChunk]:
        path = Path(file_path)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, IOError) as e:
            logger.warning("Cannot read %s: %s", file_path, e)
            return []

        ext = path.suffix.lower()

        if ext == ".json":
            return self._parse_json(text, file_path)
        elif ext in (".md", ".txt"):
            return self._parse_text(text, file_path)
        else:
            return self._parse_text(text, file_path)

    def _parse_json(self, text: str, file_path: str) -> list[ParsedChunk]:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning("Invalid JSON in %s: %s", file_path, e)
            return []

        # OpenAPI spec detection
        if isinstance(data, dict) and ("openapi" in data or "swagger" in data):
            return self._parse_openapi(data, file_path)

        chunks: list[ParsedChunk] = []

        if isinstance(data, list):
            for i, item in enumerate(data):
                name = self._first_string_field(item) if isinstance(item, dict) else f"item_{i}"
                content = json.dumps(item, indent=2)
                chunks.append(
                    self._make_chunk(
                        name=name,
                        qualified_name=f"{self._source_name} > {name}",
                        content=content,
                        chunk_type="record",
                        file_path=file_path,
                    )
                )
        elif isinstance(data, dict):
            for key, value in data.items():
                content = json.dumps(value, indent=2)
                chunks.append(
                    self._make_chunk(
                        name=key,
                        qualified_name=f"{self._source_name} > {key}",
                        content=content,
                        chunk_type="record",
                        file_path=file_path,
                    )
                )

        return chunks

    def _parse_openapi(self, data: dict, file_path: str) -> list[ParsedChunk]:
        chunks: list[ParsedChunk] = []
        paths = data.get("paths", {})

        for path_str, methods in paths.items():
            if not isinstance(methods, dict):
                continue
            for method, spec in methods.items():
                if method.startswith("x-") or not isinstance(spec, dict):
                    continue
                name = f"{method.upper()} {path_str}"
                summary = spec.get("summary", "")
                description = spec.get("description", "")
                content = json.dumps(spec, indent=2)

                chunks.append(
                    self._make_chunk(
                        name=name,
                        qualified_name=f"{self._source_name} > {name}",
                        content=f"{summary}\n{description}\n\n{content}".strip(),
                        chunk_type="endpoint",
                        file_path=file_path,
                    )
                )

        return chunks

    def _parse_text(self, text: str, file_path: str) -> list[ParsedChunk]:
        # Split at headings
        sections = re.split(r"(?m)^(#{1,3}\s+.+)$", text)
        chunks: list[ParsedChunk] = []

        if len(sections) <= 1:
            # No headings — single article chunk
            if len(text.strip()) >= 50:
                chunks.append(
                    self._make_chunk(
                        name=Path(file_path).stem,
                        qualified_name=f"{self._source_name} > {Path(file_path).stem}",
                        content=text.strip(),
                        chunk_type="article",
                        file_path=file_path,
                    )
                )
            return chunks

        current_heading = Path(file_path).stem
        current_content_parts: list[str] = []

        for part in sections:
            heading_match = re.match(r"^#{1,3}\s+(.+)$", part)
            if heading_match:
                # Flush previous
                if current_content_parts:
                    content = "\n".join(current_content_parts).strip()
                    if len(content) >= 50:
                        chunks.append(
                            self._make_chunk(
                                name=current_heading,
                                qualified_name=f"{self._source_name} > {current_heading}",
                                content=content,
                                chunk_type="section",
                                file_path=file_path,
                            )
                        )
                current_heading = heading_match.group(1).strip()
                current_content_parts = []
            else:
                current_content_parts.append(part)

        # Final flush
        if current_content_parts:
            content = "\n".join(current_content_parts).strip()
            if len(content) >= 50:
                chunks.append(
                    self._make_chunk(
                        name=current_heading,
                        qualified_name=f"{self._source_name} > {current_heading}",
                        content=content,
                        chunk_type="section",
                        file_path=file_path,
                    )
                )

        return chunks

    def _first_string_field(self, obj: dict) -> str:
        for v in obj.values():
            if isinstance(v, str) and v.strip():
                return v.strip()[:100]
        return "unnamed"

    def _make_chunk(
        self,
        name: str,
        qualified_name: str,
        content: str,
        chunk_type: str,
        file_path: str,
    ) -> ParsedChunk:
        content_with_context = (
            f"# {self._source_name}\n> {qualified_name}\n\n{content}"
        )
        return ParsedChunk(
            source_id=self._source_id,
            source_type="generic",
            file_path=file_path,
            language="text",
            chunk_type=chunk_type,
            name=name,
            qualified_name=qualified_name,
            content=content,
            content_with_context=content_with_context,
            start_line=0,
            end_line=0,
            metadata={"source_name": self._source_name},
        )
