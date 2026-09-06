# Morning Intelligence

目的：按 Asia/Shanghai 时区，在 08:30 为前一日晚报后至当日 08:15 的窗口生成中文早报并自动发布。

读者版规则：最多 3 个重点和 9 条一句话快讯，每条紧邻原文链接，顶部提供 30 秒速览。不发布历史旧闻、列表/分页/个人主页、未知日期、仅有标题或固定话术分析；不得将抓取时间当成发布时间。没有合格事件时正常跳过，不凑数。

1. 出刊前完整执行 `analyze-pending.md` 的发现、正文补抓与分析流程，并检查五个目标的覆盖。不要仅查询现有分析队列后就声称无消息。允许补读近三个日历日内未在日报发布的有效事件，但必须保留真实日期并标注“近期补读”，不能冒充今天的新消息。
2. 执行 `intelligence/scripts/intelctl-secure report generate --edition morning`。
3. 没有有效新内容时将 Run 标记为 `skipped`，原因写 `no_effective_new_items`，不创建空文章。
4. 有内容时检查关键事实的 evidence URL、推断标记、重复项和敏感信息。
5. generate 返回 `ready` 后，执行 `intelligence/scripts/intelctl-secure report publish --edition morning --execute --push --published-url <根据返回 path 生成的 fatflowers.github.io URL>`。成功路径为 `draft → validating → ready → published`。
6. 发布及重试必须完整执行 `intelligence/multica/runbooks/publication-check.md`。失败时停止、保留现场，创建或更新当前 Multica Issue；不得在 push 后未经部署和正文核验就宣布已上线。
7. 成功时在 Issue 中记录 `pipeline_run_id`、`report_id`、Git commit、published URL、来源数量和最高重要度。

早报必须设置 `hiddenInHomeList: true`。
