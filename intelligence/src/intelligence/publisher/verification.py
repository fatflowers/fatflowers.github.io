"""Verify the exact public artifact, not merely a successful Git push."""
from __future__ import annotations

import hashlib
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser


MARKER = re.compile(r'<span hidden data-intelligence-artifact="([0-9a-f]{64})"></span>')


def publication_marker(markdown: str) -> str:
    """Append a Hugo-preserved marker bound to the complete Markdown artifact."""
    if MARKER.search(markdown):
        raise ValueError("artifact already has a publication marker")
    digest = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    return markdown + '\n<span hidden data-intelligence-artifact="%s"></span>\n' % digest


class _Markers(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = []

    def handle_starttag(self, tag, attrs):
        if tag == "span":
            self.values.extend(value for name, value in attrs if name == "data-intelligence-artifact")


def verify_publication(url: str, markdown: str, *, timeout_seconds=300.0,
                       interval_seconds=5.0, opener=urllib.request.urlopen,
                       clock=time.monotonic, sleep=time.sleep) -> None:
    marker = MARKER.search(markdown)
    if not marker:
        raise ValueError("expected artifact has no publication fingerprint")
    if urllib.parse.urlsplit(url).scheme != "https":
        raise ValueError("public verification requires HTTPS")
    deadline = clock() + min(max(timeout_seconds, 0), 300)
    expected = marker.group(1)
    while True:
        separator = "&" if "?" in url else "?"
        request = urllib.request.Request(url + separator + "intelligence_artifact=" + expected,
                                         headers={"Cache-Control": "no-cache", "User-Agent": "PersonalIntelligence/1.0"})
        try:
            with opener(request, timeout=max(0.1, min(10, deadline - clock()))) as response:
                if response.status == 200 and urllib.parse.urlsplit(response.geturl()).scheme == "https":
                    parser = _Markers()
                    parser.feed(response.read(2_000_001).decode("utf-8", errors="replace"))
                    if parser.values == [expected]:
                        return
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        remaining = deadline - clock()
        if remaining <= 0:
            raise RuntimeError("public page did not serve the expected artifact before verification timeout; Git push is not publication confirmation")
        sleep(min(max(interval_seconds, 0.1), 10, remaining))
