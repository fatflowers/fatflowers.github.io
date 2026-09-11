# Collect Due Channels

目的：每 30 分钟采集所有到期的公共频道，优先使用免费公共端点，并由本机 OpenCLI 处理浏览器页面和 X。

1. 在仓库根目录执行 `intelligence/scripts/intelctl-secure collect plan --due`，创建并记录 `pipeline_run_id`。
2. 执行 `intelligence/scripts/intelctl-secure collect local --due`。本地路由固定为 RSS/官方 API 优先；网页频道先普通 HTTP，失败或正文不足才调用 `opencli web read`；X 频道调用 `opencli twitter search 'from:<handle>' --product live`。
3. OpenCLI 必须使用 `--window background`、临时 tab，并输出结构化 JSON 或 Markdown；不得执行发帖、回复、点赞、关注等写操作。
4. X 每轮从 Latest 搜索的最新结果开始，使用 tweet ID 去重。若 50 条窗口仍未覆盖上一水位，必须失败并保留旧游标，不得假装采集完整。
5. 当前启用频道不得调用 `get_twitter_user_tweet_timeline`、`post_firecrawl_scrape` 或 `post_firecrawl_map`；OpenCLI 失败时记录具体退出码，不切换到付费路线。
6. 列表页只是发现入口；后续 `research discover/run` 补抓正文后才可进入分析与出刊。
7. 只有入库成功后才提交频道游标；失败时保留原游标。
8. 同一频道连续失败不足三次时只记录；达到三次时创建或更新健康 Issue。OpenCLI Browser Bridge、Chrome 或 X 登录错误立即失败。
9. 没有到期频道或没有新增条目时标记 `skipped`，不生成空报告。
10. 返回运行状态、`pipeline_run_id`、成功/失败频道数、新增条目数和下一次运行时间。

所有命令都必须从项目工作目录执行。不得输出 MCP OAuth Token 或 Worker API Token。
