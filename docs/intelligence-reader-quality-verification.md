# 情报报告读者质量修复验收

更新：2026-09-06。状态：未完成 / 进行中 / 已完成。

| 任务 | 状态 | 已有证据与验收边界 |
| --- | --- | --- |
| 补抓原文，而非只分析标题或目录 | 已完成 | 新增 research discover/run/hydrate/ingest；五条正文在 D1 完成版本绑定分析，Run `d89a5f3d-96f2-4bb8-9c9d-07544a49e8d6`。HTTP 失败回退捕获原生 MCP 返回，不接受模型重写的抓取结果。 |
| 修订 9 月 6 日早报 | 已完成 | [公开早报](https://fatflowers.github.io/zh/posts/intelligence/2026-09-06-morning/)：3 条重点、2 条快讯；逐条原文、真实日期、近期补读、具体行动。线上完整内容指纹已验证。 |
| 保留更正历史与已刊事件记录 | 已完成 | 正文提交 `8ead1739f5a883d81af55d103200978947979b05`；D1 审计 `0a0b431f-8c27-4dcf-a3bd-085f69dc465f`，原子替换五条 report_items，旧正文与关联保存在审计中。 |
| 避免历史记录挤占新文章处理额度 | 已完成 | 新发现的正文链接优先，同时按目标轮转；Worker SQL 回归测试覆盖新链接优先和跨目标公平。 |
| 同一期重复执行不报假故障 | 已完成 | 生产 Run `3d43f227-572d-4361-9458-646c3877ca0e` 返回 skipped / already_published。 |
| 推送成功不等于页面上线 | 已完成 | 新流水线在标记 published 前校验 HTTPS 页面内的完整文章指纹；测试拒绝旧页面 HTTP 200，允许部署延迟后的正确页面与安全重试。 |
| 中文阅读时长与首页策略 | 已完成 | Hugo 构建显示约 1,208 字 / 3 分钟；普通日报 hiddenInHomeList=true，周报可进入首页。 |
| 更新 Multica Cloud 指令 | 已完成 | Agent、Skill 和 7 个 Autopilot 说明已逐一回读，与仓库匹配；正文版本、覆盖缺口、逐条引用和发布验收规则同步。 |
| 恢复调度并验收新版无人值守分析 | 已完成 | 七个 Autopilot 已逐一回读为 active。唯一手动验收执行 `01a074d6-dacb-793e-a766-47c2b0af6ead` 的运行任务 `01a074d6-dacf-7bd3-b061-e73c4ffcef13` 于 03:57:44 UTC 完成；D1 分析 Run `264cf49b-4f73-4e81-895c-8ab0def6f04d` 实际 succeeded，15 条成功、0 错误。运行报告五目标入口检查及处理 30 条研究候选；37 条尚待补抓、无法核验日期的页面明确排除，不将其说成无更新。此项验证执行链路，不代替以下读者质量验收。 |
| reader-v4：普通读者视角与已刊事实对比 | 已完成 | 生产 pending 接口已验证返回五条已刊事件，Worker/CLI 测试覆盖透传。当前会话逐条读取 15 条原文并对照五条已刊事实，编辑修正实际入库 Run `7c5894fe-9ad7-442b-b4a8-c15e0f320dc0` 成功。实际 build_report 午报预览仅选中 OpenAI 第一方回应和 Anthropic 形式化证明声明；重复发布宣传、Wiki 短评及 Blender 转述不再入选，正文没有默认 Aisa 身份，每条原文紧邻信息。reader-v4 Autopilot 描述已回读匹配。本次复核由当前会话完成，不声称另一模型或新增无人值守重分析完成。 |

## 质量边界

- 当前关注 OpenAI、Anthropic、Composio、Simon Willison、MCP Ecosystem；不是全行业完整覆盖。
- coverage 是库存计数，不证明本轮检查完整。入口检查失败、正文不足、日期不明和确无合格更新必须区分。
- 不设最低条数、不为凑数降低标准。近三个日历日的未刊内容可补读，但不能冒充当天新发布。
- 同 canonical URL 的跨频道已刊内容排除；同一文档后续具有独立内容变化证据的事件仍可入选。
- Python 全套 207 项、Worker 24 项测试和 Hugo 构建通过；自动运行证据与人工编辑复核证据分别记录在上表，不相互替代。
- 正文版本部署 [GitHub Actions 34009806518](https://github.com/fatflowers/fatflowers.github.io/actions/runs/34009806518) 成功，后续队列修复部署 [34010012627](https://github.com/fatflowers/fatflowers.github.io/actions/runs/34010012627) 成功；更正正文与 D1 保持一致。下一期预览不提前公开发布，交由正常调度处理。
