---
title: "Hermes 的自动 skill：方向对，但一次经历不该直接落盘"
date: 2026-09-22
tags: ["Hermes", "Agent", "Skill", "程序性记忆"]
categories: ["Original Tech"]
---

> 本文整理自与 ChatGPT 的一次讨论。问题是：Hermes Agent 这种、执行完 session 就触发创建 skill 的机制，真的好吗？下面按 [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) 当前 `main` 上的实现来写。

自动从执行经验里长出 skill，这个方向是好的。Hermes 现在的默认机制偏激进。它不是「每次 session 跑完就总结成一条 skill」，但一次复杂经历仍然可以在没有复用验证的情况下，直接变成持久的程序性记忆。

## 它实际在什么时候动手

固定流程不是：

```text
session end → create skill
```

更接近：

```text
对话循环里的工具迭代累计
  → _iters_since_skill >= creation_nudge_interval
  → 这一轮有最终回复，且没有被打断
  → 主回复已经交付
  → 后台 review agent 拿对话快照再看一遍
  → 更新已有 skill / 加参考文件 / 新建 skill / 什么都不写
```

[`turn_finalizer.py`](https://github.com/NousResearch/hermes-agent/blob/main/agent/turn_finalizer.py) 在回合收尾时检查：

```python
agent._skill_nudge_interval > 0
and agent._iters_since_skill >= agent._skill_nudge_interval
and "skill_manage" in agent.valid_tool_names
```

过线之后才 `_spawn_background_review(..., review_skills=True)`。还要同时满足：已经有 `final_response`、这轮没有被打断、没有 `skip_background_review`。cron 一类没有人在环里的运行会把后台 review 关掉。注释写明，review 放在交付之后，避免和用户正在等的任务抢资源。

计数器本身跨 turn 累积。[`turn_context.py`](https://github.com/NousResearch/hermes-agent/blob/main/agent/turn_context.py) 把 `_iters_since_skill` 排除在每轮重置之外。[`turn_iteration_prep.py`](https://github.com/NousResearch/hermes-agent/blob/main/agent/turn_iteration_prep.py) 在每次工具迭代上加一。两种情况会清零：这轮触发了 review，或者前台真的调用了 `skill_manage`。

默认阈值在 [`agent_init.py`](https://github.com/NousResearch/hermes-agent/blob/main/agent/agent_init.py)：`skills.creation_nudge_interval`，缺省是 **10**。重度工具任务一轮就能撞上。

触发条件因此很粗。它不要求「同一类 workflow 成功执行过 3 次」，也不要求「检测到相同任务再次出现」。工具迭代够多，就值得让另一个 LLM 看一眼要不要学点什么。

## 写 skill 的不是主 Agent

后台是单独的 review agent。它拿的是对话快照，用 `_SKILL_REVIEW_PROMPT` 做判断。fork 自己的 memory nudge 和 skill nudge 都被设成 0，避免 review 再触发 review。走另一套模型时，更早的轮次会被压成摘要，最近一段保持原文，所以 reviewer 看到的不一定是完整逐字稿。

prompt 对「什么才配叫 skill」已经写得很具体。目标是 class-level：一类工作的步骤、命令、顺序和坑。名字不能是某个 PR 号、某条报错、某个库名，或者 `fix-X` / `debug-Y` 这种只对今天这场任务有意义的词。明确禁止写进去的包括：

- 缺二进制、没装包、凭证没配好这类环境故障
- 「某工具不能用」这种否定句，以免以后环境修好了 agent 还在拒绝
- 会话中途已经恢复的瞬时错误
- 一次性任务叙述，比如「总结今天的市场」「分析这个 PR」
- 没找到可用方法就结束的失败过程，不能包装成可靠流程

更新顺序也是先改旧的：当前加载且由 curator 管理的 skill，然后已有的 umbrella，然后 `references/`、`templates/`、`scripts/`，最后才新建一条 class-level skill。

这些约束里，有一部分已经不是 prompt。[`skill_manager_guards.py`](https://github.com/NousResearch/hermes-agent/blob/main/tools/skill_manager_guards.py) 会直接拒绝后台 review 对下面这些 skill 的写入：

- bundled
- hub 安装的
- `skills.external_dirs` 里的
- `hermes curator pin` 钉住的
- 没有 `created_by: "agent"` 的用户 skill，包括没有用量记录的

改已有 `SKILL.md` 或覆盖已有附属文件之前，必须在这次 review 里 `skill_view` 过目标。对话记录里引用过的旧内容不算。没读就写，工具返回拒绝。

Curator 负责事后清理。默认 `interval_hours: 168`（7 天），并且要空闲满 `min_idle_hours`（2 小时）才跑，不是定时 cron。确定性的部分一直开着：14 天不用标 stale，30 天归档到 `~/.hermes/skills/.archive/`，不直接删除。用 LLM 做合并、改 umbrella 的 consolidation 默认关着，要 `curator.consolidate: true` 才开。

所以现在已经不是「生成了就永远堆着」。

## 激进的是晋升，不是架构

`_SKILL_REVIEW_PROMPT` 开头就是：

> Be ACTIVE — most sessions produce at least one skill update, even if small. A pass that does nothing is a missed learning opportunity, not a neutral outcome.

后面允许说 `Nothing to save.`，但同一段又写：这是可选项，不该是默认。先验是「想办法学一点」，不是「证据够了才学」。

LLM 很会事后把一次成功收成步骤。部署时命令 A 失败、查了文档、换了命令 B、改了配置、最后成功，reviewer 很容易写成「部署 X 必须按 1、2、3」。真实原因可能只是这台机器缺一个依赖，或者这个 API 版本刚好如此。于是偶然性进了 `SKILL.md`，以后几十次同类任务都会按这条走。这是自我强化的错误。

prompt 里的禁止项挡的是常见坏形状，挡不住「这次碰巧成功，被写成长期规则」。那是经验验证问题，不是措辞问题。

另一个错位是：启动 review 的信号，和值得写成 skill 的信号，不是同一个。

工具迭代数是弱代理。查 12 个网页、打 15 次工具、只是看一次新闻，很容易过阈值。用户第三次纠正「我们公司的 PR 都必须先跑 integration test」，可能只打了 1 次工具，更该留下，却要等计数器慢慢凑满。prompt 确实把用户对流程、风格、格式的纠正定义成一等信号，但那只影响 review 开始之后看什么。要不要启动，仍然看工具迭代够不够。

第三个问题是默认会写。[`skills.md`](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/skills.md) 写明：

```yaml
skills:
  write_approval: false     # false = write freely (default) | true = require approval
```

`config_defaults.py` 里同样是 `False`。打开之后，`skill_manage` 的 create / edit / patch / delete 和附属文件改动都先落到 `~/.hermes/pending/skills/`，再用 `/skills pending`、`/skills diff`、`/skills approve`、`/skills reject` 决定。这道门盖住前台和后台。默认关着，所以默认路径是：

```text
任务做到工具迭代过线
  → 后台 LLM 认为值得学
  → 直接改持久 skill
```

记错「用户喜欢短回答」通常只影响语气。记错「做 Kubernetes 部署必须先做 X」，会变成以后每次都执行的步骤。

## 我会借架构，改晋升

前台执行、后台反思、程序性记忆、按需加载，这条链路比把历史对话全塞进上下文干净。所有权、读后写、pin、以及 curator 的确定性归档，也已经是代码，不只是提示。

缺的是「候选」和「正式 skill」之间的那一层。我不会让一次复杂经历直接等于 `SKILL.md`。

```text
一次复杂经历
  → lesson candidate
  → 去重、并入已有候选
  → 下次同类任务拿出来用
  → 复用成功，置信度上升
  → 再成功一到两次
  → 晋升为持久 skill
```

启动反思也可以换成更贴学习价值的信号：同一类流程再次出现、用户纠正了步骤、失败之后有一次被验证的成功、或者用户明确说「记住这个流程」。工具迭代数只做辅助，避免安静的短任务永远进不了反思，也避免一次大搜索就换来一条持久规则。

`write_approval: true` 是今天就能用的刹车。长期跑的 coding agent 要防的，主要不是「它学得不够」，而是「它把错误经验学进去」。Hermes 已经把爆炸式增长和越权改写挡住了一部分。skill 要不要从候选晋升成长期规则，这层还没有被真正做出来。
