from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from git import Repo

from app.config import RepositoryConfig

logger = logging.getLogger("[git]")

_CODE_EXTENSIONS = {".py", ".ts", ".tsx"}


class GitClient:
    def __init__(self, repos_base_dir: str = "/repos") -> None:
        self._base_dir = Path(repos_base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def get_local_path(self, repo_url: str) -> Path:
        slug = re.sub(r"[^\w\-.]", "_", urlparse(repo_url).path.strip("/"))
        return self._base_dir / slug

    def clone_or_pull(
        self,
        repo_config: RepositoryConfig,
        credential: str | None,
        credential_type: str = "token",
    ) -> str:
        local_path = self.get_local_path(repo_config.repository_url)
        clone_url = repo_config.repository_url
        env: dict[str, str] = {}
        temp_key_path: str | None = None

        try:
            if credential and credential_type == "token":
                # Use GIT_ASKPASS to inject token instead of embedding in URL.
                # This prevents the token from being persisted in .git/config.
                askpass_script = self._write_askpass_script(credential)
                env["GIT_ASKPASS"] = askpass_script
                env["GIT_TERMINAL_PROMPT"] = "0"

                # Ensure clone URL uses https with no embedded credentials
                parsed = urlparse(clone_url)
                clone_url = (
                    f"{parsed.scheme}://{parsed.hostname}"
                    f"{':%d' % parsed.port if parsed.port else ''}"
                    f"{parsed.path}"
                )
                if not clone_url.endswith(".git"):
                    clone_url += ".git"

            elif credential and credential_type == "ssh":
                if credential.startswith("-----BEGIN"):
                    fd, temp_key_path = tempfile.mkstemp(
                        prefix=f"key_{os.getpid()}_"
                    )
                    os.write(fd, credential.encode())
                    os.close(fd)
                    os.chmod(temp_key_path, 0o600)
                    key_path = temp_key_path
                else:
                    key_path = credential

                # Use strict host key checking with known_hosts
                known_hosts = self._get_known_hosts_path()
                env["GIT_SSH_COMMAND"] = (
                    f"ssh -i {key_path}"
                    f" -o UserKnownHostsFile={known_hosts}"
                    f" -o StrictHostKeyChecking=accept-new"
                )

            if local_path.exists() and (local_path / ".git").exists():
                logger.info("Pulling updates for %s", repo_config.repository_url)
                repo = Repo(str(local_path))
                with repo.git.custom_environment(**env):
                    repo.remotes.origin.pull()
            else:
                logger.info(
                    "Cloning %s (branch=%s)",
                    repo_config.repository_url,
                    repo_config.branch,
                )
                local_path.mkdir(parents=True, exist_ok=True)
                repo = Repo.clone_from(
                    clone_url,
                    str(local_path),
                    branch=repo_config.branch,
                    env=env if env else None,
                )

            sha = repo.head.commit.hexsha
            logger.info("HEAD at %s for %s", sha[:12], repo_config.repository_url)
            return sha

        finally:
            if temp_key_path and os.path.exists(temp_key_path):
                os.unlink(temp_key_path)
            # Clean up askpass script
            askpass_path = env.get("GIT_ASKPASS")
            if askpass_path and os.path.exists(askpass_path):
                os.unlink(askpass_path)

    def get_current_sha(self, local_path: Path) -> str:
        repo = Repo(str(local_path))
        return repo.head.commit.hexsha

    def get_changed_files(
        self, local_path: Path, since_sha: str
    ) -> list[str]:
        repo = Repo(str(local_path))
        diff_output = repo.git.diff("--name-only", since_sha, "HEAD")
        files = [f for f in diff_output.strip().splitlines() if f]
        return [
            f
            for f in files
            if Path(f).suffix.lower() in _CODE_EXTENSIONS
        ]

    def apply_directory_filter(
        self, files: list[str], rules: list[str]
    ) -> list[str]:
        if rules == ["*"]:
            return files

        include_rules = [r for r in rules if not r.startswith("!")]
        exclude_rules = [r[1:] for r in rules if r.startswith("!")]

        result = []
        for f in files:
            excluded = any(
                f"/{ex}/" in f"/{f}" or f.startswith(f"{ex}/")
                for ex in exclude_rules
            )
            if excluded:
                continue

            if include_rules:
                included = any(
                    f.startswith(f"{inc}/") or f"/{inc}/" in f"/{f}"
                    for inc in include_rules
                )
                if not included:
                    continue

            result.append(f)

        return result

    def walk_files(
        self, local_path: Path, rules: list[str]
    ) -> list[str]:
        all_files = []
        for path in local_path.rglob("*"):
            if path.is_file() and path.suffix.lower() in _CODE_EXTENSIONS:
                rel = str(path.relative_to(local_path))
                if not rel.startswith(".git/"):
                    all_files.append(rel)

        return self.apply_directory_filter(all_files, rules)

    def _write_askpass_script(self, token: str) -> str:
        """Write a temporary GIT_ASKPASS script that echoes the token.

        This avoids embedding credentials in the clone URL (which gets
        persisted to .git/config). The script is deleted in the finally block.
        """
        fd, path = tempfile.mkstemp(prefix="git_askpass_", suffix=".sh")
        # The script echoes the token as the password for any prompt
        os.write(fd, f"#!/bin/sh\necho '{token}'\n".encode())
        os.close(fd)
        os.chmod(path, 0o700)
        return path

    def _get_known_hosts_path(self) -> str:
        """Return a shared known_hosts file path under the repos base dir."""
        path = self._base_dir / ".ssh_known_hosts"
        if not path.exists():
            path.touch(mode=0o644)
        return str(path)
