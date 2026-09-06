"""Read original articles before analysis, preserving publication evidence."""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from urllib.parse import quote, urlsplit
from xml.etree.ElementTree import ParseError

from intelligence.catalog import CatalogError
from intelligence.enrichment import enrich_article, fetch_article
from intelligence.models.catalog import stable_id
from intelligence.normalize import NormalizedItem, canonicalize_url


def cutoff(value=None):
    if value is None:
        return (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as exc:
        raise CatalogError("--since must be an ISO date or timestamp") from exc
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).isoformat()


def research_plan(client, *, since=None, limit=30, target=None):
    if not 1 <= limit <= 100:
        raise CatalogError("research limit must be between 1 and 100")
    since = cutoff(since)
    response = client._request("GET", client._path("/v1/items/pending-enrichment", {
        "since": min(datetime.fromisoformat(since), datetime.now(timezone.utc) - timedelta(days=7)).isoformat(), "limit": 500,
        "target_id": stable_id("target", target) if target else None,
    }))
    groups = defaultdict(deque)
    for item in response.get("items", []):
        groups[item.get("target_id", "unknown")].append(item)
    selected = []
    while groups and len(selected) < limit:
        for key in list(groups):
            selected.append(groups[key].popleft())
            if not groups[key]:
                del groups[key]
            if len(selected) == limit:
                break
    return {"since": since, "items": selected, "selected": len(selected),
            "next_step": "research hydrate --item-id ID --since SAME_CUTOFF; execute fixed Firecrawl fallback if returned"}


def research_coverage(client, *, since=None):
    return client._request("GET", client._path("/v1/coverage", {"since": cutoff(since)}))


def _item(client, item_id):
    response = client._request("GET", "/v1/items/%s" % quote(item_id, safe=""))
    item = response.get("item")
    if not isinstance(item, Mapping):
        raise CatalogError("Worker did not return the requested item")
    return item


def _fallback(url):
    return {"server": "aisa-tools", "binding": "firecrawl-page-scrape-v1",
            "tool_name": "post_firecrawl_scrape",
            "arguments": {"url": url, "proxy": "basic", "formats": ["markdown"]}}


def _queue_children(client, item, links, *, limit=30, allowed_hosts=None):
    """Queue only same-site article discoveries; never inherit index dates."""
    from .operations import normalized_item_record
    parent_url = canonicalize_url(item["url"])
    parent_host = urlsplit(parent_url).hostname
    allowed_hosts = set(allowed_hosts or [parent_host])
    metadata = item.get("raw_metadata", item.get("raw_metadata_json")) or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    prefixes = metadata.get("article_path_prefixes", [])
    records, seen = [], {parent_url}
    now = datetime.now(timezone.utc).isoformat()
    for link in links:
        url = canonicalize_url(link.get("url", ""))
        parsed = urlsplit(url)
        path = parsed.path.rstrip("/").lower()
        navigation = (
            path in {"", "/about", "/following", "/tos"}
            or path.endswith(("/about", "/following", "/verified_followers"))
            or path.startswith(("/tags/", "/tag/", "/hashtag/"))
        )
        if prefixes and not any(parsed.path.startswith(prefix) for prefix in prefixes):
            continue
        if navigation or parsed.scheme not in ("http", "https") or parsed.hostname not in allowed_hosts or parsed.username or parsed.password or url in seen:
            continue
        seen.add(url)
        candidate = NormalizedItem(external_id=None, target_slug=item["target_id"],
            channel_slug=item["channel_id"], url=url, title=link.get("title") or parsed.path,
            author=None, published_at=None, content_text="", fetched_at=now,
            metadata={"discovery_only": True, "discovered_from": parent_url,
                      "allow_paid_fallback": metadata.get("allow_paid_fallback", True)})
        record = normalized_item_record(candidate, target_id=item["target_id"], channel_id=item["channel_id"], now=now)
        # Indexes often label different articles identically (e.g. "Release").
        # A discovery stub has no body yet, so its hash must identify its URL,
        # not collapse unrelated destinations by a repeated link caption.
        record["content_hash"] = hashlib.sha256(("discovery:" + url).encode()).hexdigest()
        records.append(record)
        if len(records) == limit:
            break
    if not records:
        return []
    digest = hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()
    client.write_items(records, idempotency_key="research:discover:" + digest)
    return [{"id": row["id"], "url": row["url"]} for row in records]


def _persist(client, item, article, *, since, tool_name):
    article = dict(article)
    # A dated feed entry is source evidence for this exact article, but its
    # date never transfers to a linked tutorial or to an HTTP redirect target.
    raw = item.get("raw_metadata_json") or item.get("raw_metadata") or {}
    if isinstance(raw, str):
        raw = json.loads(raw)
    if (not article.get("published_at") and item.get("published_at")
            and raw.get("platform") == "rss"
            and canonicalize_url(article.get("canonical_url") or item["url"]) == canonicalize_url(item["url"])):
        article.update(published_at=item["published_at"], publication_precision=raw.get("publication_precision", "second"),
                       publication_evidence={"source": "feed.publication", "value": item["published_at"]})
    status, reason = "ready", "article_body_and_publication_date_verified"
    published = article.get("published_at")
    evidence = article.get("publication_evidence")
    if article.get("page_kind") == "unsupported_social_discovery":
        status, reason = "rejected", "unsupported_social_discovery_url"
    elif article.get("page_kind") == "routine_release":
        status, reason = "rejected", "release_has_no_substantive_change_details"
    elif article.get("page_kind") == "index":
        status, reason = "rejected", "index_page_requires_following_article_links"
    elif len(article.get("content_text") or "") < (1 if tool_name == "twitter-native-api" else 200):
        status, reason = "failed", "insufficient_article_body"
    elif not published or not evidence:
        status, reason = "failed", "publication_date_unverified"
    elif datetime.fromisoformat(cutoff(published)) < datetime.fromisoformat(cutoff(since)):
        status, reason = "rejected", "publication_before_research_window"
    elif datetime.fromisoformat(cutoff(published)) > datetime.now(timezone.utc) + timedelta(minutes=10):
        status, reason = "failed", "publication_date_in_future"
    payload = {"expected_revision": item["content_revision"], "status": status, "reason": reason}
    if status == "ready":
        source_url = article.get("canonical_url") or item["url"]
        source = str(evidence.get("source", ""))
        kind = "platform" if source.startswith("platform.") else "feed" if source.startswith("feed.") else "article_text" if source.startswith("article.") else "article_metadata"
        payload.update(title=article.get("title") or item["title"],
                       content_text=article["content_text"], final_url=source_url,
                       published_at=cutoff(published), fetched_at=datetime.now(timezone.utc).isoformat(),
                       tool_name=tool_name, publication_precision=article.get("publication_precision", "second"),
                       date_evidence={"kind": kind, "value": str(evidence.get("value") or published), "source_url": source_url})
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    result = client._request("POST", "/v1/items/%s/enrichment" % quote(item["id"], safe=""),
                             body=payload, headers={"Idempotency-Key": "enrichment:" + digest})
    links = article.get("discovered_links", [])
    queued = _queue_children(client, item, links) if article.get("page_kind") == "index" else []
    primary = article.get("primary_link")
    if primary and urlsplit(item["url"]).hostname in {"simonwillison.net", "www.simonwillison.net"}:
        queued.extend(_queue_children(client, item, [{"url": primary, "title": article.get("title") or primary}],
                                      limit=1, allowed_hosts={"simonwillison.net", "www.simonwillison.net", "til.simonwillison.net"}))
    return {"item_id": item["id"], "status": status, "reason": reason, "stored": result,
            "discovered_links": links, "queued_children": queued}


def research_hydrate(client, *, item_id, since=None):
    item = _item(client, item_id)
    since = cutoff(since)
    raw = item.get("raw_metadata_json") or "{}"
    metadata = json.loads(raw) if isinstance(raw, str) else raw
    if urlsplit(item["url"]).hostname in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"} and metadata.get("platform") != "twitter":
        return _persist(
            client,
            item,
            {"page_kind": "unsupported_social_discovery", "content_text": ""},
            since=since,
            tool_name="policy",
        )
    if metadata.get("platform") == "twitter":
        # A native platform response is the source. X's HTML login/index shell
        # must never replace it or cause a short but complete announcement to
        # be rejected as an index page.
        body = (item.get("content_text") or "").strip()
        native = metadata.get("raw") or {}
        complete = (bool(body) and bool(item.get("published_at"))
                    and not native.get("isTruncated") and not native.get("truncated")
                    and not metadata.get("truncated")
                    and metadata.get("source_content_kind") != "truncated_social_post"
                    and not re.search(r"(?:…|\.\.\.)\s*(?:https?://\S+)?\s*$", body))
        if not complete:
            return {"item_id": item_id, "status": "needs_platform_refetch",
                    "reason": "native_social_body_incomplete", "children": []}
        article = {"title": item.get("title") or body[:160], "content_text": body,
                   "canonical_url": item.get("canonical_url") or item["url"],
                   "published_at": item["published_at"], "publication_precision": "second",
                   "publication_evidence": {"source": "platform.twitter.published_at", "value": item["published_at"]},
                   "page_kind": "article"}
        return _persist(client, item, article, since=since, tool_name="twitter-native-api")
    if metadata.get("platform") == "github" and metadata.get("event_type") == "ReleaseEvent" and item.get("published_at"):
        # ReleaseEvent is the official API's body+timestamp, not an HTML snippet.
        article = {"title": item["title"], "content_text": item.get("content_text") or "",
                   "canonical_url": item.get("canonical_url") or item["url"],
                   "published_at": item["published_at"], "publication_precision": "second",
                   "publication_evidence": {"source": "platform.github.published_at", "value": item["published_at"]},
                   "page_kind": "article" if len(item.get("content_text") or "") >= 200 else "routine_release"}
        return _persist(client, item, article, since=since, tool_name="github-public-api")
    try:
        article = fetch_article(item["url"])
    except (OSError, ValueError, TimeoutError) as exc:
        result = _persist(client, item, {}, since=since, tool_name="http")
        result.update(reason="http_fetch_failed", error_type=type(exc).__name__)
        if metadata.get("allow_paid_fallback", True):
            result["fallback"] = _fallback(item["url"])
        return result
    result = _persist(client, item, article, since=since, tool_name="http")
    if result["status"] == "failed" and metadata.get("allow_paid_fallback", True):
        result["fallback"] = _fallback(item["url"])
    return result


def research_ingest(client, *, item_id, payload, since=None):
    item = _item(client, item_id)
    data = _firecrawl_document(payload, item)
    article = enrich_article(item["url"], markdown=data["markdown"], metadata=data.get("metadata") or {})
    return _persist(client, item, article, since=cutoff(since), tool_name="post_firecrawl_scrape")


def _firecrawl_document(payload, item):
    """Unwrap one successful scrape, never ingest a neighbouring batch result."""
    def documents(value, identity=None):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return []
        if not isinstance(value, Mapping) or value.get("success") is False or value.get("successful") is False or value.get("isError") is True or value.get("error"):
            return []
        if value.get("tool") and (value["tool"] != "post_firecrawl_scrape" or value.get("successful") is not True):
            return []
        if value.get("upstream_status") and not 200 <= int(value["upstream_status"]) < 300:
            return []
        if isinstance(value.get("structuredContent"), Mapping):
            return documents(value["structuredContent"], identity)
        identity = value.get("item_id") or identity
        if isinstance(value.get("markdown"), str):
            return [(value, identity)]
        found = []
        for key in ("data", "result", "response", "structuredContent"):
            if key in value:
                found.extend(documents(value[key], identity))
        for result in value.get("results", []):
            found.extend(documents(result, identity))
        for part in value.get("content", []):
            if isinstance(part, Mapping) and part.get("type") == "text":
                found.extend(documents(part.get("text"), identity))
        return found
    candidates = documents(payload)
    matched = []
    for document, identity in candidates:
        metadata = document.get("metadata") or {}
        url = metadata.get("sourceURL") or metadata.get("url") or metadata.get("canonicalUrl")
        if identity == item["id"] or (url and canonicalize_url(url) == canonicalize_url(item["url"])):
            matched.append(document)
    if len(matched) == 1:
        return matched[0]
    if not matched and len(candidates) == 1:
        document, identity = candidates[0]
        metadata = document.get("metadata") or {}
        if not identity and not any(metadata.get(key) for key in ("sourceURL", "url", "canonicalUrl")):
            return document
    raise CatalogError("Firecrawl input must contain exactly one successful document matching the item URL or ID")


def research_run(client, *, since=None, limit=30, target=None):
    since = cutoff(since)
    plan = research_plan(client, since=since, limit=limit, target=target)
    pending = deque(item["id"] for item in plan["items"])
    attempted, results, fallbacks = set(), [], []
    while pending and len(attempted) < limit:
        item_id = pending.popleft()
        if item_id in attempted:
            continue
        attempted.add(item_id)
        result = research_hydrate(client, item_id=item_id, since=since)
        results.append(result)
        if result.get("fallback"):
            fallbacks.append({"item_id": item_id, "since": since, **result["fallback"]})
        # Preserve the target-balanced first pass; one large index must not
        # consume every request before the other targets have been researched.
        pending.extend(row["id"] for row in result.get("queued_children", []))
    return {"since": since, "attempted": len(attempted), "results": results,
            "fallback_plans": fallbacks, "coverage": research_coverage(client, since=since),
            "batch_complete": not fallbacks and not pending and all(r['status'] in {'ready', 'rejected'} for r in results),
            "remaining_in_batch": len(pending)}


def resolve_mcp_fallbacks(client, result, *, since=None):
    """Ingest only actual native MCP responses, never a model's transcription."""
    from intelligence.mcp.codex_bridge import capture_batch
    plans = result.get("fallback_plans") or ([{"item_id": result["item_id"], **result["fallback"]}] if result.get("fallback") else [])
    unresolved, completed = [], []
    for offset in range(0, len(plans), 6):
        group = plans[offset:offset + 6]
        calls = [{"call_id": p["item_id"], "tool_name": p["tool_name"], "arguments": p["arguments"]} for p in group]
        captured = capture_batch(calls, timeout=300)
        for plan in group:
            item_id = plan["item_id"]
            if item_id not in captured.payloads:
                unresolved.append({**plan, "diagnostic": captured.diagnostics.get(item_id)})
                continue
            try:
                stored = research_ingest(client, item_id=item_id, payload=captured.payloads[item_id], since=since)
                completed.append(stored)
                if stored["status"] == "failed":
                    unresolved.append({**plan, "diagnostic": stored["reason"]})
            except (CatalogError, ValueError) as exc:
                unresolved.append({**plan, "diagnostic": str(exc)})
    effective = dict(result)
    if result.get("item_id") and completed:
        effective.update(completed[-1])
        effective.pop("fallback", None)
    completed_by_id = {row['item_id']: row for row in completed}
    rows = result.get('results', [effective] if effective.get('item_id') else [])
    unresolved_items = [completed_by_id.get(row.get('item_id'), row) for row in rows
                        if completed_by_id.get(row.get('item_id'), row).get('status')
                        not in {'ready', 'rejected', 'discovered'}]
    return {**effective, "mcp_completed": completed, "fallback_plans": unresolved,
            "unresolved_items": unresolved_items,
            "batch_complete": not unresolved and not unresolved_items and not result.get("remaining_in_batch", 0),
            "coverage": research_coverage(client, since=since)}


def research_discover(repository, client, *, target=None):
    """Read configured news indexes afresh; documentation maps are not news."""
    from .operations import normalized_item_record
    from intelligence.collectors import ChannelSpec
    from intelligence.collectors.rss import RSSCollector
    catalog = repository.load()
    targets = [entry for entry in catalog.targets if entry.enabled and (not target or entry.slug == target)]
    if target and not targets:
        raise CatalogError("enabled target not found: " + target)
    results, fallbacks = [], []
    for entry in targets:
        eligible = [channel for channel in entry.channels if channel.enabled and channel.tier == "core" and channel.url]
        # Every explicitly enabled editorial source is independent. Adding an
        # engineering blog must not silently disable the news RSS, nor may a
        # third blog disappear behind a per-target slice.
        channels = [channel for channel in eligible if channel.channel_type in ("blog", "news", "rss")]
        for channel in channels:
            now = datetime.now(timezone.utc).isoformat()
            if channel.channel_type == "rss" or channel.collector_type == "rss":
                try:
                    page = RSSCollector(timeout=20).collect(ChannelSpec.from_catalog(entry.to_dict(), channel.to_dict()))
                    # Feed order convention is newest first; preserve actual publication dates.
                    records = [normalized_item_record(item, target_id=entry.id, channel_id=channel.id, now=now) for item in page.items[:20]]
                    for record in records:
                        record["raw_metadata"] = {
                            **dict(record.get("raw_metadata") or {}),
                            "allow_paid_fallback": channel.config.get("allow_paid_fallback", True),
                        }
                    if records:
                        client.write_items(records, idempotency_key="research:feed:" + hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest())
                    results.append({"target": entry.slug, "channel": channel.slug, "status": "discovered", "queued": len(records)})
                except (OSError, ValueError, TimeoutError, ParseError) as exc:
                    results.append({"target": entry.slug, "channel": channel.slug, "status": "failed", "error_type": type(exc).__name__})
                continue
            seed = NormalizedItem(external_id=None, target_slug=entry.slug, channel_slug=channel.slug,
                                  url=channel.url, title=channel.name, author=None, published_at=None,
                                  content_text="", fetched_at=now, metadata={"discovery_only": True, "research_seed": True,
                                  "article_path_prefixes": channel.config.get("article_path_prefixes", []),
                                  "allow_paid_fallback": channel.config.get("allow_paid_fallback", True)})
            record = normalized_item_record(seed, target_id=entry.id, channel_id=channel.id, now=now)
            client.write_items([record], idempotency_key="research:seed:" + hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest())
            try:
                article = fetch_article(channel.url)
                queued = _queue_children(client, record, article.get("discovered_links", []), limit=20)
                result = {"target": entry.slug, "channel": channel.slug, "item_id": record["id"],
                          "status": "discovered" if queued else "needs_fallback", "queued": len(queued)}
                if not queued and channel.config.get("allow_paid_fallback", True):
                    fallbacks.append({"item_id": record["id"], **_fallback(channel.url)})
                results.append(result)
            except (OSError, ValueError, TimeoutError) as exc:
                results.append({"target": entry.slug, "channel": channel.slug, "item_id": record['id'], "status": "needs_fallback", "error_type": type(exc).__name__})
                if channel.config.get("allow_paid_fallback", True):
                    fallbacks.append({"item_id": record["id"], **_fallback(channel.url)})
    return {"targets_checked": len(targets), "results": results, "fallback_plans": fallbacks,
            "next_step": "research run; execute fallback plans and research ingest for inaccessible indexes"}
