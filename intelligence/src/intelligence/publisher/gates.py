from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from ipaddress import ip_address
from pathlib import Path
from typing import Callable, Optional, Protocol, Sequence
from urllib.parse import urlparse

from intelligence.reporter import Report, RenderedReport


Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    message: str


class GateFailure(RuntimeError):
    def __init__(self, results: Sequence[GateResult]):
        self.results = tuple(results)
        failed = "; ".join(result.message for result in results if not result.passed)
        super().__init__(failed or "publication validation failed")


@dataclass(frozen=True)
class GateContext:
    repository: Path
    report: Report
    rendered: RenderedReport
    changed_paths: Optional[tuple[Path, ...]] = None


class PublicationGate(Protocol):
    name: str

    def check(self, context: GateContext) -> GateResult: ...


class PublicSourcesGate:
    name = "public_sources"

    def check(self, context: GateContext) -> GateResult:
        sources = tuple(source for signal in context.report.signals for source in signal.sources)
        if not sources:
            return GateResult(self.name, False, "report has no sources")
        for source in sources:
            parsed = urlparse(source.url)
            if not source.is_public:
                return GateResult(self.name, False, f"source is not public: {source.url}")
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                return GateResult(self.name, False, f"source URL is not public HTTP(S): {source.url}")
            if parsed.username or parsed.password:
                return GateResult(self.name, False, f"source URL contains credentials: {source.url}")
            hostname = (parsed.hostname or "").lower().rstrip(".")
            if hostname in {"localhost", "0.0.0.0"} or hostname.endswith((".local", ".internal")):
                return GateResult(self.name, False, f"source URL is not public: {source.url}")
            try:
                address = ip_address(hostname)
            except ValueError:
                pass
            else:
                if not address.is_global:
                    return GateResult(self.name, False, f"source URL is not public: {source.url}")
        evidence_urls = {
            evidence.url
            for signal in context.report.signals
            for evidence in signal.analysis.evidence
        }
        source_urls = {source.url for source in sources}
        missing = sorted(evidence_urls - source_urls)
        if missing:
            return GateResult(self.name, False, f"analysis evidence is absent from report sources: {missing[0]}")
        return GateResult(self.name, True, f"validated {len(source_urls)} public source(s)")


class SecretsGate:
    name = "sensitive_content"
    _PATTERNS = (
        ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
        ("authorization bearer", re.compile(r"(?i)authorization\s*:\s*bearer\s+[A-Za-z0-9._~+/-]{8,}")),
        ("cookie header", re.compile(r"(?i)(?:set-)?cookie\s*:\s*[^\n]{8,}")),
        ("generic secret", re.compile(r"(?i)(?:api[_-]?key|client[_-]?secret|access[_-]?token)\s*[:=]\s*['\"]?[A-Za-z0-9._~+/-]{12,}")),
        ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
        ("OpenAI key", re.compile(r"sk-[A-Za-z0-9_-]{20,}")),
        ("local URL", re.compile(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0)(?::\d+)?", re.I)),
    )

    def check(self, context: GateContext) -> GateResult:
        for label, pattern in self._PATTERNS:
            if pattern.search(context.rendered.markdown):
                return GateResult(self.name, False, f"possible {label} detected")
        return GateResult(self.name, True, "no sensitive content pattern detected")


class FrontMatterGate:
    name = "front_matter"
    _REQUIRED = {
        "title",
        "date",
        "categories",
        "tags",
        "description",
        "reportType",
        "period",
        "generated",
        "sourcesCount",
        "hiddenInHomeList",
        "reportId",
    }

    def check(self, context: GateContext) -> GateResult:
        markdown = context.rendered.markdown
        if not markdown.startswith("---\n") or "\n---\n" not in markdown[4:]:
            return GateResult(self.name, False, "missing YAML front matter delimiters")
        raw = markdown.split("\n---\n", 1)[0][4:]
        keys: set[str] = set()
        for line in raw.splitlines():
            if not line or line[0].isspace() or ":" not in line:
                return GateResult(self.name, False, f"invalid front matter line: {line!r}")
            key, value = line.split(":", 1)
            if not key or not value.strip():
                return GateResult(self.name, False, f"empty front matter field: {key}")
            if key in keys:
                return GateResult(self.name, False, f"duplicate front matter field: {key}")
            keys.add(key)
        missing = sorted(self._REQUIRED - keys)
        if missing:
            return GateResult(self.name, False, f"missing front matter fields: {', '.join(missing)}")
        return GateResult(self.name, True, "front matter is structurally valid")


class HugoBuildGate:
    name = "hugo_build"

    def __init__(self, *, runner: Runner = subprocess.run, command: tuple[str, ...] = ("hugo", "--minify")):
        self.runner = runner
        self.command = command

    def check(self, context: GateContext) -> GateResult:
        completed = self.runner(
            self.command,
            cwd=context.repository,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode:
            message = (completed.stderr or completed.stdout or "Hugo build failed").strip()
            return GateResult(self.name, False, message[-500:])
        return GateResult(self.name, True, "Hugo production build succeeded")


class GitDiffScopeGate:
    name = "git_diff_scope"

    def __init__(
        self,
        *,
        runner: Runner = subprocess.run,
        allowed_prefixes: tuple[str, ...] = (
            "content/posts/intelligence/",
            "static/images/intelligence/",
        ),
    ):
        self.runner = runner
        self.allowed_prefixes = allowed_prefixes

    def _discover_paths(self, repository: Path) -> tuple[Path, ...]:
        completed = self.runner(
            ("git", "status", "--porcelain=v1", "--untracked-files=all"),
            cwd=repository,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode:
            raise RuntimeError((completed.stderr or "git status failed").strip())
        paths: list[Path] = []
        for line in completed.stdout.splitlines():
            raw = line[3:]
            if " -> " in raw:
                raw = raw.split(" -> ", 1)[1]
            paths.append(Path(raw.strip('"')))
        return tuple(paths)

    def check(self, context: GateContext) -> GateResult:
        try:
            paths = context.changed_paths if context.changed_paths is not None else self._discover_paths(context.repository)
        except RuntimeError as exc:
            return GateResult(self.name, False, str(exc))
        if not paths:
            return GateResult(self.name, False, "no report artifact change found")
        invalid = sorted(
            path.as_posix()
            for path in paths
            if not any(path.as_posix().startswith(prefix) for prefix in self.allowed_prefixes)
        )
        if invalid:
            return GateResult(self.name, False, f"change outside publish scope: {invalid[0]}")
        expected = context.rendered.relative_path.as_posix()
        if expected not in {path.as_posix() for path in paths}:
            return GateResult(self.name, False, f"rendered report is absent from Git changes: {expected}")
        return GateResult(self.name, True, f"validated {len(paths)} publish path(s)")


@dataclass
class PublishValidator:
    gates: tuple[PublicationGate, ...] = field(default_factory=tuple)

    @classmethod
    def default(cls, *, runner: Runner = subprocess.run) -> "PublishValidator":
        return cls(
            (
                PublicSourcesGate(),
                SecretsGate(),
                FrontMatterGate(),
                HugoBuildGate(runner=runner),
                GitDiffScopeGate(runner=runner),
            )
        )

    def validate(self, context: GateContext) -> tuple[GateResult, ...]:
        results = tuple(gate.check(context) for gate in self.gates)
        if any(not result.passed for result in results):
            raise GateFailure(results)
        return results
