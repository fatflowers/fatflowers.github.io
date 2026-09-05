---
name: personal-intelligence-operator
description: 通过 intelctl 管理个人公开情报系统的目标、频道、标签、采集、分析、报告、发布、调度和运行诊断。
---

# Personal Intelligence Operator

当用户用自然语言要求关注目标、增加或暂停频道、调整标签或频率、立即采集、生成报告、解释失败或查询状态时使用本 Skill。

完整安全边界和回应契约由 Agent instructions 提供。仓库命令统一通过 `intelligence/scripts/intelctl-secure` 执行；不得读取或输出其本机凭据来源。

## 意图映射

| 意图 | 受限命令 |
|---|---|
| 查看目标 | `intelctl target list/show` |
| 新增或修改目标 | `intelctl target add/update/disable` |
| 查看频道 | `intelctl channel list` |
| 新增或修改频道 | `intelctl channel add/bind-tool/test/set-interval/disable` |
| 管理标签 | `intelctl tag list/add/attach/detach` |
| 校验和同步 | `intelctl catalog validate/sync` |
| MCP 鉴权状态 | `intelctl mcp auth status` |
| 新能力发现 | `intelctl mcp discover/inspect/test/binding verify` |
| 采集 | `intelctl collect plan/local/ingest` |
| 分析 | `intelctl analyze pending/ingest` |
| 报告 | `intelctl report generate/publish` |
| 状态诊断 | `intelctl status`、`intelctl run show/retry` |
| 调度变更 | 更新 `schedules.yaml` 与对应 Multica Autopilot trigger |

禁止拼接任意 SQL、任意 shell 或未列入上述范围的生产操作。

## 常见映射

- “关注 X，它属于竞品”：创建 disabled 目标/频道配置，附加标签，校验后同步。
- “给 X 增加 Twitter”：解析账号到 numeric userId，固定 `twitter-user-timeline-v1`，测试通过后启用。
- “暂停所有 Twitter”：先列出命中的全部频道，再批量 disable；不删除历史数据。
- “科技大厂 Blog 每小时检查”：按目标标签筛选 Blog，dry-run 后改为 60 分钟并应用调度。
- “立即检查 Agent 资源层”：采集带 `agent-resource-layer` 标签目标的已启用频道。
- “重新生成晚报但不要发布”：生成 ad-hoc draft 并返回预览，不调用 publish。
- “为什么没有早报”：查询对应 Run、窗口条目、分析状态与策略判断，给出 skipped/failed 的证据。

## 频道新增步骤

1. `channel add` 创建为 disabled。
2. 优先选择已有固定 binding。
3. 无适用 binding 时才运行 MCP discover/inspect。
4. 最小只读调用，保存脱敏响应结构。
5. 输出适配器和契约测试通过后 `binding verify`。
6. `channel test` 通过后启用、同步、审计。

## 报告规则

- morning/evening：有有效新内容才生成。
- midday：只有 importance ≥ 4 才生成。
- weekly：每周固定生成，做跨事件关联。
- ad-hoc：默认 draft，只有用户明确说发布才发布。
- 自动报告校验通过后直接提交并推送 `main`。
