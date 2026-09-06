"""Deterministic collector for public GitHub organization activity."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Callable

from intelligence.normalize import NormalizedItem

from .base import ChannelSpec, CollectionPage

USER_AGENT = "fatflowers-personal-intelligence/0.1 (+https://fatflowers.github.io/)"
API_VERSION = "2022-11-28"
_NEXT_LINK = re.compile(r'<([^>]+)>;\s*rel="next"')


class GitHubCollectorError(RuntimeError):
    retryable = False


class GitHubAuthenticationError(GitHubCollectorError):
    pass


class GitHubNotFoundError(GitHubCollectorError):
    pass


class GitHubRateLimitError(GitHubCollectorError):
    def __init__(self, message: str, *, reset_at: str | None = None) -> None:
        self.reset_at = reset_at
        suffix = f"; resets at {reset_at}" if reset_at else ""
        super().__init__(f"{message}{suffix}")


class GitHubTransientError(GitHubCollectorError):
    retryable = True


def environment_token(variable: str = "GITHUB_TOKEN") -> Callable[[], str | None]:
    """Return an opt-in token callback without retaining the credential."""

    def provide() -> str | None:
        import os

        return os.environ.get(variable) or None

    return provide


def _header(headers: Mapping[str, Any], name: str) -> str | None:
    lowered = name.casefold()
    for key, value in headers.items():
        if str(key).casefold() == lowered:
            return str(value)
    return None


def _reset_at(headers: Mapping[str, Any]) -> str | None:
    value = _header(headers, "X-RateLimit-Reset")
    if not value:
        return None
    try:
        return datetime.fromtimestamp(float(value), timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError):
        return value


def _next_page(headers: Mapping[str, Any]) -> int | None:
    link = _header(headers, "Link") or ""
    match = _NEXT_LINK.search(link)
    if not match:
        return None
    values = urllib.parse.parse_qs(urllib.parse.urlsplit(match.group(1)).query)
    try:
        return int(values["page"][0])
    except (KeyError, IndexError, ValueError):
        return None


class GitHubCollector:
    """Collect selected public events from an organization.

    Pagination uses a two-stage cursor when a previously stored event is not
    reached within ``max_pages``. The caller must persist items before committing
    ``CollectionPage.next_cursor``, just like every other collector.
    """

    def __init__(
        self,
        *,
        token_provider: Callable[[], str | None] | None = None,
        timeout: float = 30.0,
        opener: Any = None,
    ) -> None:
        self.token_provider = token_provider
        self.timeout = timeout
        self._opener = opener or urllib.request.urlopen

    def collect(self, channel: ChannelSpec, cursor: Mapping[str, Any] | None = None) -> CollectionPage:
        config = dict(channel.config)
        account_type = "user" if config.get("user") else "organization"
        organization = str(config.get("user") or config.get("organization") or "").strip()
        if not organization:
            organization = self._organization_from_url(channel.url)
        if not organization:
            raise ValueError("GitHub organization channel requires config.organization or a GitHub URL")

        state = dict(cursor or {})
        start_page = max(1, int(state.get("page", 1)))
        per_page = max(1, min(100, int(config.get("per_page", 100))))
        max_pages = max(1, min(10, int(config.get("max_pages", 3))))
        previous_id = str(state.get("last_external_id") or "")
        pending_newest = str(state.get("pending_newest_event_id") or "")
        pending_etag = str(state.get("pending_etag") or "")
        request_etag = str(state.get("etag") or "") if start_page == 1 else ""
        newest_id = pending_newest
        first_etag = pending_etag
        items: list[NormalizedItem] = []
        raw_count = 0
        page = start_page
        next_page: int | None = None
        reached_previous = False

        for offset in range(max_pages):
            events, headers, not_modified = self._get_events(
                organization,
                page=page,
                per_page=per_page,
                etag=request_etag if offset == 0 else "",
                account_type=account_type,
            )
            if not_modified:
                return CollectionPage.of([], next_cursor=state, metadata={"not_modified": True, "organization": organization})
            if page == 1:
                first_etag = _header(headers, "ETag") or first_etag
            if events and not newest_id:
                newest_id = str(events[0].get("id") or "")
            raw_count += len(events)

            for event in events:
                event_id = str(event.get("id") or "")
                if previous_id and event_id == previous_id:
                    reached_previous = True
                    break
                items.extend(self._adapt_event(event, channel, organization))
            if reached_previous:
                next_page = None
                break

            next_page = _next_page(headers)
            if next_page is None or not events:
                break
            page = next_page

        continuing = bool(previous_id and not reached_previous and next_page is not None)
        if continuing:
            next_cursor = {
                "last_external_id": previous_id,
                "page": next_page,
                "pending_newest_event_id": newest_id,
                "pending_etag": first_etag,
            }
        else:
            next_cursor = {"last_external_id": newest_id or previous_id}
            if first_etag:
                next_cursor["etag"] = first_etag

        return CollectionPage.of(
            items,
            next_cursor=next_cursor,
            raw_count=raw_count,
            metadata={
                "organization": organization,
                "pages_fetched": offset + 1,
                "pagination_pending": continuing,
                "rate_limit_remaining": _header(headers, "X-RateLimit-Remaining"),
            },
        )

    @staticmethod
    def _organization_from_url(url: str | None) -> str:
        if not url:
            return ""
        parts = [part for part in urllib.parse.urlsplit(url).path.split("/") if part]
        return parts[0] if urllib.parse.urlsplit(url).hostname in {"github.com", "www.github.com"} and parts else ""

    def _get_events(
        self, organization: str, *, page: int, per_page: int, etag: str, account_type: str = "organization"
    ) -> tuple[list[Mapping[str, Any]], Mapping[str, Any], bool]:
        query = urllib.parse.urlencode({"per_page": per_page, "page": page})
        owner = urllib.parse.quote(organization, safe='')
        endpoint = f"users/{owner}/events/public" if account_type == "user" else f"orgs/{owner}/events"
        url = f"https://api.github.com/{endpoint}?{query}"
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": API_VERSION,
        }
        token = self.token_provider() if self.token_provider else None
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if etag:
            headers["If-None-Match"] = etag
        request = urllib.request.Request(url, headers=headers, method="GET")

        try:
            response = self._opener(request, timeout=self.timeout)
            try:
                response_headers = dict(response.headers.items())
                payload = response.read()
            finally:
                close = getattr(response, "close", None)
                if close:
                    close()
        except urllib.error.HTTPError as exc:
            response_headers = dict(exc.headers.items()) if exc.headers else {}
            if exc.code == 304:
                return [], response_headers, True
            body = exc.read().decode("utf-8", "replace")[:500]
            if exc.code in {401}:
                raise GitHubAuthenticationError(body or "GitHub authentication failed") from exc
            if exc.code == 404:
                raise GitHubNotFoundError(f"GitHub organization not found: {organization}") from exc
            remaining = _header(response_headers, "X-RateLimit-Remaining")
            if exc.code == 429 or (exc.code == 403 and remaining == "0"):
                raise GitHubRateLimitError(
                    body or "GitHub API rate limit exceeded", reset_at=_reset_at(response_headers)
                ) from exc
            if exc.code >= 500:
                raise GitHubTransientError(body or f"GitHub API returned HTTP {exc.code}") from exc
            raise GitHubCollectorError(body or f"GitHub API returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise GitHubTransientError(f"GitHub API request failed: {exc}") from exc

        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitHubCollectorError("GitHub API returned invalid JSON") from exc
        if not isinstance(decoded, list) or any(not isinstance(event, Mapping) for event in decoded):
            raise GitHubCollectorError("GitHub organization events response must be an array")
        return list(decoded), response_headers, False

    @staticmethod
    def _adapt_event(
        event: Mapping[str, Any], channel: ChannelSpec, organization: str
    ) -> list[NormalizedItem]:
        event_type = str(event.get("type") or "")
        config = channel.config
        if event_type == "ReleaseEvent" and bool(config.get("include_releases", True)):
            release = event.get("payload", {}).get("release", {}) if isinstance(event.get("payload"), Mapping) else {}
            if not isinstance(release, Mapping):
                return []
            url = release.get("html_url")
            if not url:
                return []
            author = release.get("author", {})
            return [
                NormalizedItem(
                    external_id=f"release:{release.get('id') or event.get('id')}",
                    target_slug=channel.target_slug,
                    channel_slug=channel.channel_slug,
                    url=str(url),
                    title=str(release.get("name") or release.get("tag_name") or "GitHub release"),
                    author=str(author.get("login")) if isinstance(author, Mapping) and author.get("login") else None,
                    published_at=release.get("published_at") or release.get("created_at") or event.get("created_at"),
                    content_text=str(release.get("body") or release.get("name") or release.get("tag_name") or "GitHub release"),
                    language=None,
                    metadata={"platform": "github", "event_type": event_type, "organization": organization, "repository": _repo_name(event)},
                )
            ]

        if event_type == "PushEvent" and bool(config.get("include_recent_commits", True)):
            payload = event.get("payload", {})
            commits = payload.get("commits", []) if isinstance(payload, Mapping) else []
            repo = _repo_name(event)
            actor = event.get("actor", {})
            items: list[NormalizedItem] = []
            for commit in commits if isinstance(commits, list) else []:
                if not isinstance(commit, Mapping) or not commit.get("sha") or not repo:
                    continue
                sha = str(commit["sha"])
                author = commit.get("author", {})
                author_name = author.get("name") if isinstance(author, Mapping) else None
                if not author_name and isinstance(actor, Mapping):
                    author_name = actor.get("login")
                message = str(commit.get("message") or f"Commit {sha[:7]}")
                items.append(
                    NormalizedItem(
                        external_id=f"commit:{sha}",
                        target_slug=channel.target_slug,
                        channel_slug=channel.channel_slug,
                        url=f"https://github.com/{repo}/commit/{sha}",
                        title=message.splitlines()[0][:240],
                        author=str(author_name) if author_name else None,
                        published_at=event.get("created_at"),
                        content_text=message,
                        language=None,
                        metadata={"platform": "github", "event_type": event_type, "organization": organization, "repository": repo, "event_id": event.get("id")},
                    )
                )
            return items

        if not bool(config.get("include_other_events", False)):
            return []
        repo = _repo_name(event)
        actor = event.get("actor", {})
        event_id = str(event.get("id") or "")
        if not event_id or not repo:
            return []
        return [
            NormalizedItem(
                external_id=f"event:{event_id}",
                target_slug=channel.target_slug,
                channel_slug=channel.channel_slug,
                url=f"https://github.com/{repo}",
                title=f"{event_type or 'GitHub event'} · {repo}",
                author=str(actor.get("login")) if isinstance(actor, Mapping) and actor.get("login") else None,
                published_at=event.get("created_at"),
                content_text=f"{event_type or 'GitHub event'} in {repo}",
                language=None,
                metadata={"platform": "github", "event_type": event_type, "organization": organization, "repository": repo},
            )
        ]


def _repo_name(event: Mapping[str, Any]) -> str:
    repo = event.get("repo", {})
    return str(repo.get("name") or "") if isinstance(repo, Mapping) else ""
