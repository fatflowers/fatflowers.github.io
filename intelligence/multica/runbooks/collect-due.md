# Collect Due Channels

目的：每 30 分钟采集所有到期的公共频道，使用固定工具绑定，不在常规运行中发现或猜测工具。

1. 在仓库根目录执行 `intelligence/scripts/intelctl-secure collect plan --due`，创建并记录 `pipeline_run_id`。
2. 对计划中的 RSS、HTTP 和 GitHub 频道，执行 `intelligence/scripts/intelctl-secure collect local --due`，由确定性 collector 执行并入库。
3. 对计划中的 MCP 频道，读取 `intelligence/config/mcp-tools.yaml` 中已经 `verified` 的 binding。
4. 使用 AIsa MCP 的 `AISA_BATCH_USE` 调用 binding 中固定的 `tool_name` 和渲染后参数；禁止调用 `AISA_SEARCH_TOOL` 替换工具。
5. 将每个 MCP 返回值裁剪为固定 adapter 所需字段，暂存在 `/private/tmp/personal-intelligence/`，再执行 `intelligence/scripts/intelctl-secure collect ingest --channel <slug> --input <tempfile>`；成功或失败后均删除临时文件。
6. 只有入库成功后才提交频道游标；失败时保留原游标。
7. 同一频道连续失败不足三次时只记录；达到三次时创建或更新健康 Issue。认证或 Schema 错误立即失败，不静默回退。
8. 没有到期频道或没有新增条目时标记 `skipped`，不生成空报告。
9. 返回运行状态、`pipeline_run_id`、成功/失败频道数、新增条目数和下一次运行时间。

所有命令都必须从项目工作目录执行。不得输出 MCP OAuth Token 或 Worker API Token。
