from __future__ import annotations

import logging
import re

import mistune

from app.indexing.parsers.base import BaseParser, ParsedChunk

logger = logging.getLogger("[indexing]")


class WikiParser(BaseParser):
    def __init__(
        self,
        source_id: str = "",
        source_url: str = "",
    ) -> None:
        self._source_id = source_id
        self._source_url = source_url

    def supported_extensions(self) -> set[str]:
        return {".md", ".markdown", ".wiki"}

    def parse_file(
        self, file_path: str, source: str | None = None
    ) -> list[ParsedChunk]:
        from pathlib import Path

        path = Path(file_path)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, IOError) as e:
            logger.warning("Cannot read %s: %s", file_path, e)
            return []

        return self.parse_content(
            text,
            page_title=path.stem.replace("-", " ").replace("_", " ").title(),
            file_path=file_path,
            url=self._source_url,
        )

    def parse_content(
        self,
        text: str,
        page_title: str,
        file_path: str,
        url: str = "",
    ) -> list[ParsedChunk]:
        sections = self._split_by_headings(text)
        chunks: list[ParsedChunk] = []

        if not sections:
            if len(text.strip()) >= 50:
                chunks.append(
                    self._make_chunk(
                        page_title=page_title,
                        heading_path=[page_title],
                        content=text.strip(),
                        chunk_type="page",
                        file_path=file_path,
                        url=url,
                        start_line=1,
                        end_line=text.count("\n") + 1,
                    )
                )
            return chunks

        for section in sections:
            content = section["content"].strip()
            if len(content) < 50:
                continue

            level = section["level"]
            if level <= 1:
                chunk_type = "page"
            elif level == 2:
                chunk_type = "section"
            else:
                chunk_type = "subsection"

            chunks.append(
                self._make_chunk(
                    page_title=page_title,
                    heading_path=section["heading_path"],
                    content=content,
                    chunk_type=chunk_type,
                    file_path=file_path,
                    url=url,
                    start_line=section["start_line"],
                    end_line=section["end_line"],
                )
            )

        return chunks

    def _split_by_headings(self, text: str) -> list[dict]:
        lines = text.split("\n")
        sections: list[dict] = []
        current_headings: dict[int, str] = {}
        current_content_lines: list[str] = []
        current_level = 0
        current_start = 1

        for i, line in enumerate(lines, 1):
            heading_match = re.match(r"^(#{1,6})\s+(.+)$", line)
            if heading_match:
                # Save previous section
                if current_content_lines:
                    heading_path = self._build_heading_path(
                        current_headings, current_level
                    )
                    sections.append(
                        {
                            "level": current_level,
                            "heading_path": heading_path,
                            "content": "\n".join(current_content_lines),
                            "start_line": current_start,
                            "end_line": i - 1,
                        }
                    )

                level = len(heading_match.group(1))
                title = heading_match.group(2).strip()
                current_headings[level] = title
                # Clear deeper headings
                for k in list(current_headings.keys()):
                    if k > level:
                        del current_headings[k]
                current_level = level
                current_content_lines = []
                current_start = i
            else:
                current_content_lines.append(line)

        # Final section
        if current_content_lines:
            heading_path = self._build_heading_path(current_headings, current_level)
            sections.append(
                {
                    "level": current_level,
                    "heading_path": heading_path,
                    "content": "\n".join(current_content_lines),
                    "start_line": current_start,
                    "end_line": len(lines),
                }
            )

        return sections

    def _build_heading_path(
        self, headings: dict[int, str], current_level: int
    ) -> list[str]:
        path = []
        for level in sorted(headings.keys()):
            if level <= current_level:
                path.append(headings[level])
        return path if path else ["(untitled)"]

    def _make_chunk(
        self,
        page_title: str,
        heading_path: list[str],
        content: str,
        chunk_type: str,
        file_path: str,
        url: str,
        start_line: int,
        end_line: int,
    ) -> ParsedChunk:
        qualified_name = " > ".join(heading_path)
        content_with_context = (
            f"# {page_title}\n"
            f"> Path: {qualified_name}\n"
            f"> Source: {url}\n\n"
            f"{content}"
        )

        return ParsedChunk(
            source_id=self._source_id,
            source_type="wiki",
            file_path=file_path,
            language="markdown",
            chunk_type=chunk_type,
            name=heading_path[-1] if heading_path else page_title,
            qualified_name=qualified_name,
            content=content,
            content_with_context=content_with_context,
            start_line=start_line,
            end_line=end_line,
            metadata={"page_title": page_title, "url": url},
        )
