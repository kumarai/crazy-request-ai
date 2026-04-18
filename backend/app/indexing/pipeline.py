from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse
from uuid import UUID

from dateutil.parser import isoparse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import RepositoryConfig, settings
from app.db.repositories.checkpoints import CheckpointsRepository
from app.db.repositories.chunks import ChunksRepository
from app.db.repositories.credentials import CredentialsRepository
from app.db.repositories.sources import SourcesRepository
from app.indexing.api_client import ApiClient
from app.indexing.embedder import Embedder
from app.indexing.git_client import GitClient
from app.indexing.gitlab_client import GitLabClient
from app.indexing.parsers.api_parser import ApiResponseParser
from app.indexing.parsers.code_parser import CodeParser
from app.indexing.parsers.generic_parser import GenericParser
from app.indexing.parsers.wiki_parser import WikiParser
from app.indexing.summarizer import Summarizer

if TYPE_CHECKING:
    from app.llm.client import LLMClient

logger = logging.getLogger("[indexing]")


@dataclass
class IndexStats:
    files_processed: int = 0
    chunks_created: int = 0
    chunks_deleted: int = 0
    dependencies_created: int = 0
    errors: list[str] = field(default_factory=list)


class IndexingPipeline:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        redis,
        llm: LLMClient,
    ) -> None:
        self._sf = session_factory
        self._redis = redis
        self._llm = llm
        self._sources_repo = SourcesRepository(session_factory)
        self._chunks_repo = ChunksRepository(session_factory)
        self._checkpoints_repo = CheckpointsRepository(session_factory)
        self._credentials_repo = CredentialsRepository(session_factory)
        self._git_client = GitClient(settings.indexing_repos_base_dir)
        self._summarizer = Summarizer(llm, llm.resolve_model("summary"))
        self._embedder = Embedder(llm, llm.resolve_model("embedding"))

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------
    async def bootstrap_from_config(self) -> None:
        """Called at startup. Upsert sources and dispatch full index if needed."""
        from app.tasks.index_tasks import index_repo, index_source, index_wiki

        for repo_cfg in settings.repositories:
            safe_config = {
                "repository_url": repo_cfg.repository_url,
                "directory": repo_cfg.directory,
                "branch": repo_cfg.branch,
                "is_internal": repo_cfg.is_internal,
                "wiki_enabled": repo_cfg.wiki_enabled,
                "schedule": repo_cfg.schedule,
            }
            name = self._repo_name(repo_cfg.repository_url)
            cred_id = UUID(repo_cfg.credential_id) if repo_cfg.credential_id else None
            source = await self._sources_repo.upsert_source(
                name=name,
                source_type="git_repo",
                config=safe_config,
                credential_id=cred_id,
            )
            source_id = str(source["id"])

            checkpoint = await self._checkpoints_repo.get_index_checkpoint(
                source["id"]
            )
            if checkpoint is None:
                logger.info("No checkpoint for %s — dispatching full index", name)
                index_repo.delay(source_id, mode="full")

            if repo_cfg.wiki_enabled:
                wiki_name = f"{name}-wiki"
                wiki_source = await self._sources_repo.upsert_source(
                    name=wiki_name,
                    source_type="gitlab_wiki",
                    config=safe_config,
                    credential_id=cred_id,
                )
                wiki_checkpoint = await self._checkpoints_repo.get_index_checkpoint(
                    wiki_source["id"]
                )
                if wiki_checkpoint is None:
                    index_wiki.delay(str(wiki_source["id"]), mode="full")

        for generic_cfg in settings.generic_sources:
            # Validate path against allowlist at config load time
            self._validate_generic_path(generic_cfg.path)

            cred_id = (
                UUID(generic_cfg.credential_id) if generic_cfg.credential_id else None
            )
            source = await self._sources_repo.upsert_source(
                name=generic_cfg.name,
                source_type=generic_cfg.source_type,
                config={
                    "path": generic_cfg.path,
                    "schedule": generic_cfg.schedule,
                },
                credential_id=cred_id,
            )
            checkpoint = await self._checkpoints_repo.get_index_checkpoint(
                source["id"]
            )
            if checkpoint is None:
                index_source.delay(str(source["id"]), mode="full")

        logger.info("Bootstrap complete")

    # ------------------------------------------------------------------
    # Git repo indexing
    # ------------------------------------------------------------------
    async def index_git_repo(
        self,
        source: dict,
        mode: str = "incremental",
    ) -> IndexStats:
        stats = IndexStats()
        source_id = source["id"]
        config = source.get("config", {})
        repo_url = config.get("repository_url", "")
        if not repo_url:
            stats.errors.append("Missing repository_url in source config")
            return stats

        repo_config = RepositoryConfig(
            **{k: config[k] for k in RepositoryConfig.model_fields if k in config}
        )

        # Resolve credential from DB only via source.credential_id FK
        credential, credential_type = await self._resolve_credential(source)

        local_path = self._git_client.get_local_path(repo_url)
        current_sha = self._git_client.clone_or_pull(
            repo_config, credential, credential_type=credential_type
        )

        # Parser stores repo-relative paths, not absolute
        parser = CodeParser(source_id=str(source_id), repo_root=str(local_path))

        if mode == "full":
            # Get repo-relative file list
            all_files = self._git_client.walk_files(
                local_path, repo_config.directory
            )
            chunks = self._parse_files(
                parser, all_files, local_path, stats
            )

            if chunks:
                # Compare content hashes to skip expensive LLM calls
                # for chunks whose code hasn't changed since last index.
                existing_hashes = await self._chunks_repo.get_content_hashes(
                    source_id
                )
                changed_chunks = []
                unchanged = 0
                for c in chunks:
                    key = (c.file_path, c.name, c.start_line)
                    h = hashlib.md5(c.content.encode()).hexdigest()
                    if existing_hashes.get(key) != h:
                        changed_chunks.append(c)
                    else:
                        unchanged += 1

                if unchanged:
                    logger.info(
                        "Full index: %d changed, %d unchanged — skipping LLM for unchanged",
                        len(changed_chunks),
                        unchanged,
                    )

                if changed_chunks:
                    changed_chunks = await self._summarize_and_embed(changed_chunks)
                    chunk_dicts = [
                        self._chunk_to_dict(c, source_id, str(local_path))
                        for c in changed_chunks
                    ]
                    stats.chunks_created = await self._chunks_repo.upsert_chunks_batch(
                        chunk_dicts
                    )

                # Dependencies are cheap to extract — always rebuild from all parsed chunks
                deps = self._extract_dependencies(
                    chunks, source_id, local_path
                )
                stats.dependencies_created = (
                    await self._chunks_repo.upsert_dependencies_batch(deps)
                )

            # Clean up chunks for files that no longer exist in the repo
            stats.chunks_deleted = await self._chunks_repo.delete_chunks_not_in_files(
                all_files, source_id
            )

            await self._checkpoints_repo.upsert_index_checkpoint(
                source_id, current_sha
            )

        else:  # incremental
            checkpoint = await self._checkpoints_repo.get_index_checkpoint(
                source_id
            )
            last_sha = checkpoint["last_commit_sha"] if checkpoint else None

            if last_sha and current_sha == last_sha:
                logger.info("No changes for source %s", source_id)
                return stats

            if last_sha:
                changed = self._git_client.get_changed_files(local_path, last_sha)
                changed = self._git_client.apply_directory_filter(
                    changed, repo_config.directory
                )
            else:
                changed = self._git_client.walk_files(
                    local_path, repo_config.directory
                )

            if not changed:
                await self._checkpoints_repo.upsert_index_checkpoint(
                    source_id, current_sha
                )
                return stats

            # Delete stale chunks AND dependencies for changed files
            stats.chunks_deleted = (
                await self._chunks_repo.delete_chunks_for_files(changed, source_id)
            )
            await self._chunks_repo.delete_dependencies_for_files(
                changed, source_id
            )

            chunks = self._parse_files(
                parser, changed, local_path, stats, check_exists=True
            )

            if chunks:
                chunks = await self._summarize_and_embed(chunks)
                chunk_dicts = [
                    self._chunk_to_dict(c, source_id, str(local_path))
                    for c in chunks
                ]
                stats.chunks_created = await self._chunks_repo.upsert_chunks_batch(
                    chunk_dicts
                )

                # Resolve deps against ALL repo files, not just changed ones,
                # so imports from changed files to unchanged files resolve correctly
                all_repo_files = set(
                    self._git_client.walk_files(local_path, repo_config.directory)
                )
                deps = self._extract_dependencies(
                    chunks, source_id, local_path, extra_known_files=all_repo_files
                )
                stats.dependencies_created = (
                    await self._chunks_repo.upsert_dependencies_batch(deps)
                )

            await self._checkpoints_repo.upsert_index_checkpoint(
                source_id, current_sha
            )

        logger.info(
            "Index complete for %s: files=%d, created=%d, deleted=%d, deps=%d",
            source_id,
            stats.files_processed,
            stats.chunks_created,
            stats.chunks_deleted,
            stats.dependencies_created,
        )
        return stats

    # ------------------------------------------------------------------
    # Wiki indexing
    # ------------------------------------------------------------------
    async def index_wiki(
        self,
        source: dict,
        mode: str = "incremental",
    ) -> IndexStats:
        stats = IndexStats()
        source_id = source["id"]
        config = source.get("config", {})
        repo_url = config.get("repository_url", "")

        parsed = urlparse(repo_url)
        gitlab_base_url = f"{parsed.scheme}://{parsed.netloc}"
        project_path = parsed.path.strip("/")
        encoded_path = project_path.replace("/", "%2F")

        # Resolve token from credentials table (same as git repo indexing)
        token, _ = await self._resolve_credential(source)
        if not token:
            msg = f"No credential found for wiki source {source_id} — cannot access GitLab API"
            logger.error(msg)
            stats.errors.append(msg)
            return stats

        gitlab_client = GitLabClient(gitlab_base_url, token)

        try:
            pages = await gitlab_client.list_wiki_pages(encoded_path)
        except Exception as e:
            logger.error("Failed to list wiki pages: %s", e, exc_info=True)
            stats.errors.append(str(e))
            return stats

        wiki_parser = WikiParser(
            source_id=str(source_id),
            source_url=f"{gitlab_base_url}/{project_path}/-/wikis",
        )

        for page in pages:
            slug = page.get("slug", "")
            updated_at = page.get("updated_at")

            if mode == "incremental":
                checkpoint = await self._checkpoints_repo.get_wiki_checkpoint(
                    source_id, slug
                )
                if checkpoint and updated_at:
                    last = checkpoint.get("last_updated_at")
                    if last:
                        last_dt = isoparse(str(last)) if not isinstance(last, datetime) else last
                        updated_dt = isoparse(str(updated_at)) if not isinstance(updated_at, datetime) else updated_at
                        if updated_dt <= last_dt:
                            continue

            try:
                full_page = await gitlab_client.get_wiki_page(encoded_path, slug)
                content = full_page.get("content", "")

                await self._chunks_repo.delete_chunks_for_source_slug(
                    source_id, slug
                )

                chunks = wiki_parser.parse_content(
                    content,
                    page_title=full_page.get("title", slug),
                    file_path=f"wiki/{slug}",
                    url=f"{gitlab_base_url}/{project_path}/-/wikis/{slug}",
                )

                if chunks:
                    chunks = await self._summarize_and_embed(chunks)
                    chunk_dicts = [
                        self._chunk_to_dict(c, source_id) for c in chunks
                    ]
                    stats.chunks_created += await self._chunks_repo.upsert_chunks_batch(
                        chunk_dicts
                    )
                    stats.files_processed += 1

                await self._checkpoints_repo.upsert_wiki_checkpoint(
                    source_id,
                    slug,
                    gitlab_page_id=full_page.get("id"),
                    last_updated_at=updated_at,
                )
            except Exception as e:
                logger.error("Wiki page %s failed: %s", slug, e, exc_info=True)
                stats.errors.append(f"wiki/{slug}: {e}")

        return stats

    # ------------------------------------------------------------------
    # Generic source indexing (file uploads from object storage or local)
    # ------------------------------------------------------------------
    async def index_generic(
        self,
        source: dict,
        mode: str = "incremental",
    ) -> IndexStats:
        stats = IndexStats()
        source_id = source["id"]
        config = source.get("config", {})
        source_name = source.get("name", "")
        storage_prefix = config.get("storage_prefix", "")

        # Object storage path (file uploads via MinIO/GCS)
        if storage_prefix:
            return await self._index_generic_from_storage(
                source_id, source_name, storage_prefix, mode, stats
            )

        # Legacy: local filesystem path
        source_path = config.get("path", "")
        if source_path:
            self._validate_generic_path(source_path)
            return await self._index_generic_from_local(
                source_id, source_name, source_path, mode, stats
            )

        stats.errors.append("No storage_prefix or path in source config")
        return stats

    async def _index_generic_from_storage(
        self,
        source_id,
        source_name: str,
        storage_prefix: str,
        mode: str,
        stats: IndexStats,
    ) -> IndexStats:
        """Index files uploaded to object storage (MinIO/S3/GCS)."""
        from app.storage import create_storage_client

        storage = create_storage_client(settings)
        objects = await storage.list_objects(storage_prefix)

        if not objects:
            logger.info("No files in storage for %s", source_name)
            return stats

        parser = GenericParser(source_id=str(source_id), source_name=source_name)
        indexed_file_paths: list[str] = []
        existing_hashes: dict = {}
        if mode == "incremental":
            existing_hashes = await self._chunks_repo.get_content_hashes(source_id)

        for obj in objects:
            key = obj["key"]
            rel_path = key.removeprefix(storage_prefix)
            if not rel_path:
                continue

            # Check extension
            suffix = Path(rel_path).suffix.lower()
            if suffix not in parser.supported_extensions():
                continue

            try:
                content = await storage.get_object_text(key)

                # Write to a temp file for the parser (parsers expect file paths)
                import tempfile
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=suffix, delete=False
                ) as tmp:
                    tmp.write(content)
                    tmp_path = tmp.name

                try:
                    chunks = parser.parse_file(tmp_path)
                    for c in chunks:
                        c.file_path = rel_path

                    # Incremental: skip unchanged via content hash
                    if mode == "incremental" and existing_hashes:
                        chunks = [
                            c for c in chunks
                            if existing_hashes.get(
                                (c.file_path, c.name, c.start_line)
                            ) != hashlib.md5(c.content.encode()).hexdigest()
                        ]

                    if chunks:
                        chunks = await self._summarize_and_embed(chunks)
                        chunk_dicts = [
                            self._chunk_to_dict(c, source_id) for c in chunks
                        ]
                        stats.chunks_created += await self._chunks_repo.upsert_chunks_batch(
                            chunk_dicts
                        )
                        stats.files_processed += 1

                    indexed_file_paths.append(rel_path)
                finally:
                    os.unlink(tmp_path)

            except Exception as e:
                logger.error(
                    "Storage parse error %s: %s", key, e, exc_info=True
                )
                stats.errors.append(f"{key}: {e}")

        stats.chunks_deleted = await self._chunks_repo.delete_chunks_not_in_files(
            indexed_file_paths, source_id
        )
        await self._checkpoints_repo.upsert_index_checkpoint(
            source_id, f"generic-{datetime.now(timezone.utc).isoformat()}"
        )
        return stats

    async def _index_generic_from_local(
        self,
        source_id,
        source_name: str,
        source_path: str,
        mode: str,
        stats: IndexStats,
    ) -> IndexStats:
        """Index files from local filesystem (legacy mode)."""
        parser = GenericParser(source_id=str(source_id), source_name=source_name)
        path = Path(source_path)

        if path.is_file():
            files = [path]
        elif path.is_dir():
            files = [
                f
                for f in path.rglob("*")
                if f.is_file()
                and f.suffix.lower() in parser.supported_extensions()
            ]
        else:
            stats.errors.append(f"Path not found: {source_path}")
            return stats

        indexed_file_paths: list[str] = []
        file_indexed_times: dict[str, datetime] = {}
        if mode == "incremental":
            file_indexed_times = await self._chunks_repo.get_file_indexed_times(
                source_id
            )

        for file_path in files:
            rel_path = str(file_path.relative_to(path)) if path.is_dir() else file_path.name

            if mode == "incremental":
                mtime = datetime.fromtimestamp(
                    file_path.stat().st_mtime, tz=timezone.utc
                )
                last_indexed = file_indexed_times.get(rel_path)
                if last_indexed and mtime <= last_indexed:
                    indexed_file_paths.append(rel_path)
                    continue

            try:
                chunks = parser.parse_file(str(file_path))
                for c in chunks:
                    c.file_path = rel_path

                if chunks:
                    chunks = await self._summarize_and_embed(chunks)
                    chunk_dicts = [
                        self._chunk_to_dict(c, source_id) for c in chunks
                    ]
                    stats.chunks_created += await self._chunks_repo.upsert_chunks_batch(
                        chunk_dicts
                    )
                    stats.files_processed += 1

                indexed_file_paths.append(rel_path)
            except Exception as e:
                logger.error("Generic parse error %s: %s", file_path, e, exc_info=True)
                stats.errors.append(f"{file_path}: {e}")

        stats.chunks_deleted = await self._chunks_repo.delete_chunks_not_in_files(
            indexed_file_paths, source_id
        )
        await self._checkpoints_repo.upsert_index_checkpoint(
            source_id, f"generic-{datetime.now(timezone.utc).isoformat()}"
        )
        return stats

    # ------------------------------------------------------------------
    # Remote API indexing
    # ------------------------------------------------------------------
    async def index_api(
        self,
        source: dict,
        mode: str = "incremental",
    ) -> IndexStats:
        stats = IndexStats()
        source_id = source["id"]
        config = source.get("config", {})
        source_name = source.get("name", "")

        if not config.get("url"):
            stats.errors.append("Missing 'url' in API source config")
            return stats

        # Resolve credential
        credential, credential_type = await self._resolve_credential(source)

        # Fetch all pages
        api_client = ApiClient(config, credential, credential_type)
        try:
            payloads = await api_client.fetch_all()
        except Exception as e:
            logger.error("API fetch failed for %s: %s", source_name, e, exc_info=True)
            stats.errors.append(f"Fetch failed: {e}")
            return stats

        # Parse responses into chunks
        parser = ApiResponseParser(
            source_id=str(source_id),
            source_name=source_name,
            config=config,
        )
        chunks = parser.parse_responses(payloads)

        if not chunks:
            logger.info("No chunks parsed from API source %s", source_name)
            await self._checkpoints_repo.upsert_index_checkpoint(
                source_id, f"api-{datetime.now(timezone.utc).isoformat()}"
            )
            return stats

        # For incremental: skip unchanged chunks via content hashing
        if mode == "incremental":
            existing_hashes = await self._chunks_repo.get_content_hashes(source_id)
            changed_chunks = []
            unchanged = 0
            for c in chunks:
                key = (c.file_path, c.name, c.start_line)
                h = hashlib.md5(c.content.encode()).hexdigest()
                if existing_hashes.get(key) != h:
                    changed_chunks.append(c)
                else:
                    unchanged += 1
            if unchanged:
                logger.info(
                    "API incremental: %d changed, %d unchanged",
                    len(changed_chunks), unchanged,
                )
            chunks_to_process = changed_chunks
        else:
            chunks_to_process = chunks

        # Summarize + embed + upsert
        if chunks_to_process:
            chunks_to_process = await self._summarize_and_embed(chunks_to_process)
            chunk_dicts = [
                self._chunk_to_dict(c, source_id) for c in chunks_to_process
            ]
            stats.chunks_created = await self._chunks_repo.upsert_chunks_batch(
                chunk_dicts
            )

        stats.files_processed = len(payloads)

        # Delete stale chunks (items that no longer appear in API response)
        current_file_paths = [c.file_path for c in chunks]
        stats.chunks_deleted = await self._chunks_repo.delete_chunks_not_in_files(
            current_file_paths, source_id
        )

        await self._checkpoints_repo.upsert_index_checkpoint(
            source_id, f"api-{datetime.now(timezone.utc).isoformat()}"
        )

        logger.info(
            "API index complete for %s: pages=%d, created=%d, deleted=%d",
            source_name,
            stats.files_processed,
            stats.chunks_created,
            stats.chunks_deleted,
        )
        return stats

    # ------------------------------------------------------------------
    # Single-file reindex
    # ------------------------------------------------------------------
    async def reindex_file(self, file_path: str, source_id: str) -> None:
        """Reindex a single file. file_path is repo-relative (from the post-commit hook).
        We resolve it under the cloned repo root for parsing, but store repo-relative."""
        source = await self._sources_repo.get_source(UUID(source_id))
        if not source:
            logger.error("Source %s not found for reindex", source_id)
            return

        sid = UUID(source_id)
        config = source.get("config", {})
        repo_url = config.get("repository_url", "")

        # Pull latest so we read the current version of the file
        current_sha: str | None = None
        if repo_url:
            repo_config = RepositoryConfig(
                **{k: config[k] for k in RepositoryConfig.model_fields if k in config}
            )
            credential, credential_type = await self._resolve_credential(source)
            current_sha = self._git_client.clone_or_pull(
                repo_config, credential, credential_type=credential_type
            )
            local_path = self._git_client.get_local_path(repo_url)
            abs_path = str(local_path / file_path)
            repo_root = str(local_path)
        else:
            local_path = Path(".")
            abs_path = file_path
            repo_root = ""

        if not os.path.exists(abs_path):
            # File was deleted — remove its chunks and dependency edges
            await self._chunks_repo.delete_chunks_for_files([file_path], sid)
            await self._chunks_repo.delete_dependencies_for_files([file_path], sid)
            logger.info("Reindex: file deleted, removed chunks+deps for %s", file_path)
        else:
            await self._chunks_repo.delete_chunks_for_files([file_path], sid)
            await self._chunks_repo.delete_dependencies_for_files([file_path], sid)

            parser = CodeParser(source_id=source_id, repo_root=repo_root)
            chunks = parser.parse_file(abs_path)
            for c in chunks:
                c.file_path = file_path

            if chunks:
                chunks = await self._summarize_and_embed(chunks)
                chunk_dicts = [self._chunk_to_dict(c, sid, repo_root) for c in chunks]
                await self._chunks_repo.upsert_chunks_batch(chunk_dicts)

                if repo_url:
                    all_repo_files = set(
                        self._git_client.walk_files(local_path, ["*"])
                    )
                else:
                    all_repo_files = set()
                deps = self._extract_dependencies(
                    chunks, sid, local_path,
                    extra_known_files=all_repo_files,
                )
                await self._chunks_repo.upsert_dependencies_batch(deps)

            logger.info("Reindexed %s (%d chunks)", file_path, len(chunks))

        # Update checkpoint so the next scheduled incremental sync doesn't
        # re-process files that this reindex already covered.
        if current_sha:
            await self._checkpoints_repo.upsert_index_checkpoint(sid, current_sha)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    async def _resolve_credential(
        self, source: dict
    ) -> tuple[str | None, str]:
        """Resolve credential from the source.credential_id FK only.
        Never trust config.credential_id from untrusted callers."""
        cred_id = source.get("credential_id")
        if not cred_id:
            return None, "token"

        uid = UUID(str(cred_id)) if not isinstance(cred_id, UUID) else cred_id
        cred_meta = await self._credentials_repo.get(uid)
        if not cred_meta:
            logger.warning("Credential %s not found for source %s", cred_id, source.get("id"))
            return None, "token"

        value = await self._credentials_repo.get_decrypted_value(uid)
        return value, cred_meta.get("credential_type", "token")

    async def _summarize_and_embed(self, chunks):
        chunks = await self._summarizer.summarize_chunks(chunks)
        return await self._embedder.embed_chunks(chunks)

    # ------------------------------------------------------------------
    # Re-embed: rebuild embed_input + embedding for all existing chunks
    # without re-parsing or re-summarizing.  Used when switching embedding
    # models (e.g. OpenAI → Voyage) to avoid burning summarizer tokens.
    # ------------------------------------------------------------------
    async def reembed_source(self, source: dict) -> IndexStats:
        from app.indexing.parsers.base import ParsedChunk

        stats = IndexStats()
        source_id = source["id"]

        rows = await self._chunks_repo.iter_chunks_for_source(source_id)
        if not rows:
            logger.info("Re-embed: source %s has no chunks", source_id)
            return stats

        # Reconstitute ParsedChunk objects from DB rows so the existing
        # Embedder._build_embed_input logic can synthesize the text.
        chunks: list[ParsedChunk] = []
        ids: list = []
        for r in rows:
            md = r.get("metadata") or {}
            chunks.append(
                ParsedChunk(
                    source_id=str(source_id),
                    source_type=r.get("source_type", "code"),
                    file_path=r.get("file_path", ""),
                    language=r.get("language", ""),
                    chunk_type=r.get("chunk_type", ""),
                    name=r.get("name", ""),
                    qualified_name=r.get("qualified_name", ""),
                    content=r.get("content", ""),
                    content_with_context=r.get("content_with_context") or "",
                    start_line=r.get("start_line", 0),
                    end_line=r.get("end_line", 0),
                    metadata=md,
                    summary=r.get("summary"),
                    purpose=r.get("purpose"),
                    signature=r.get("signature"),
                    reuse_signal=r.get("reuse_signal"),
                    domain_tags=r.get("domain_tags") or [],
                    complexity=r.get("complexity"),
                    imports_used=r.get("imports_used") or [],
                )
            )
            # Carry through side_effects / example_call if the new embedder
            # uses them — attributes are set via setattr to stay compatible
            # with the current ParsedChunk dataclass.
            if r.get("side_effects") is not None:
                setattr(chunks[-1], "side_effects", r["side_effects"])
            if r.get("example_call") is not None:
                setattr(chunks[-1], "example_call", r["example_call"])
            ids.append(r["id"])

        chunks = await self._embedder.embed_chunks(chunks)

        updates = []
        for chunk_id, c in zip(ids, chunks):
            updates.append(
                {
                    "id": chunk_id,
                    "embedding": c.embedding,
                    "embed_input": getattr(c, "embed_input", None),
                }
            )
        updated = await self._chunks_repo.update_embeddings(updates)
        stats.chunks_created = updated  # reuse the field for "rows updated"
        logger.info(
            "Re-embed complete for %s: %d chunks updated",
            source_id,
            updated,
        )
        return stats

    def _parse_files(
        self,
        parser: CodeParser,
        files: list[str],
        local_path: Path,
        stats: IndexStats,
        check_exists: bool = False,
    ) -> list:
        """Parse files, storing repo-relative paths in chunks."""
        chunks = []
        for f in files:
            full_path = str(local_path / f)
            if check_exists and not os.path.exists(full_path):
                continue
            try:
                file_chunks = parser.parse_file(full_path)
                # Override file_path to repo-relative
                for c in file_chunks:
                    c.file_path = f
                chunks.extend(file_chunks)
                stats.files_processed += 1
            except Exception as e:
                logger.error("Parse error %s: %s", f, e, exc_info=True)
                stats.errors.append(f"{f}: {e}")
        return chunks

    def _chunk_to_dict(self, chunk, source_id, repo_root: str = "") -> dict:
        sid = source_id if not isinstance(source_id, str) else UUID(source_id)
        return {
            "source_id": sid,
            "source_type": chunk.source_type,
            "file_path": chunk.file_path,
            "repo_root": repo_root,
            "language": chunk.language,
            "chunk_type": chunk.chunk_type,
            "name": chunk.name,
            "qualified_name": chunk.qualified_name,
            "content": chunk.content,
            "content_with_context": chunk.content_with_context,
            "start_line": chunk.start_line,
            "end_line": chunk.end_line,
            "summary": chunk.summary,
            "purpose": chunk.purpose,
            "signature": chunk.signature,
            "reuse_signal": chunk.reuse_signal,
            "domain_tags": chunk.domain_tags or [],
            "complexity": chunk.complexity,
            "imports_used": chunk.imports_used or [],
            "embedding": chunk.embedding,
            "summary_embedding": chunk.summary_embedding,
            "metadata": chunk.metadata or {},
            "commit_sha": None,
        }

    def _extract_dependencies(
        self,
        chunks,
        source_id,
        local_path: Path,
        extra_known_files: set[str] | None = None,
    ) -> list[dict]:
        """Build dependency edges using repo-relative file paths, scoped by source_id.

        extra_known_files: on incremental runs, pass the full repo file set so
        imports from changed files to unchanged files still resolve.
        """
        sid = source_id if not isinstance(source_id, str) else UUID(source_id)
        deps: list[dict] = []
        seen: set[tuple[str, str]] = set()

        known_files = {c.file_path for c in chunks}
        if extra_known_files:
            known_files |= extra_known_files

        for c in chunks:
            for imp in c.imports_used or []:
                if imp.startswith("."):
                    continue

                # Try to resolve import to an actual repo-relative file path
                resolved = self._resolve_import(imp, c.file_path, known_files)
                if not resolved:
                    continue

                edge = (c.file_path, resolved)
                if edge in seen:
                    continue
                seen.add(edge)

                deps.append({
                    "source_id": sid,
                    "from_file": c.file_path,
                    "to_file": resolved,
                    "import_names": [imp],
                    "dep_type": "import",
                })
        return deps

    def _resolve_import(
        self, import_name: str, from_file: str, known_files: set[str]
    ) -> str | None:
        """Best-effort resolution of an import name to a repo-relative file path."""
        # Direct match: import name maps to a file we know about
        candidates = [
            f"{import_name}.py",
            f"{import_name}.ts",
            f"{import_name}.tsx",
            f"{import_name}/index.ts",
            f"{import_name}/index.tsx",
            f"{import_name}/__init__.py",
        ]
        # Also try converting dot notation to path (Python: foo.bar -> foo/bar.py)
        if "." in import_name:
            path_form = import_name.replace(".", "/")
            candidates.extend([
                f"{path_form}.py",
                f"{path_form}/__init__.py",
            ])

        for candidate in candidates:
            if candidate in known_files:
                return candidate

        return None

    def _validate_generic_path(self, raw_path: str) -> None:
        """Ensure generic source path is under an allowed root."""
        resolved = str(Path(raw_path).resolve())
        for root in settings.indexing_generic_allowed_roots:
            root_resolved = str(Path(root).resolve())
            if resolved == root_resolved or resolved.startswith(root_resolved + "/"):
                return
        raise ValueError(
            f"Generic source path {raw_path!r} is outside allowed roots: "
            f"{settings.indexing_generic_allowed_roots}"
        )

    def _repo_name(self, url: str) -> str:
        path = urlparse(url).path.strip("/")
        return path.replace("/", "-").lower()
