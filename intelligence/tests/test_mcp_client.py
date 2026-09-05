import io
import json
import urllib.error

import pytest

from intelligence.mcp.auth import StaticTokenProvider
from intelligence.mcp.client import StreamableHTTPMCPClient
from intelligence.mcp.errors import MCPAuthenticationError, MCPRateLimitError, MCPTransientError


class Response:
    def __init__(self, body, headers=None):
        self.body = body
        self.headers = headers or {"Content-Type": "application/json"}

    def read(self):
        return self.body.encode()


def test_client_parses_streamable_sse_and_sends_bearer():
    seen = []

    def opener(request, timeout):
        seen.append(request)
        body = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n'
        return Response(body, {"Content-Type": "text/event-stream", "Mcp-Session-Id": "session-1"})

    client = StreamableHTTPMCPClient("https://tools.example/mcp", StaticTokenProvider("secret"), opener=opener)
    result = client._request({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert result == {"ok": True}
    assert seen[0].get_header("Authorization") == "Bearer secret"


@pytest.mark.parametrize(
    ("status", "error"),
    [(401, MCPAuthenticationError), (429, MCPRateLimitError), (503, MCPTransientError)],
)
def test_http_failures_are_classified(status, error):
    def opener(request, timeout):
        raise urllib.error.HTTPError(request.full_url, status, "failed", {}, io.BytesIO(b"failure"))

    client = StreamableHTTPMCPClient("https://tools.example/mcp", StaticTokenProvider("secret"), opener=opener)
    with pytest.raises(error):
        client._request({"jsonrpc": "2.0", "id": 1, "method": "ping"})
