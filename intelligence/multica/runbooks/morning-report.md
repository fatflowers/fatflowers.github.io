# Morning Intelligence

目的：按 Asia/Shanghai 时区，在 08:30 为前一日晚报后至当日 08:15 的窗口生成中文早报并自动发布。

1. 查询窗口内条目及分析状态；若仍有待分析条目，先完整执行 `analyze-pending.md`。
2. 执行 `intelligence/scripts/intelctl-secure report generate --edition morning`。
3. 没有有效新内容时将 Run 标记为 `skipped`，原因写 `no_effective_new_items`，不创建空文章。
4. 有内容时检查关键事实的 evidence URL、推断标记、重复项和敏感信息。
5. generate 返回 `ready` 后，执行 `intelligence/scripts/intelctl-secure report publish --edition morning --execute --push --published-url <根据返回 path 生成的 fatflowers.github.io URL>`。成功路径为 `draft → validating → ready → published`。
6. 发布失败时停止、保留 draft、将状态设为 `failed`，创建或更新当前 Multica Issue。
7. 成功时在 Issue 中记录 `pipeline_run_id`、`report_id`、Git commit、published URL、来源数量和最高重要度。

早报必须设置 `hiddenInHomeList: true`。
