import io
import json
import urllib.error

import pytest

from intelligence.collectors.base import ChannelSpec
from intelligence.collectors.github import GitHubCollector, GitHubRateLimitError, USER_AGENT


class Response:
    def __init__(self, value, headers=None):
        self.value = value
        self.headers = HeaderMap(headers or {})
        self.closed = False

    def read(self):
        return json.dumps(self.value).encode()

    def close(self):
        self.closed = True


class HeaderMap(dict):
    def items(self):
        return super().items()


def channel(**config):
    return ChannelSpec(
        "composio",
        "composio-github",
        "github-release",
        "github_api",
        url="https://github.com/ComposioHQ",
        config={"organization": "ComposioHQ", "include_releases": True, "include_recent_commits": True, **config},
    )


def test_maps_release_and_push_commits_without_authentication():
    seen = []
    responses = [
        Response(
            [
                {
                    "id": "event-2",
                    "type": "ReleaseEvent",
                    "created_at": "2026-09-05T10:00:00Z",
                    "repo": {"name": "ComposioHQ/composio"},
                    "payload": {"release": {"id": 7, "html_url": "https://github.com/ComposioHQ/composio/releases/tag/v1", "tag_name": "v1", "body": "Shipped", "published_at": "2026-09-05T10:00:00Z", "author": {"login": "bot"}}},
                },
                {
                    "id": "event-1",
                    "type": "PushEvent",
                    "created_at": "2026-09-05T09:00:00Z",
                    "repo": {"name": "ComposioHQ/composio"},
                    "actor": {"login": "alice"},
                    "payload": {"commits": [{"sha": "abc123", "message": "Add MCP support", "author": {"name": "Alice"}}]},
                },
            ],
            {"ETag": '"new"', "X-RateLimit-Remaining": "58"},
        )
    ]

    def opener(request, timeout):
        seen.append(request)
        return responses.pop(0)

    page = GitHubCollector(opener=opener).collect(channel())
    assert [item.external_id for item in page.items] == ["release:7", "commit:abc123"]
    assert page.items[1].url == "https://github.com/ComposioHQ/composio/commit/abc123"
    assert page.next_cursor == {"last_external_id": "event-2", "etag": '"new"'}
    assert seen[0].get_header("User-agent") == USER_AGENT
    assert seen[0].get_header("Authorization") is None


def test_uses_optional_token_and_stops_at_previous_event():
    seen = []

    def opener(request, timeout):
        seen.append(request)
        return Response(
            [
                {"id": "new", "type": "PushEvent", "created_at": "2026-09-05T09:00:00Z", "repo": {"name": "org/repo"}, "payload": {"commits": [{"sha": "newsha", "message": "new"}]}},
                {"id": "old", "type": "PushEvent", "repo": {"name": "org/repo"}, "payload": {"commits": [{"sha": "oldsha", "message": "old"}]}},
            ],
            {"ETag": '"current"', "Link": '<https://api.github.com/orgs/org/events?page=2>; rel="next"'},
        )

    page = GitHubCollector(token_provider=lambda: "token", opener=opener).collect(
        channel(organization="org"), {"last_external_id": "old", "etag": '"prior"'}
    )
    assert [item.external_id for item in page.items] == ["commit:newsha"]
    assert page.next_cursor == {"last_external_id": "new", "etag": '"current"'}
    assert seen[0].get_header("Authorization") == "Bearer token"
    assert seen[0].get_header("If-none-match") == '"prior"'


def test_stages_pagination_without_advancing_high_water_mark():
    requests = []

    def opener(request, timeout):
        requests.append(request.full_url)
        page_number = "2" if "page=2" in request.full_url else "1"
        return Response(
            [{"id": f"event-{page_number}", "type": "WatchEvent", "repo": {"name": "org/repo"}}],
            {"ETag": '"first"', "Link": f'<https://api.github.com/orgs/org/events?per_page=1&page={int(page_number)+1}>; rel="next"'},
        )

    collector = GitHubCollector(opener=opener)
    first = collector.collect(channel(organization="org", max_pages=1, per_page=1), {"last_external_id": "old"})
    assert first.next_cursor["last_external_id"] == "old"
    assert first.next_cursor["page"] == 2
    assert first.next_cursor["pending_newest_event_id"] == "event-1"
    second = collector.collect(channel(organization="org", max_pages=1, per_page=1), first.next_cursor)
    assert "page=2" in requests[-1]
    assert second.next_cursor["page"] == 3
    assert second.next_cursor["pending_newest_event_id"] == "event-1"


def test_not_modified_is_an_empty_success():
    def opener(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 304, "not modified", HeaderMap({"ETag": '"same"'}), io.BytesIO())

    cursor = {"last_external_id": "old", "etag": '"same"'}
    page = GitHubCollector(opener=opener).collect(channel(), cursor)
    assert page.items == ()
    assert page.next_cursor == cursor
    assert page.metadata["not_modified"] is True


def test_personal_account_uses_public_user_events_endpoint():
    seen = []
    def opener(request, timeout):
        seen.append(request.full_url)
        return Response([])
    spec = ChannelSpec("simon-willison", "simon-github", "github-release", "github_api",
                       url="https://github.com/simonw", config={"user": "simonw"})
    GitHubCollector(opener=opener).collect(spec)
    assert seen == ["https://api.github.com/users/simonw/events/public?per_page=100&page=1"]


def test_github_profile_fallback_does_not_hide_api_failure():
    from intelligence.collectors.router import CollectorRouter, RouteStep, CollectionRouteError
    from intelligence.collectors.base import CollectionPage
    from intelligence.normalize import NormalizedItem
    class Failed:
        def collect(self, channel, cursor):
            raise RuntimeError("API unavailable")
    class Profile:
        def collect(self, channel, cursor):
            return CollectionPage.of([NormalizedItem(None, "composio", "composio-github", channel.url,
                                                       "Profile", None, None, "GitHub profile content")])
    with pytest.raises(CollectionRouteError, match="profile/repository"):
        CollectorRouter({"github_api": Failed(), "http": Profile()}).collect(
            channel(), route=[RouteStep("github_api"), RouteStep("http")])


def test_rate_limit_has_reset_time():
    def opener(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            403,
            "limited",
            HeaderMap({"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1788566400"}),
            io.BytesIO(b'{"message":"API rate limit exceeded"}'),
        )

    with pytest.raises(GitHubRateLimitError) as captured:
        GitHubCollector(opener=opener).collect(channel())
    assert captured.value.reset_at == "2026-09-05T00:00:00Z"
    assert "resets at" in str(captured.value)
