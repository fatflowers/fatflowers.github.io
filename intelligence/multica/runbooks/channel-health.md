# Channel Health Review

目的：每日检查频道健康、调度滞后、MCP 契约状态和关键依赖，只在出现可行动变化时通知。

1. 执行 `intelligence/scripts/intelctl-secure status` 检查本地目录和 Worker/D1 健康；该命令不验证 MCP OAuth、Git remote 或 Hugo。分别用最近真实 MCP 调用结果、`git remote -v` 和 `hugo version` 核对相应依赖，不把 configured 当作已鉴权。
2. 用 `run list/show` 检查最近采集、研究、分析和发布的实际结果，结合 `research coverage` 与 `mcp binding list` 定位缺口。频道状态字段仅在现有接口返回时使用，不能虚构 `status` 未返回的 last_success 等证据。
3. 单次临时错误只记录；同一频道连续失败三次，创建或更新该频道的唯一 Issue。
4. `unauthorized`、`tool_not_found`、Schema 契约失败、所有频道失败、发布链路失败应立即创建或更新 Issue。
5. 工具不存在或 Schema 不兼容时禁用该 binding，不允许定时任务临时猜测替代工具。
6. 恢复成功时在原 Issue 记录恢复，并关闭/标记已恢复；不要另建重复 Issue。
7. 一切正常且无状态变化时保持静默。Multica 任务结果如实记录 succeeded/skipped/failed；CLI 创建的 pipeline Run 由对应命令维护，不猜测不存在的状态修改子命令。

本 Runbook 只能诊断和执行预定义恢复动作，不得自行修改用户的目标、标签、频率或发布规则。
