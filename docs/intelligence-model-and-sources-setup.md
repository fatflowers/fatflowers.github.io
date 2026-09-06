# 模型配置与公开信息源页面

更新：2026-09-06。

## 模型

- 项目声明：`intelligence/multica/config.yaml` 的 `agent.model=gpt-5.6-terra`、`agent.thinking_level=medium`。
- Multica Cloud Agent 已回读为相同设置；`custom_args` 仅保留 OpenAI provider，删除了覆盖模型的 Sol 参数。
- 内部 MCP 调用从同一项目文件读取模型，显式传递模型、provider 和 reasoning effort，不依赖主机全局默认。没有自动切换昂贵模型的回退。
- 可选的 `INTELLIGENCE_CODEX_MODEL` / `INTELLIGENCE_CODEX_REASONING_EFFORT`，及 MCP 专用的 `INTELLIGENCE_MCP_MODEL` / `INTELLIGENCE_MCP_REASONING_EFFORT` 仅在显式设置时覆盖；非法配置启动前报错。
- 不修改主机其他项目的全局 Codex 默认值，不重启正在执行的任务；Cloud 设置对新启动任务生效。Cloud 界面与 Git 声明不是双向自动同步，后续修改需要保持一致。
- 真实小请求已返回 OK，启动信息为 Terra / medium；内部桥接使用公开 example.com 实测取得原生 AISA_BATCH_USE 响应（初次未取得结果，复测成功）。未使用生产正文作模型测试输入。

## 公开页面

- 英文 `/sources/`，中文 `/zh/sources/`，均有首页导航入口。
- 每次 Hugo 构建直接读取 `intelligence/config/catalog.yaml`，没有重复维护的页面数据。
- 展示目标、频道、标签、有效启用状态、公开 URL、采集方式、间隔、优先级、层级、回退和部分公开过滤条件，支持关键词与状态筛选。
- 当前为 5 个目标、32 个频道，26 个有效启用、6 个未启用。
- 页面是部署时配置快照，不是实时健康监控；不访问需要凭据的 Worker API。
- 字段白名单排除凭据、内部标识与运行日志；链接剥离查询/片段，拒绝 userinfo、IP 地址、本地域名及非 HTTP(S) 协议。HTML 数据按上下文转义。

## 验证

| 项目 | 状态 | 证据 |
| --- | --- | --- |
| 模型与覆盖参数修复 | 已完成 | Cloud 回读、命令参数回归测试、真实 Terra/medium 小请求及原生 MCP 返回 |
| 页面及安全过滤 | 已完成 | 实际 YAML 构建映射、双语导航、恶意字段/XSS、停用继承和 JS 筛选测试；浏览器预览显示 32 个频道 |
| 部署与线上验证 | 已完成 | 提交 `d7d12c5051d50b775784e9efcf6a0e8ee56fc11e` 的 Pages 部署 `34014357540` 成功；线上中英文页面均核对 5 个目标、32 个频道、26 个启用，全部源链接匹配；首页导航正确，无内部字段。Python 全套 231 项测试通过。 |
