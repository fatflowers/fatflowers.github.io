"""Small deterministic extractor for official HTML and Firecrawl responses.

This deliberately returns missing evidence instead of guessing article dates.
It preserves prose and source metadata; analysis belongs downstream.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
import ipaddress
import json
import re
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list = field(default_factory=list)
    parent: "Node | None" = field(default=None, repr=False)

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()


class Parser(HTMLParser):
    VOID = {"meta", "link", "img", "br", "hr", "input", "source", "area", "base", "embed", "wbr"}

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: v or "" for k, v in attrs}, parent=self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


EXCLUDED = {"script", "style", "nav", "footer", "header", "aside", "form", "button", "noscript", "svg", "template"}
BLOCKS = {"p", "div", "section", "article", "main", "h1", "h2", "h3", "h4", "li", "blockquote", "pre", "br", "tr"}


def _text(node, clean=True):
    if clean and (node.tag in EXCLUDED or node.attrs.get("aria-hidden") == "true" or "hidden" in node.attrs):
        return ""
    if clean and re.search(r"(?:^|[\s_-])(cookie|newsletter|related|share|breadcrumb|navigation)(?:$|[\s_-])", node.attrs.get("class", "") + " " + node.attrs.get("id", ""), re.I):
        return ""
    text = "".join(_text(c, clean) if isinstance(c, Node) else c for c in node.children)
    return ("\n" + text + "\n") if node.tag in BLOCKS else text


def _clean(text):
    return "\n\n".join(re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in re.split(r"\n+", unescape(text)) if line.strip())


def _date(value):
    if not isinstance(value, str):
        return None, "unknown"
    value = value.strip()
    value = re.sub(r"\b(\d{1,2})(?:st|nd|rd|th)\b", r"\1", value)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            datetime.strptime(value, "%Y-%m-%d")
            return value, "day"
        except ValueError:
            return None, "unknown"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # A timestamp lacking an offset is not silently assigned a timezone.
        if parsed.tzinfo is not None:
            return parsed.isoformat(), "second"
        return parsed.date().isoformat(), "day"
    except ValueError:
        pass
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat(), "day"
        except ValueError:
            continue
    return None, "unknown"


def _json_articles(value):
    if isinstance(value, list):
        for entry in value:
            yield from _json_articles(entry)
    elif isinstance(value, dict):
        kinds = value.get("@type", [])
        kinds = [kinds] if isinstance(kinds, str) else kinds
        if any(k in {"Article", "NewsArticle", "BlogPosting", "TechArticle", "Report"} for k in kinds):
            yield value
        if "@graph" in value:
            yield from _json_articles(value["@graph"])


def _url(value, base):
    result = urlsplit(urljoin(base, value))
    if result.scheme not in {"http", "https"} or not result.hostname or result.username or result.password:
        return None
    return urlunsplit((result.scheme, result.netloc, result.path or "/", result.query, ""))


def discover_links(url, html=None, *, markdown=None):
    """Same-host article candidates; card dates are evidence, never publication proof."""
    if markdown and not html:
        # Convert only Markdown links to a tiny synthetic DOM, retaining each
        # line as a local discovery card. It is never used as article prose.
        from html import escape
        cards = []
        for line in markdown.splitlines():
            if len(cards) >= 100:
                break
            anchors = [f'<a href="{escape(href, quote=True)}">{escape(label)}</a>' for label, href in re.findall(r"(?<!!)\[([^\]\n]+)\]\((https?://[^\s)]+|/[^\s)]+)(?:\s+\"[^\"]*\")?\)", line)]
            if anchors:
                cards.append("<li>" + " ".join(anchors) + "</li>")
        html = "<main><ul>" + "".join(cards) + "</ul></main>"
    root = Parser(html or "").root
    result, seen = [], set()
    forbidden = re.compile(r"/(?:tags?|topics?|categor(?:y|ies)|authors?|search|login|signup|privacy|terms|page|feed|rss)(?:/|$)", re.I)
    base_path = urlsplit(url).path.rstrip('/')
    editorial_prefix = base_path + '/' if base_path in {'/blog', '/engineering'} else '/index/' if base_path == '/news/engineering' else None
    for node in root.walk():
        if node.tag != "a" or not node.attrs.get("href"):
            continue
        link = _url(node.attrs["href"], url)
        if not link or urlsplit(link).hostname != urlsplit(url).hostname or link.rstrip("/") == url.rstrip("/") or link in seen:
            continue
        path = urlsplit(link).path
        if editorial_prefix and not path.startswith(editorial_prefix):
            continue
        if forbidden.search(path) or path.rstrip("/") in {"", "/blog", "/news", "/docs", "/documentation"} or re.search(r"\.(?:png|jpg|jpeg|webp|gif|svg|pdf|zip)$", path, re.I):
            continue
        ancestors, parent = [], node.parent
        while parent:
            ancestors.append(parent)
            parent = parent.parent
        if any(a.tag in {"nav", "footer", "header", "aside"} for a in ancestors):
            continue
        title = _clean(_text(node))
        if len(title) < 8 or title.lower() in {"read more", "learn more", "view all", "next page", "previous page"}:
            continue
        published_at, precision, evidence = None, "unknown", None
        # Only inspect a local card, not the entire list containing other dates.
        card = next((a for a in ancestors if a.tag in {"article", "li"} or re.search(r"card|post-item", a.attrs.get("class", ""))), None)
        if card:
            for date_node in card.walk():
                if date_node.tag == "time":
                    raw = date_node.attrs.get("datetime") or _clean(_text(date_node))
                    published_at, precision = _date(raw)
                    if published_at:
                        evidence = {"source": "index_card_time", "value": raw}
                        break
        seen.add(link)
        result.append({"url": link, "title": title, "published_at": published_at, "publication_precision": precision, "publication_evidence": evidence})
        if len(result) >= 50:
            break
    return result


def enrich_article(url, *, html=None, markdown=None, metadata=None):
    """Return hydration fields with explicit publication evidence and page type."""
    metadata = metadata or {}
    root = Parser(html or "").root
    nodes = list(root.walk())
    meta = dict(metadata)
    for node in nodes:
        if node.tag == "meta":
            key = node.attrs.get("property") or node.attrs.get("name")
            if key and node.attrs.get("content"):
                meta[key] = node.attrs["content"]
    structured = []
    for node in nodes:
        if node.tag == "script" and node.attrs.get("type", "").startswith("application/ld+json"):
            try:
                structured.extend(_json_articles(json.loads(_text(node, clean=False))))
            except (ValueError, TypeError):
                continue
    article_nodes = [n for n in nodes if n.tag == "article"]
    entry_nodes = [n for n in nodes if "entryPage" in n.attrs.get("class", "").split() or n.attrs.get("data-permalink-context", "").rstrip("/") == urlsplit(url).path.rstrip("/") and n.attrs.get("data-permalink-context")]
    candidates = article_nodes or entry_nodes or [n for n in nodes if n.tag == "main"] or [n for n in nodes if n.tag == "body"] or [root]
    body = max(candidates, key=lambda n: len(_text(n)))
    content = _clean(_text(body)) if html else (markdown or "").strip()
    h1 = next((_clean(_text(n)) for n in body.walk() if n.tag == "h1"), None)
    if not h1 and entry_nodes:
        h1 = next((_clean(_text(n)) for n in body.walk() if n.tag == "h2" or "beat-title" in n.attrs.get("class", "").split()), None)
    title = h1 or meta.get("og:title") or meta.get("ogTitle") or meta.get("title") or next((a.get("headline") for a in structured if a.get("headline")), None) or next((_clean(_text(n, False)) for n in nodes if n.tag == "title"), "")
    if not title and markdown:
        heading = re.search(r"^#\s+(.+)$", markdown, re.M)
        title = heading.group(1).strip() if heading else ""
    canonical = next((_url(n.attrs.get("href", ""), url) for n in nodes if n.tag == "link" and "canonical" in n.attrs.get("rel", "").split()), None) or _url(meta.get("canonicalUrl") or meta.get("url") or url, url) or url
    dates = [("jsonld.datePublished", a.get("datePublished")) for a in structured]
    dates += [("metadata." + key, meta.get(key)) for key in ("article:published_time", "datePublished", "publishedTime", "published_at", "publicationDate")]
    if urlsplit(url).hostname in {'anthropic.com', 'www.anthropic.com'} and urlsplit(url).path.startswith('/engineering/'):
        for node in nodes:
            css = node.attrs.get('class', '')
            if 'HeroEngineering' in css and '__date' in css:
                raw = re.sub(r'^Published\s*', '', _clean(_text(node)), flags=re.I)
                dates.append(('article.engineering_header', raw))
    # Developer-blog templates put a plain date next to the H1, outside the
    # Markdown <article>. Read that local header, not dates in the nav/cards.
    if urlsplit(url).hostname == 'developers.openai.com' and re.fullmatch(r'/blog/[^/]+/?', urlsplit(url).path):
        heading = next((n for n in nodes if n.tag == 'h1'), None)
        parent = heading.parent if heading else None
        for _ in range(4):
            if parent is None:
                break
            before = []
            for node in parent.walk():
                if node is heading:
                    break
                if node.tag in {'span', 'time'} and _date(_clean(_text(node)))[0]:
                    before.append(_clean(_text(node)))
            if len(set(before)) == 1:
                dates.append(('article.developer_blog_header', before[0]))
                break
            parent = parent.parent
    # Datetime inside article header is useful even though header text is excluded.
    dates += [("article.time", n.attrs.get("datetime") or _clean(_text(n))) for n in body.walk() if n.tag == "time" and not re.search(r"modified|updated", n.attrs.get("class", "") + " " + n.attrs.get("itemprop", ""), re.I)]
    # Some official news templates render the date as plain visible text. Only
    # date-labelled elements and whole-date strings qualify, not dates in prose.
    for node in body.walk():
        label = " ".join(node.attrs.get(key, "") for key in ("class", "id", "itemprop"))
        if re.search(r"date|published", label, re.I) and not re.search(r"updated|modified", label, re.I):
            raw = _clean(_text(node))
            raw = re.sub(r"^Published(?:\s+on)?\s*:?\s*", "", raw, flags=re.I)
            dates.append(("article.visible_date", raw))
        if urlsplit(url).hostname == "til.simonwillison.net" and "created" in node.attrs.get("class", "").split():
            created = re.match(r"Created\s+(\d{4}-\d{2}-\d{2}T[^,\s]+)", _clean(_text(node)))
            if created:
                dates.append(("article.created_timestamp", created.group(1)))
    published_at, precision, evidence = None, "unknown", None
    for source, raw in dates:
        parsed, granularity = _date(raw)
        if parsed:
            published_at, precision, evidence = parsed, granularity, {"source": source, "value": raw}
            break
    path = urlsplit(url).path.rstrip("/")
    index_path = bool(re.fullmatch(r"(?:/(?:blog|news|announcements|updates|articles|changelog))?(?:/page/\d+)?", path))
    links = discover_links(url, html, markdown=markdown)
    primary_link = None
    if urlsplit(url).hostname in {"simonwillison.net", "www.simonwillison.net"}:
        for node in body.walk():
            if "beat-title" in node.attrs.get("class", "").split():
                primary_link = next((_url(n.attrs["href"], url) for n in node.walk() if n.tag == "a" and n.attrs.get("href")), None)
                break
    if index_path or (len(article_nodes) > 1 and not structured):
        kind = "index"
    elif urlsplit(url).hostname in {"simonwillison.net", "www.simonwillison.net"}:
        kind = "commentary"
    elif structured or article_nodes or (title and len(content) >= 300):
        kind = "article"
    else:
        kind = "unknown"
    # Index-wide dates must not be mistaken for a specific article publication.
    if kind == "index":
        published_at, precision, evidence = None, "unknown", None
    return {"title": str(title), "content_text": content, "published_at": published_at, "publication_precision": precision, "publication_evidence": evidence, "page_kind": kind, "canonical_url": canonical, "primary_link": primary_link, "discovered_links": links, "body_provenance": {"source": "http_html" if html else "firecrawl_markdown", "url": url, "selector": body.tag if html else "markdown", "characters": len(content)}}


def fetch_article(url, *, timeout=20, max_bytes=2_000_000):
    """Retrieve public HTTP HTML with bounded size and an identifiable user agent."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Article URL must be public HTTP(S)")
    if parsed.hostname in {"localhost", "localhost.localdomain"} or parsed.hostname.endswith(".local"):
        raise ValueError("Local article hosts are not allowed")
    try:
        if not ipaddress.ip_address(parsed.hostname).is_global:
            raise ValueError("Private article addresses are not allowed")
    except ValueError as exc:
        if "not allowed" in str(exc):
            raise
    request = Request(url, headers={"User-Agent": "PersonalIntelligence/1.0 (+https://fatflowers.github.io)", "Accept": "text/html,application/xhtml+xml"})
    with urlopen(request, timeout=timeout) as response:
        media_type = response.headers.get_content_type()
        if media_type not in {"text/html", "application/xhtml+xml"}:
            raise ValueError("Article response is not HTML")
        raw = response.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ValueError("Article exceeds hydration byte limit")
        encoding = response.headers.get_content_charset() or "utf-8"
        return enrich_article(response.geturl(), html=raw.decode(encoding, errors="replace"))
