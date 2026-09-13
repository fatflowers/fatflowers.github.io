"""Deterministic static feed snapshots generated from analyzed D1 records."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from intelligence.content_policy import excluded_mapping
from intelligence.normalize.text import canonicalize_url


def export_feed_snapshot(repository, client, *, force: bool = False) -> dict[str, Any]:
    root = _repository_root(repository.path)
    output = root / "static" / "data" / "intelligence-feed"
    index_path = output / "index.json"
    previous = _read_json(index_path)
    previous_latest = previous.get("latest_analyzed_at") if isinstance(previous, Mapping) else None
    policy_path = root / "intelligence" / "config" / "content-policy.yaml"
    policy_hash = hashlib.sha256(policy_path.read_bytes()).hexdigest() if policy_path.exists() else None

    if previous_latest and previous.get("policy_sha256") == policy_hash and not force:
        change = client.get_feed_export(minimum_importance=2, changed_after=str(previous_latest))
        if not change.get("changed"):
            return {"status": "unchanged", "changed": False, "path": str(index_path),
                    "latest_analyzed_at": previous_latest, "new_items": []}

    rows, cursor, latest = [], None, previous_latest
    upper = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    for _ in range(100):
        page = client.get_feed_export(
            minimum_importance=2,
            from_value="1970-01-01T00:00:00Z",
            to_value=upper,
            cursor_published=cursor.get("published_at") if cursor else None,
            cursor_id=cursor.get("item_id") if cursor else None,
            limit=500,
        )
        rows.extend(page.get("items", []))
        latest = page.get("latest_analyzed_at") or latest
        cursor = page.get("next_cursor")
        if not cursor:
            break
    else:
        raise RuntimeError("feed export exceeded 50,000 analyzed items")

    normalized = [_public_item(row) for row in rows if not excluded_mapping(row, stage="reports")]
    normalized = _dedupe(normalized)
    anchor = datetime.fromisoformat(str(latest).replace("Z", "+00:00")) if latest else datetime.now(timezone.utc)
    current_start = anchor - timedelta(days=90)
    current, archives = [], {}
    for item in normalized:
        published = datetime.fromisoformat(item["published_at"].replace("Z", "+00:00"))
        if published >= current_start:
            current.append(item)
        else:
            archives.setdefault(published.strftime("%Y-%m"), []).append(item)

    output.mkdir(parents=True, exist_ok=True)
    for month, items in archives.items():
        _write_json(output / "archive" / f"{month}.json", {
            "version": 1, "month": month, "count": len(items), "items": items,
        })
    snapshot = {
        "version": 1,
        "latest_analyzed_at": latest,
        "window_days": 90,
        "page_size": 30,
        "history_preserved": True,
        "policy_sha256": policy_hash,
        "count": len(current),
        "total_count": len(normalized),
        "targets": sorted({item["target_name"] for item in normalized}),
        "archives": sorted(archives, reverse=True),
        "items": current,
    }
    old_ids = {str(item.get("id")) for item in previous.get("items", [])} if isinstance(previous, Mapping) else set()
    new_items = [item for item in current if item["id"] not in old_ids] if previous else []
    changed = _canonical_json(previous) != _canonical_json(snapshot)
    if changed:
        _write_json(index_path, snapshot)
    return {
        "status": "updated" if changed else "unchanged",
        "changed": changed,
        "initial_snapshot": not bool(previous),
        "path": str(index_path),
        "latest_analyzed_at": latest,
        "items": len(current),
        "total_items": len(normalized),
        "archives": len(archives),
        "new_items": [
            {key: item[key] for key in ("id", "headline", "target_name", "published_at", "url")}
            for item in new_items[:50]
        ],
    }


def _public_item(row: Mapping[str, Any]) -> dict[str, Any]:
    url = str(row.get("canonical_url") or row.get("url") or "")
    linked_url = canonicalize_url(str(row.get("linked_url") or ""))
    return {
        "id": str(row["id"]),
        "headline": str(row.get("headline") or row.get("title") or "未命名信息"),
        "title": str(row.get("title") or row.get("headline") or "未命名信息"),
        "url": url,
        "linked_url": linked_url,
        "published_at": str(row["published_at"]),
        "analyzed_at": str(row.get("analyzed_at") or ""),
        "target_slug": str(row.get("target_slug") or ""),
        "target_name": str(row.get("target_name") or row.get("target_slug") or "未知目标"),
        "channel_slug": str(row.get("channel_slug") or ""),
        "channel_name": str(row.get("channel_name") or row.get("channel_slug") or "未知频道"),
        "importance": int(row.get("importance") or 1),
        "summary": str(row.get("summary") or ""),
        "key_change": str(row.get("key_change") or ""),
        "why_it_matters": str(row.get("why_it_matters") or ""),
        "company_impact": str(row.get("company_impact") or ""),
        "topics": _array(row.get("topics_json")),
        "watch_next": _array(row.get("watch_next_json")),
        "evidence": _array(row.get("evidence_json")),
        "sources": [{
            "target_name": str(row.get("target_name") or row.get("target_slug") or "未知目标"),
            "channel_name": str(row.get("channel_name") or row.get("channel_slug") or "未知频道"),
            "url": url,
        }],
    }


def _array(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge exact URLs and social posts that explicitly link a collected article.

    Text similarity is deliberately not used here: two posts from the same target
    can discuss adjacent stories with similar vocabulary. A card/link edge is
    deterministic evidence that the social post is a retelling of the article.
    """
    parents = list(range(len(items)))

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        a, b = root(left), root(right)
        if a != b:
            parents[max(a, b)] = min(a, b)

    by_url: dict[str, int] = {}
    for index, item in enumerate(items):
        url = canonicalize_url(item["url"]).casefold()
        if url:
            if url in by_url:
                union(index, by_url[url])
            else:
                by_url[url] = index
    for index, item in enumerate(items):
        linked = canonicalize_url(item.get("linked_url", "")).casefold()
        if linked and linked in by_url:
            union(index, by_url[linked])

    groups: dict[int, list[dict[str, Any]]] = {}
    for index, item in enumerate(items):
        groups.setdefault(root(index), []).append(item)

    selected = []
    for group in groups.values():
        linked_targets = {canonicalize_url(item.get("linked_url", "")).casefold() for item in group}
        primary = max(group, key=lambda item: (
            canonicalize_url(item["url"]).casefold() in linked_targets,
            item["importance"], item["analyzed_at"], item["id"],
        )).copy()
        sources = _unique_objects(
            source for item in group for source in item.get("sources", [])
        )
        if len(sources) > 1:
            primary["sources"] = sources
            primary["evidence"] = _unique_objects(
                evidence for item in group for evidence in item.get("evidence", [])
            )
            primary["topics"] = list(dict.fromkeys(
                topic for item in group for topic in item.get("topics", [])
            ))
            primary["watch_next"] = list(dict.fromkeys(
                value for item in group for value in item.get("watch_next", [])
            ))
            primary["merged_item_ids"] = sorted(item["id"] for item in group)
        else:
            primary.pop("sources", None)
        primary.pop("linked_url", None)
        selected.append(primary)
    return sorted(selected, key=lambda value: (value["published_at"], value["id"]), reverse=True)


def _unique_objects(values) -> list[dict[str, Any]]:
    selected = []
    seen = set()
    for value in values:
        if not isinstance(value, Mapping):
            continue
        key = canonicalize_url(str(value.get("url") or "")) or json.dumps(value, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        selected.append(dict(value))
    return selected


def _repository_root(path: Path) -> Path:
    for directory in (path.resolve().parent, *path.resolve().parents):
        if (directory / "hugo.toml").exists() and (directory / "intelligence").is_dir():
            return directory
    raise RuntimeError("cannot locate Hugo repository root")


def _read_json(path: Path) -> Mapping[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, Mapping) else {}


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
