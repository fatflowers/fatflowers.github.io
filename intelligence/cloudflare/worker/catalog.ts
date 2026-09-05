import { ApiError, enumString, jsonText, optionalBoolean, optionalString, requireArray, requireNumber, requireObject, requireString } from "./http.ts";
import type { ApiResponse, AuthContext, D1PreparedStatement, JsonObject } from "./types.ts";

const TARGET_TYPES = ["company", "product", "person", "project", "topic"] as const;
const PRIORITIES = ["low", "normal", "high", "critical"] as const;

function boolInt(value: unknown, fallback: boolean): number {
  return optionalBoolean(value, fallback) ? 1 : 0;
}

export async function getCatalog({ env }: AuthContext): Promise<ApiResponse> {
  const statements = [
    env.DB.prepare("SELECT * FROM targets ORDER BY slug"),
    env.DB.prepare("SELECT * FROM channels ORDER BY target_id, slug"),
    env.DB.prepare("SELECT * FROM tags ORDER BY tag_type, slug"),
    env.DB.prepare("SELECT target_id, tag_id FROM target_tags ORDER BY target_id, tag_id"),
    env.DB.prepare("SELECT channel_id, tag_id FROM channel_tags ORDER BY channel_id, tag_id"),
  ];
  const [targets, channels, tags, targetTags, channelTags] = await env.DB.batch(statements);
  return {
    status: 200,
    body: {
      targets: targets.results ?? [],
      channels: channels.results ?? [],
      tags: tags.results ?? [],
      target_tags: targetTags.results ?? [],
      channel_tags: channelTags.results ?? [],
    },
  };
}

export async function syncCatalog({ env, body }: AuthContext): Promise<ApiResponse> {
  const envelope = requireObject(body);
  const payload = envelope.catalog === undefined ? envelope : requireObject(envelope.catalog, "catalog");
  const now = new Date().toISOString();
  const mode = payload.mode === undefined ? "replace" : enumString(payload.mode, "mode", ["replace", "merge"]);
  const targets = requireArray(payload.targets, "targets", 500).map((entry, index) => {
    const value = requireObject(entry, `targets[${index}]`);
    return {
      id: requireString(value.id, `targets[${index}].id`, { max: 128 })!,
      slug: requireString(value.slug, `targets[${index}].slug`, { max: 128 })!,
      name: requireString(value.name, `targets[${index}].name`, { max: 256 })!,
      targetType: enumString(value.target_type ?? value.type, `targets[${index}].target_type`, TARGET_TYPES),
      description: optionalString(value.description, `targets[${index}].description`, 2_000),
      priority: value.priority === undefined ? "normal" : enumString(value.priority, `targets[${index}].priority`, PRIORITIES),
      enabled: boolInt(value.enabled, true),
      createdAt: optionalString(value.created_at, `targets[${index}].created_at`, 64) ?? now,
    };
  });
  const tags = requireArray(payload.tags, "tags", 1_000).map((entry, index) => {
    const value = requireObject(entry, `tags[${index}]`);
    return {
      id: requireString(value.id, `tags[${index}].id`, { max: 128 })!,
      slug: requireString(value.slug, `tags[${index}].slug`, { max: 128 })!,
      name: requireString(value.name, `tags[${index}].name`, { max: 256 })!,
      tagType: requireString(value.tag_type ?? value.type, `tags[${index}].tag_type`, { max: 64 })!,
      createdAt: optionalString(value.created_at, `tags[${index}].created_at`, 64) ?? now,
    };
  });
  const channels = requireArray(payload.channels, "channels", 2_000).map((entry, index) => {
    const value = requireObject(entry, `channels[${index}]`);
    return {
      id: requireString(value.id, `channels[${index}].id`, { max: 128 })!,
      targetId: requireString(value.target_id, `channels[${index}].target_id`, { max: 128 })!,
      slug: requireString(value.slug, `channels[${index}].slug`, { max: 128 })!,
      name: requireString(value.name, `channels[${index}].name`, { max: 256 })!,
      channelType: requireString(value.channel_type ?? value.type, `channels[${index}].channel_type`, { max: 64 })!,
      collectorType: requireString(value.collector_type ?? value.collector, `channels[${index}].collector_type`, { max: 64 })!,
      url: optionalString(value.url, `channels[${index}].url`, 4_096),
      handle: optionalString(value.handle, `channels[${index}].handle`, 256),
      intervalMinutes: value.interval_minutes === undefined ? 60 : requireNumber(value.interval_minutes, `channels[${index}].interval_minutes`, 5, 43_200),
      priority: value.priority === undefined ? "normal" : enumString(value.priority, `channels[${index}].priority`, PRIORITIES),
      enabled: boolInt(value.enabled, true),
      toolBinding: optionalString(value.tool_binding, `channels[${index}].tool_binding`, 256),
      configJson: jsonText(value.config, `channels[${index}].config`),
      createdAt: optionalString(value.created_at, `channels[${index}].created_at`, 64) ?? now,
    };
  });
  const targetTags = requireArray(payload.target_tags ?? [], "target_tags", 5_000).map((entry, index) => {
    const value = requireObject(entry, `target_tags[${index}]`);
    return [
      requireString(value.target_id, `target_tags[${index}].target_id`, { max: 128 })!,
      requireString(value.tag_id, `target_tags[${index}].tag_id`, { max: 128 })!,
    ];
  });
  const channelTags = requireArray(payload.channel_tags ?? [], "channel_tags", 10_000).map((entry, index) => {
    const value = requireObject(entry, `channel_tags[${index}]`);
    return [
      requireString(value.channel_id, `channel_tags[${index}].channel_id`, { max: 128 })!,
      requireString(value.tag_id, `channel_tags[${index}].tag_id`, { max: 128 })!,
    ];
  });

  assertUnique(targets, "id", "targets");
  assertUnique(targets, "slug", "targets");
  assertUnique(tags, "id", "tags");
  assertUnique(tags, "slug", "tags");
  assertUnique(channels, "id", "channels");
  assertUnique(channels, "slug", "channels");

  const statements: D1PreparedStatement[] = [];
  if (mode === "replace") {
    statements.push(env.DB.prepare("UPDATE channels SET enabled = 0, updated_at = ?").bind(now));
    statements.push(env.DB.prepare("UPDATE targets SET enabled = 0, updated_at = ?").bind(now));
  }
  for (const tag of tags) {
    statements.push(env.DB.prepare(`INSERT INTO tags (id, slug, name, tag_type, created_at)
      VALUES (?, ?, ?, ?, ?)
      ON CONFLICT(id) DO UPDATE SET slug=excluded.slug, name=excluded.name, tag_type=excluded.tag_type`)
      .bind(tag.id, tag.slug, tag.name, tag.tagType, tag.createdAt));
  }
  for (const target of targets) {
    statements.push(env.DB.prepare(`INSERT INTO targets
      (id, slug, name, target_type, description, priority, enabled, created_at, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(id) DO UPDATE SET slug=excluded.slug, name=excluded.name,
      target_type=excluded.target_type, description=excluded.description, priority=excluded.priority,
      enabled=excluded.enabled, updated_at=excluded.updated_at`)
      .bind(target.id, target.slug, target.name, target.targetType, target.description, target.priority, target.enabled, target.createdAt, now));
  }
  for (const channel of channels) {
    statements.push(env.DB.prepare(`INSERT INTO channels
      (id, target_id, slug, name, channel_type, collector_type, url, handle, interval_minutes,
       priority, enabled, tool_binding, config_json, created_at, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(id) DO UPDATE SET target_id=excluded.target_id, slug=excluded.slug, name=excluded.name,
      channel_type=excluded.channel_type, collector_type=excluded.collector_type, url=excluded.url,
      handle=excluded.handle, interval_minutes=excluded.interval_minutes, priority=excluded.priority,
      enabled=excluded.enabled, tool_binding=excluded.tool_binding, config_json=excluded.config_json,
      updated_at=excluded.updated_at`)
      .bind(channel.id, channel.targetId, channel.slug, channel.name, channel.channelType,
        channel.collectorType, channel.url, channel.handle, channel.intervalMinutes, channel.priority,
        channel.enabled, channel.toolBinding, channel.configJson, channel.createdAt, now));
  }
  if (mode === "replace") {
    statements.push(env.DB.prepare("DELETE FROM target_tags"));
    statements.push(env.DB.prepare("DELETE FROM channel_tags"));
  }
  for (const [targetId, tagId] of targetTags) {
    statements.push(env.DB.prepare("INSERT OR IGNORE INTO target_tags (target_id, tag_id) VALUES (?, ?)").bind(targetId, tagId));
  }
  for (const [channelId, tagId] of channelTags) {
    statements.push(env.DB.prepare("INSERT OR IGNORE INTO channel_tags (channel_id, tag_id) VALUES (?, ?)").bind(channelId, tagId));
  }
  if (statements.length > 0) await env.DB.batch(statements);

  return {
    status: 200,
    body: {
      mode,
      synced: {
        targets: targets.length,
        channels: channels.length,
        tags: tags.length,
        target_tags: targetTags.length,
        channel_tags: channelTags.length,
      },
      synced_at: now,
    },
  };
}

function assertUnique<T extends JsonObject>(values: T[], key: keyof T, name: string): void {
  const seen = new Set<unknown>();
  for (const value of values) {
    if (seen.has(value[key])) throw new ApiError(400, "duplicate_catalog_key", `${name} contains a duplicate ${String(key)}`);
    seen.add(value[key]);
  }
}

export async function getDueChannels({ env, url }: AuthContext): Promise<ApiResponse> {
  const rawLimit = url.searchParams.get("limit");
  const limit = rawLimit === null ? 100 : requireNumber(Number(rawLimit), "limit", 1, 500);
  const now = url.searchParams.get("now") ?? new Date().toISOString();
  const targetId = url.searchParams.get("target_id");
  const rows = await env.DB.prepare(`SELECT c.*, t.slug AS target_slug, t.name AS target_name
    FROM channels c JOIN targets t ON t.id = c.target_id
    WHERE c.enabled = 1 AND t.enabled = 1
      AND (? IS NULL OR c.target_id = ?)
      AND (c.last_checked_at IS NULL OR datetime(c.last_checked_at, '+' || c.interval_minutes || ' minutes') <= datetime(?))
    ORDER BY CASE c.priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END,
             COALESCE(c.last_checked_at, '1970-01-01T00:00:00Z')
    LIMIT ?`).bind(targetId, targetId, now, limit).all();
  return { status: 200, body: { channels: rows.results ?? [], as_of: now } };
}
