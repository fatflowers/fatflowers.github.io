# Collect Due Channels

目的：每 30 分钟采集所有到期的公共频道，使用固定工具绑定，不在常规运行中发现或猜测工具。

1. 在仓库根目录执行 `intelligence/scripts/intelctl-secure collect plan --due`，创建并记录 `pipeline_run_id`。
2. 对计划中的 RSS、HTTP 和 GitHub 频道，执行 `intelligence/scripts/intelctl-secure collect local --due`，由确定性 collector 执行并入库。
3. 对计划中的 MCP 频道，读取 `intelligence/config/mcp-tools.yaml` 中已经 `verified` 的 binding。
4. 使用 AIsa MCP 的 `AISA_BATCH_USE` 调用 binding 中固定的 `tool_name` 和渲染后参数；禁止搜索替换工具。路由需要有效 search_id/Schema 时只发现同一个固定工具。Twitter 每轮从最新页开始，不能把上一轮 pagination cursor 当成新一轮起点。
5. 将原生 MCP 响应原样保存至任务专用临时文件，或确定性提取已确认的响应字段，再执行 `intelligence/scripts/intelctl-secure collect ingest --channel <slug> --input <tempfile>`。禁止人工重打、模型补写或基于记忆构造“返回值”。仅清理本次准确路径的临时文件，不使用宽泛目录删除。列表页只是发现入口；后续 `research discover/run` 补抓正文后才可进入分析与出刊。
6. 只有入库成功后才提交频道游标；失败时保留原游标。
7. 同一频道连续失败不足三次时只记录；达到三次时创建或更新健康 Issue。认证或 Schema 错误立即失败，不静默回退。
8. 没有到期频道或没有新增条目时标记 `skipped`，不生成空报告。
9. 返回运行状态、`pipeline_run_id`、成功/失败频道数、新增条目数和下一次运行时间。

所有命令都必须从项目工作目录执行。不得输出 MCP OAuth Token 或 Worker API Token。
