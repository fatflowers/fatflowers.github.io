# Evening Intelligence

目的：按 Asia/Shanghai 时区，在 19:00 为当日 08:15 至 18:45 的窗口生成中文晚报并自动发布。

执行仓库日报编辑规则：最多 3 个重点与 5 条快讯，每条紧邻原文。无可核验日期或正文、目录页、旧闻和模板化分析不得出刊；没有合格新事件则跳过。

1. 按 `analyze-pending.md` 补齐窗口内待分析条目，并纳入按策略从早报/午报窗口结转的低价值内容。
2. 执行 `intelligence/scripts/intelctl-secure report generate --edition evening`。
3. 没有有效新内容时将 Run 标记 `skipped`，原因写 `no_effective_new_items`，不创建空文章。
4. 有内容时去除已发布条目，完成全部自动发布门禁。
5. generate 返回 `ready` 后，执行 `intelligence/scripts/intelctl-secure report publish --edition evening --execute --push --published-url <根据返回 path 生成的 fatflowers.github.io URL>`；发布失败则保留 draft 并创建或更新 Issue。
6. 在 Issue 中记录 Run、Report、Commit、URL、来源数量、目标覆盖和结转条目数。

晚报必须设置 `hiddenInHomeList: true`。
