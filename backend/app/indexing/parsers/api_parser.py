"""Parser for remote API responses — extracts items into ParsedChunks.

Supports JSON (with jmespath data_path), XML (with XPath), and plain text.
"""
from __future__ import annotations

import hashlib
import logging
import xml.etree.ElementTree as ET
from typing import Any

import jmespath

from app.indexing.parsers.base import ParsedChunk

logger = logging.getLogger("[api-parser]")


class ApiResponseParser:
    def __init__(
        self,
        source_id: str,
        source_name: str,
        config: dict[str, Any],
    ) -> None:
        self._source_id = source_id
        self._source_name = source_name
        self._data_path: str = config.get("data_path", "")
        self._content_fields: list[str] = config.get("content_fields", [])
        self._name_field: str = config.get("name_field", "")
        self._id_field: str = config.get("id_field", "")
        self._response_format: str = config.get("response_format", "json")

    def parse_responses(self, payloads: list[Any]) -> list[ParsedChunk]:
        """Parse all fetched response payloads into chunks."""
        if self._response_format == "json":
            return self._parse_json(payloads)
        if self._response_format == "xml":
            return self._parse_xml(payloads)
        return self._parse_text(payloads)

    # ── JSON ─────────────────────────────────────────────────────────

    def _parse_json(self, payloads: list[Any]) -> list[ParsedChunk]:
        chunks: list[ParsedChunk] = []
        global_idx = 0

        for payload in payloads:
            items = self._extract_items(payload)
            for item in items:
                chunk = self._item_to_chunk(item, global_idx)
                if chunk:
                    chunks.append(chunk)
                global_idx += 1

        logger.info(
            "Parsed %d chunks from %d API response pages",
            len(chunks), len(payloads),
        )
        return chunks

    def _extract_items(self, payload: Any) -> list[Any]:
        """Use jmespath data_path to pull items from a response."""
        if self._data_path and isinstance(payload, dict):
            result = jmespath.search(self._data_path, payload)
            if isinstance(result, list):
                return result
            if result is not None:
                return [result]  # single object match
            return []
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            return [payload]
        return []

    def _item_to_chunk(self, item: Any, idx: int) -> ParsedChunk | None:
        if isinstance(item, dict):
            return self._dict_to_chunk(item, idx)
        if isinstance(item, str):
            return self._text_to_chunk(item, idx)
        # Try converting to string
        text = str(item)
        if text:
            return self._text_to_chunk(text, idx)
        return None

    def _dict_to_chunk(self, item: dict, idx: int) -> ParsedChunk:
        # Build content from content_fields or fallback to full JSON
        if self._content_fields:
            parts = []
            for field in self._content_fields:
                val = _nested_get(item, field)
                if val is not None:
                    parts.append(f"{field}: {val}")
            content = "\n".join(parts) if parts else _json_str(item)
        else:
            content = _json_str(item)

        # Chunk name
        name = ""
        if self._name_field:
            name = str(_nested_get(item, self._name_field) or "")
        if not name:
            name = f"item-{idx}"

        # Item ID for dedup and file_path uniqueness
        item_id = ""
        if self._id_field:
            item_id = str(_nested_get(item, self._id_field) or "")
        if not item_id:
            item_id = hashlib.md5(content.encode()).hexdigest()[:12]

        file_path = f"api/{self._source_name}/{item_id}"

        return ParsedChunk(
            source_id=self._source_id,
            source_type="api",
            file_path=file_path,
            language="text",
            chunk_type="api_record",
            name=name,
            qualified_name=f"{self._source_name}/{name}",
            content=content,
            content_with_context=content,
            start_line=0,
            end_line=0,
            metadata={"item_id": item_id, "source": "api"},
        )

    def _text_to_chunk(self, text: str, idx: int) -> ParsedChunk:
        item_id = hashlib.md5(text.encode()).hexdigest()[:12]
        return ParsedChunk(
            source_id=self._source_id,
            source_type="api",
            file_path=f"api/{self._source_name}/{item_id}",
            language="text",
            chunk_type="api_record",
            name=f"item-{idx}",
            qualified_name=f"{self._source_name}/item-{idx}",
            content=text,
            content_with_context=text,
            start_line=0,
            end_line=0,
            metadata={"item_id": item_id, "source": "api"},
        )

    # ── XML ──────────────────────────────────────────────────────────

    def _parse_xml(self, payloads: list[Any]) -> list[ParsedChunk]:
        chunks: list[ParsedChunk] = []
        idx = 0
        for raw in payloads:
            if not isinstance(raw, str):
                continue
            try:
                root = ET.fromstring(raw)
            except ET.ParseError as e:
                logger.warning("XML parse error: %s", e)
                continue

            if self._data_path:
                elements = root.findall(self._data_path)
            else:
                elements = list(root)

            for elem in elements:
                text = ET.tostring(elem, encoding="unicode", method="text").strip()
                if not text:
                    text = ET.tostring(elem, encoding="unicode")
                name = elem.tag
                if self._name_field:
                    name_el = elem.find(self._name_field)
                    if name_el is not None and name_el.text:
                        name = name_el.text

                item_id = hashlib.md5(text.encode()).hexdigest()[:12]
                chunks.append(ParsedChunk(
                    source_id=self._source_id,
                    source_type="api",
                    file_path=f"api/{self._source_name}/{item_id}",
                    language="xml",
                    chunk_type="api_record",
                    name=name,
                    qualified_name=f"{self._source_name}/{name}",
                    content=text,
                    content_with_context=text,
                    start_line=0,
                    end_line=0,
                    metadata={"item_id": item_id, "source": "api"},
                ))
                idx += 1

        logger.info("Parsed %d XML chunks from %d pages", len(chunks), len(payloads))
        return chunks

    # ── Plain text ───────────────────────────────────────────────────

    def _parse_text(self, payloads: list[Any]) -> list[ParsedChunk]:
        chunks: list[ParsedChunk] = []
        for i, raw in enumerate(payloads):
            text = str(raw).strip()
            if not text:
                continue
            item_id = hashlib.md5(text.encode()).hexdigest()[:12]
            chunks.append(ParsedChunk(
                source_id=self._source_id,
                source_type="api",
                file_path=f"api/{self._source_name}/{item_id}",
                language="text",
                chunk_type="api_record",
                name=f"page-{i}",
                qualified_name=f"{self._source_name}/page-{i}",
                content=text,
                content_with_context=text,
                start_line=0,
                end_line=0,
                metadata={"item_id": item_id, "source": "api"},
            ))
        return chunks


# ── helpers ──────────────────────────────────────────────────────────

def _nested_get(d: dict, path: str) -> Any:
    """Dot-notation field access: ``_nested_get(d, 'a.b.c')``."""
    keys = path.split(".")
    current: Any = d
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return None
    return current


def _json_str(obj: Any) -> str:
    """Pretty-print a JSON-serialisable object."""
    try:
        return __import__("json").dumps(obj, indent=2, ensure_ascii=False, default=str)
    except Exception:
        return str(obj)
