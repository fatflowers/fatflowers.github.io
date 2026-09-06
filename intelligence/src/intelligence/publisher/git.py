from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence


Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class GitPublishResult:
    dry_run: bool
    pushed: bool
    commit_sha: Optional[str]
    paths: tuple[Path, ...]
    commands: tuple[tuple[str, ...], ...]


class GitPublisher:
    """Stage explicit report paths, commit, and optionally push.

    ``dry_run=True`` is the safe default.  The caller must deliberately set
    both ``dry_run=False`` and ``push=True`` to mutate the remote repository.
    """

    def __init__(self, repository: Path, *, runner: Runner = subprocess.run):
        self.repository = repository.resolve()
        self.runner = runner

    def publish(
        self,
        paths: Sequence[Path],
        *,
        message: str,
        push: bool = False,
        dry_run: bool = True,
        remote: str = "origin",
        branch: str = "main",
    ) -> GitPublishResult:
        normalized = tuple(Path(path) for path in paths)
        if not normalized:
            raise ValueError("at least one explicit publish path is required")
        for path in normalized:
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"publish path must be repository-relative: {path}")
            value = path.as_posix()
            if not value.startswith(("content/posts/intelligence/", "static/images/intelligence/")):
                raise ValueError(f"path is outside the publish allowlist: {value}")

        commands: list[tuple[str, ...]] = [("git", "add", "--", *(path.as_posix() for path in normalized))]
        commands.append(("git", "commit", "-m", message, "--", *(path.as_posix() for path in normalized)))
        if push:
            commands.append(("git", "push", remote, f"HEAD:{branch}"))
        if dry_run:
            return GitPublishResult(True, False, None, normalized, tuple(commands))

        if push:
            self._check_outgoing_commits(remote, branch)

        for command in commands:
            if command[1] == "commit":
                diff = self.runner(("git", "diff", "--cached", "--quiet", "--",
                                    *(path.as_posix() for path in normalized)),
                                   cwd=self.repository, capture_output=True, text=True, check=False)
                if diff.returncode == 0:
                    # A previous push or deployment check may have failed after
                    # committing this exact artifact. Resume without an empty commit.
                    continue
                if diff.returncode != 1:
                    raise RuntimeError("cannot inspect staged publication artifact")
            completed = self.runner(
                command,
                cwd=self.repository,
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode:
                raise RuntimeError((completed.stderr or completed.stdout or f"failed: {' '.join(command)}").strip())

        completed = self.runner(
            ("git", "rev-parse", "HEAD"),
            cwd=self.repository,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode:
            raise RuntimeError((completed.stderr or "failed to read commit SHA").strip())
        return GitPublishResult(False, push, completed.stdout.strip(), normalized, tuple(commands))

    def _check_outgoing_commits(self, remote: str, branch: str) -> None:
        """Explicit staging does not exclude earlier worktree snapshot commits."""
        for command in (
            ("git", "fetch", "--", remote, branch),
            ("git", "log", "--format=", "--name-only", "--diff-merges=first-parent",
             "-z", f"{remote}/{branch}..HEAD", "--"),
        ):
            result = self.runner(command, cwd=self.repository, capture_output=True,
                                 text=True, check=False)
            if result.returncode:
                raise RuntimeError("cannot verify outgoing publication commits")
        for name in result.stdout.split("\0"):
            name = name.strip()
            if name and not name.startswith((
                "content/posts/intelligence/", "static/images/intelligence/"
            )):
                raise RuntimeError(f"outgoing commit outside publish allowlist: {name}")
