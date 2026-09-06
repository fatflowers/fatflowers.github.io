# Midday High Signals

目的：按 Asia/Shanghai 时区，在 13:00 仅当早报后至 12:45 出现 importance ≥ 4 的新信号时生成并自动发布午报。

执行仓库日报编辑规则：最多 3 个重点与 5 条快讯，每条紧邻原文。无可核验日期或正文、目录页、旧闻和模板化分析不得出刊；没有合格新事件则跳过。

1. 按 `analyze-pending.md` 补齐窗口内待分析条目。
2. 执行 `intelligence/scripts/intelctl-secure report generate --edition midday`。
3. 若无 importance ≥ 4 的未发布条目，将 Run 标记 `skipped`，原因写 `importance_threshold_not_met`，保持静默且不创建空文章。
4. 若达到阈值，完成 evidence、敏感信息、Front Matter、Hugo build、Git diff 和变更路径检查。
5. generate 返回 `ready` 后，执行 `intelligence/scripts/intelctl-secure report publish --edition midday --execute --push --published-url <根据返回 path 生成的 fatflowers.github.io URL>`；失败则保留 draft 并创建或更新 Issue。
6. 只在实际发布、失败或需要用户操作时创建/更新 Issue；普通 skipped 不通知。

午报必须设置 `hiddenInHomeList: true`，已在早报使用的条目不得重复发布。
