# Catalog Change

目的：把用户关于目标、频道、标签或频率的自然语言请求转成可审计配置变更。

1. 读取 `catalog.yaml`、`mcp-tools.yaml`、`schedules.yaml` 与线上状态。
2. 列出精确匹配对象；批量请求必须展示完整目标/频道清单。
3. 用 `intelligence/scripts/intelctl-secure` 执行 dry-run，生成 before/after。
4. 新频道创建为 disabled。优先复用 verified binding；没有合适 binding 才进入工具发现。
5. 最小只读采集和输出契约测试通过后才能启用频道。
6. 运行 `intelligence/scripts/intelctl-secure catalog validate`，随后执行 `catalog sync`。
7. 改变调度时只更新 Multica Autopilot；`schedules.yaml` 是声明来源，不再安装 launchd。
8. 写入 audit event，提交相关配置，并返回 Git commit 与下一次运行时间。

任何步骤失败均停止后续步骤。删除请求默认 disable，不物理删除历史记录。
