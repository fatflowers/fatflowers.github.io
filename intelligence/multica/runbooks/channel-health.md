# Channel Health Review

目的：每日检查频道健康、调度滞后、MCP 契约状态和关键依赖，只在出现可行动变化时通知。

1. 执行 `intelligence/scripts/intelctl-secure status`，检查 Worker、D1、MCP OAuth、Git remote 和 Hugo 环境。
2. 查询频道的 last_checked、last_success、consecutive_failures、error_code 和绑定状态。
3. 单次临时错误只记录；同一频道连续失败三次，创建或更新该频道的唯一 Issue。
4. `unauthorized`、`tool_not_found`、Schema 契约失败、所有频道失败、发布链路失败应立即创建或更新 Issue。
5. 工具不存在或 Schema 不兼容时禁用该 binding，不允许定时任务临时猜测替代工具。
6. 恢复成功时在原 Issue 记录恢复，并关闭/标记已恢复；不要另建重复 Issue。
7. 一切正常且无状态变化时保持静默，将 Run 标记 `succeeded`。

本 Runbook 只能诊断和执行预定义恢复动作，不得自行修改用户的目标、标签、频率或发布规则。
