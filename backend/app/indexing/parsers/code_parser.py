from __future__ import annotations

import logging
import re
from pathlib import Path

import tree_sitter_python as tspython
import tree_sitter_typescript as tstypescript
from tree_sitter import Language, Parser

from app.indexing.parsers.base import BaseParser, ParsedChunk

logger = logging.getLogger("[indexing]")

PY_LANGUAGE = Language(tspython.language())
TS_LANGUAGE = Language(tstypescript.language_typescript())
TSX_LANGUAGE = Language(tstypescript.language_tsx())

# Node types to extract per language
_PY_CHUNK_MAP = {
    "class_definition": "class",
    "function_definition": "function",
}

_TS_CHUNK_MAP = {
    "class_declaration": "class",
    "abstract_class_declaration": "class",
    "function_declaration": "function",
    "interface_declaration": "interface",
    "type_alias_declaration": "type",
    "enum_declaration": "enum",
}


def _get_complexity(line_count: int) -> str:
    if line_count <= 20:
        return "simple"
    if line_count <= 60:
        return "moderate"
    return "complex"


def _extract_name(node) -> str:
    for child in node.children:
        if child.type in ("identifier", "property_identifier", "type_identifier"):
            return child.text.decode("utf-8")
    return "<anonymous>"


def _extract_decorators(node) -> list[str]:
    decorators = []
    # Walk backwards through all preceding decorator siblings (Python stacked decorators)
    sibling = node.prev_named_sibling
    preceding = []
    while sibling and sibling.type == "decorator":
        preceding.append(sibling.text.decode("utf-8"))
        sibling = sibling.prev_named_sibling
    decorators.extend(reversed(preceding))
    # Also collect decorators that are direct children (TS decorators)
    for child in node.children:
        if child.type == "decorator":
            decorators.append(child.text.decode("utf-8"))
    return decorators


def _extract_bases(node) -> list[str]:
    bases = []
    for child in node.children:
        if child.type == "argument_list":
            for arg in child.children:
                if arg.type not in ("(", ")", ","):
                    bases.append(arg.text.decode("utf-8"))
        if child.type == "class_heritage":
            bases.append(child.text.decode("utf-8"))
    return bases


def _build_imports_map(tree, source_bytes: bytes) -> dict[str, str]:
    imports: dict[str, str] = {}
    root = tree.root_node
    for child in root.children:
        text = child.text.decode("utf-8")
        if child.type == "import_statement":
            # Python: import X
            parts = text.replace("import ", "").split(",")
            for p in parts:
                name = p.strip().split(" as ")[-1].strip().split(".")[-1]
                imports[name] = text
        elif child.type == "import_from_statement":
            # Python: from X import Y, Z
            match = re.findall(r"import\s+(.+)", text)
            if match:
                names = match[0].split(",")
                for n in names:
                    name = n.strip().split(" as ")[-1].strip()
                    imports[name] = text
        elif child.type == "import_statement" or (
            child.type == "lexical_declaration"
            and "require(" in text
        ):
            # TS require
            imports[_extract_name(child)] = text
        elif child.type in (
            "import_statement",
            "import_declaration",
        ):
            # TS import
            for sub in child.children:
                if sub.type == "import_clause":
                    for spec in sub.children:
                        if spec.type == "named_imports":
                            for item in spec.children:
                                if item.type == "import_specifier":
                                    name = _extract_name(item)
                                    imports[name] = text
                        elif spec.type in ("identifier", "type_identifier"):
                            imports[spec.text.decode("utf-8")] = text
    return imports


def _relevant_imports(content: str, imports_map: dict[str, str]) -> str:
    used = []
    seen = set()
    for symbol, stmt in imports_map.items():
        if re.search(rf"\b{re.escape(symbol)}\b", content) and stmt not in seen:
            used.append(stmt)
            seen.add(stmt)
    return "\n".join(used)


def _is_test(name: str, node) -> bool:
    if name.startswith("test_") or name.startswith("Test"):
        return True
    text = node.text.decode("utf-8") if hasattr(node, "text") else ""
    return bool(re.match(r"(describe|it|test)\s*\(", text))


def _get_class_header(node) -> str:
    text = node.text.decode("utf-8")
    lines = text.split("\n")
    header_lines = []
    for line in lines:
        header_lines.append(line)
        if "{" in line or ":" in line:
            break
    return "\n".join(header_lines[:3])


def _get_signature(node, language: str) -> str:
    text = node.text.decode("utf-8")
    lines = text.split("\n")
    if language == "python":
        for line in lines:
            if "def " in line:
                return line.strip().rstrip(":")
    else:
        for line in lines:
            if "(" in line:
                sig = line.strip()
                if sig.endswith("{"):
                    sig = sig[:-1].strip()
                return sig
    return lines[0].strip() if lines else ""


class CodeParser(BaseParser):
    def __init__(self, source_id: str = "", repo_root: str = "") -> None:
        self._source_id = source_id
        self._repo_root = repo_root
        self._py_parser = Parser(PY_LANGUAGE)
        self._ts_parser = Parser(TS_LANGUAGE)
        self._tsx_parser = Parser(TSX_LANGUAGE)

    def supported_extensions(self) -> set[str]:
        return {".py", ".ts", ".tsx"}

    def parse_file(
        self, file_path: str, source: str | None = None
    ) -> list[ParsedChunk]:
        path = Path(file_path)
        ext = path.suffix.lower()
        if ext not in self.supported_extensions():
            return []

        try:
            content_bytes = path.read_bytes()
        except (OSError, IOError) as e:
            logger.warning("Cannot read %s: %s", file_path, e)
            return []

        content_str = content_bytes.decode("utf-8", errors="replace")

        if ext == ".py":
            parser = self._py_parser
            language = "python"
            chunk_map = _PY_CHUNK_MAP
        elif ext == ".tsx":
            parser = self._tsx_parser
            language = "typescript"
            chunk_map = _TS_CHUNK_MAP
        else:
            parser = self._ts_parser
            language = "typescript"
            chunk_map = _TS_CHUNK_MAP

        tree = parser.parse(content_bytes)
        imports_map = _build_imports_map(tree, content_bytes)
        chunks: list[ParsedChunk] = []
        module_chunk_added = False

        self._walk_tree(
            tree.root_node,
            chunks,
            file_path,
            language,
            chunk_map,
            imports_map,
            content_str,
            parent_class=None,
        )

        # Module-level chunk for imports + exports
        module_imports = "\n".join(set(imports_map.values()))
        if module_imports.strip():
            chunks.append(
                ParsedChunk(
                    source_id=self._source_id,
                    source_type="code",
                    file_path=file_path,
                    language=language,
                    chunk_type="module",
                    name=path.stem,
                    qualified_name=f"{path.stem} (module)",
                    content=module_imports,
                    content_with_context=module_imports,
                    start_line=0,
                    end_line=0,
                    metadata={"is_module": True},
                    imports_used=list(imports_map.keys()),
                )
            )

        return chunks

    def _walk_tree(
        self,
        node,
        chunks: list[ParsedChunk],
        file_path: str,
        language: str,
        chunk_map: dict[str, str],
        imports_map: dict[str, str],
        full_content: str,
        parent_class: str | None,
    ) -> None:
        for child in node.children:
            node_type = child.type
            chunk_type = chunk_map.get(node_type)

            # Handle methods inside classes
            if parent_class and node_type in (
                "function_definition",
                "method_definition",
            ):
                chunk_type = "method"
            elif not chunk_type and node_type == "method_definition":
                chunk_type = "method"

            # Handle arrow function const assignments in TS
            if (
                not chunk_type
                and node_type == "lexical_declaration"
                and language == "typescript"
            ):
                text = child.text.decode("utf-8")
                if "=>" in text or "function" in text:
                    chunk_type = "function"

            # Handle top-level assignments as config chunks
            if (
                not chunk_type
                and node_type in ("expression_statement", "lexical_declaration")
                and child.parent
                and child.parent.type in ("module", "program")
            ):
                text = child.text.decode("utf-8")
                if "=" in text and ("{" in text or "[" in text):
                    chunk_type = "config"

            # Handle test call expressions
            if not chunk_type and node_type == "expression_statement":
                text = child.text.decode("utf-8")
                if re.match(r"(describe|it|test)\s*\(", text):
                    chunk_type = "test"

            if chunk_type:
                name = _extract_name(child)
                content = child.text.decode("utf-8")
                start_line = child.start_point[0] + 1
                end_line = child.end_point[0] + 1
                line_count = end_line - start_line + 1

                # Determine if test
                if _is_test(name, child):
                    chunk_type = "test"

                # Build qualified name
                if parent_class:
                    qualified_name = f"{parent_class}.{name}"
                else:
                    qualified_name = name

                # Build content_with_context
                relevant_imps = _relevant_imports(content, imports_map)
                context_parts = []
                if relevant_imps:
                    context_parts.append(relevant_imps)
                    context_parts.append("")

                if chunk_type == "method" and parent_class:
                    # Find the class node to get header
                    class_header = f"class {parent_class}:"
                    if language == "typescript":
                        class_header = f"class {parent_class} {{"
                    context_parts.append(class_header)

                context_parts.append(content)

                if (
                    chunk_type == "method"
                    and parent_class
                    and language == "typescript"
                ):
                    context_parts.append("}")

                content_with_context = "\n".join(context_parts)

                # Extract metadata
                decorators = _extract_decorators(child)
                bases = _extract_bases(child) if chunk_type == "class" else []
                signature = _get_signature(child, language)
                is_async = "async " in content.split("\n")[0]

                if language == "python":
                    # Extract docstring
                    docstring = None
                    for sub in child.children:
                        if sub.type == "expression_statement":
                            for ss in sub.children:
                                if ss.type == "string":
                                    docstring = ss.text.decode("utf-8").strip(
                                        "\"'"
                                    )
                                    break

                    metadata = {
                        "decorators": decorators,
                        "bases": bases,
                        "signature": signature,
                        "return_type": None,
                        "docstring": docstring,
                        "is_async": is_async,
                        "is_dunder": name.startswith("__") and name.endswith("__"),
                        "complexity": _get_complexity(line_count),
                    }
                else:
                    is_exported = "export " in content.split("\n")[0]
                    accessibility = None
                    for kw in ("public", "private", "protected"):
                        if kw in content.split("\n")[0]:
                            accessibility = kw
                            break

                    metadata = {
                        "decorators": decorators,
                        "heritage": bases,
                        "return_type": None,
                        "signature": signature,
                        "accessibility": accessibility,
                        "is_async": is_async,
                        "is_abstract": "abstract " in content.split("\n")[0],
                        "is_exported": is_exported,
                        "test_framework": None,
                        "complexity": _get_complexity(line_count),
                    }

                # For class chunks: emit signature + constructor only
                if chunk_type == "class":
                    class_content = _get_class_header(child)
                    # Find constructor
                    for sub in child.children:
                        if sub.type == "block" or sub.type == "class_body":
                            for method in sub.children:
                                m_name = _extract_name(method)
                                if m_name in ("__init__", "constructor"):
                                    class_content += (
                                        "\n" + _get_signature(method, language)
                                    )
                                    break

                    content_for_class = class_content
                else:
                    content_for_class = content

                chunks.append(
                    ParsedChunk(
                        source_id=self._source_id,
                        source_type="code",
                        file_path=file_path,
                        language=language,
                        chunk_type=chunk_type,
                        name=name,
                        qualified_name=qualified_name,
                        content=content_for_class if chunk_type == "class" else content,
                        content_with_context=content_with_context,
                        start_line=start_line,
                        end_line=end_line,
                        metadata=metadata,
                        signature=signature,
                        complexity=_get_complexity(line_count),
                    )
                )

                # Recurse into class body for methods
                if chunk_type == "class":
                    for sub in child.children:
                        if sub.type in ("block", "class_body"):
                            self._walk_tree(
                                sub,
                                chunks,
                                file_path,
                                language,
                                chunk_map,
                                imports_map,
                                full_content,
                                parent_class=name,
                            )
            else:
                # Recurse for non-chunk nodes
                self._walk_tree(
                    child,
                    chunks,
                    file_path,
                    language,
                    chunk_map,
                    imports_map,
                    full_content,
                    parent_class=parent_class,
                )
