---
title: "Raft 源码学习：一个 Agent 如何成为长期协作的参与者"
date: 2026-09-26
author: "Simon Sun · AI 撰文"
description: "从身份、消息投递、Runtime、记忆、任务领取和权限边界，学习 Raft 如何把 Agent 组织成持续参与工作的成员。"
tags: ["Agent", "Raft", "源码阅读", "Runtime", "多智能体"]
categories: ["Original Tech"]
layout: raft-study
images: ["/images/raft-source-learning-cover.png"]
cover:
  image: "/images/raft-source-learning-cover.png"
  alt: "Raft 官方分享图：中央 RAFT 标志与周围彩色像素角色"
  width: 1200
  height: 630
  mime: "image/png"
  caption: "图片：[Raft 官方](https://raft.build/)，沿用官网分享图。"
  source: "https://raft.build/og/raft-social-1200x630.png"
ShowToc: false
sourceCommit: "05f7d8fd77d2535f993d5d90b85118438bc18216"
summary: "本文由 AI 撰写，仅用于学习和理解 Raft 源码。从一条频道消息出发，跟踪 Agent 的身份、执行过程、文件记忆、任务协作和凭据边界。"
---

> **本文由 AI 撰写，仅用于学习和理解 Raft 源码。** 内容依据下述代码快照进行静态阅读，文中的流程演示用于解释实现，不代表运行了 Raft 集成环境，也不构成对生产行为的验证。

假设团队里有一位叫 Atlas 的 Agent。今天，它在频道里收到一项排查任务；工作到一半，用户补充了限制；明天，运行它的进程重启了；几天后，另一个 Agent 想接手这项工作。

这些事情看起来都很平常，却会把一个简单的“调用模型、执行工具、返回答案”程序推向不同的问题：Atlas 的身份还在吗？它读到了哪条消息？谁拥有任务？重启以后还能找到先前的结论吗？模型拿到的凭据能访问哪些地方？

[Raft](https://github.com/botiverse/raft-source) 提供了一个可以沿着源码研究这些问题的实现。它把人和 Agent 放进共同的频道、线程、私信与任务系统，同时把 Agent 的执行交给实际机器上的运行时。本文就沿着 Atlas 的一次工作，逐层拆开这些设计。

## 01 · 先确定我们读的是什么 {#starting-point}

这里的 Raft 是 Botiverse 的人机协作产品，与分布式系统中的同名共识算法无关。阅读对象是本地仓库提交 **`05f7d8fd77d2535f993d5d90b85118438bc18216`**。仓库内的 `RELEASE_SOURCE` 另行记录了内部源提交 `d3aae345…` 和导出时间 **2026-09-24 04:40 UTC**；这两个提交标识承担不同用途，本文的源码链接统一固定在公开快照提交上。

先读 [README](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/README.md) 会发现两个重要背景：它把 Agent 描述为拥有身份、记忆和能力范围的持久参与者；这个公开仓库是发布镜像，开发在私有仓库进行。该快照声明采用 FSL-1.1-ALv2，并说明当时不接受外部 PR。许可证的具体条款应以仓库的 [LICENSE](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/LICENSE) 为准。

公开源码降低了研究和检查实现的门槛，这是可以直接观察到的结果。至于公司是否希望借此获客、改变定位或建立生态，只能作为产品分析的假设，不能靠几段代码证明。因此，本文把主要篇幅放在能由实现回答的问题上。

阅读时也要留意：源码仍有一些 `slock` 名称，手册中的个别描述与当前数据结构并不完全一致。遇到分歧，应继续追踪实际读写路径，而不是把名称或说明当成最终事实。

## 02 · 全局地图：谁管理协作，谁执行工作 {#architecture}

Raft 的顶层结构很适合先按职责分成两边。服务端保存协作对象并决定请求能否发生；机器端承载 Agent 的实际执行。Web 和桌面端让人操作这些对象，CLI 则提供人和 Agent 都能使用的入口。

{{< raft-lab kind="architecture" >}}

`packages/server` 里是 API、实时通信、数据持久化与业务服务。这里的 `agents`、`channels`、`messages`、`tasks` 等对象，描述的是共同工作空间中的关系。它需要知道谁能看到哪个频道、消息应该交给谁、任务归谁负责。

`packages/computer` 承担机器端运行环境与安装、管理相关职责；`packages/daemon` 中的常驻程序连接服务端，管理 Agent 的启动、输入、活动与退出。实际模型交互再由各个 runtime driver 适配。数据库中仍能看到 `machineId` 映射到历史列名 `daemon_id`，因此产品名、代码名与存储名要对照着读。

可以把 Server 理解为**协作与控制的一侧**，把 Daemon 理解为**机器上的执行管理者**。这个类比能帮助定位代码，但不能据此假设它具备某个通用集群调度系统的全部能力。比如，`agents.executionMode` 同时存在 `byoc` 和 `cloud`；“执行与业务服务分离”也不等于“只能在用户自己的电脑运行”。

这一结构的价值在于变化可以分层处理：频道权限变了，不应该要求重写模型协议；换一个 runtime，也不应该改变任务的归属模型。代价则是服务端、网络连接、Daemon 和 runtime 都会出现自己的状态，后面必须认真处理这些状态之间的同步。

源码入口：[数据库模型](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/server/src/db/schema.ts#L851)、[AgentOrchestrator](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/server/src/services/agentOrchestrator.ts)、[Daemon core](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/daemon/src/core.ts)。

## 03 · Atlas 是谁：频道、身份、会话与轮次 {#identity}

### Channel 与 runtime session 是两种不同的“会话”

在界面上，我们可能把频道里的聊天也叫会话。但在源码里，**Channel 是多人共享的消息空间，runtime session 是某个 Agent 使用的原生模型上下文**。把两者混为一谈，就容易误以为“进一个频道便新建一个 session”，或者“频道成员共享一段模型上下文”。

| 对象 | 关系与作用 |
| --- | --- |
| Channel | 保存共同可见的消息；一个频道可以加入多位 Agent |
| Agent | 拥有稳定身份；同一 Agent 可以加入多个频道 |
| `agents.sessionId` | 这位 Agent 当前保存的 runtime 会话引用，可为空，也会被替换 |
| Runtime turn | 当前会话中的一轮执行；同一个 session 可以经历多轮 |
| Runtime 进程 | 承载执行；进程重启之后仍可能恢复同一个 session |

`channel_agents` 以 `(channelId, agentId)` 为联合主键，表达频道与 Agent 的多对多关系。`sessionId` 则放在 `agents` 表上，没有放在这张成员关系表里。Daemon 也按 `agentId` 查找当前的 `AgentProcess`，不是按 `(agentId, channelId)` 为每个频道分配一个进程或会话。

下面假设 Atlas 同时加入两个频道，Nova 只加入部署频道：

```text
#deploy ──→ Atlas 的 Inbox ──→ Atlas 当前 session A1
        └─→ Nova  的 Inbox ──→ Nova  当前 session N1
#review ──→ Atlas 的同一个 Inbox ──→ 仍由 Atlas 当前 session A1 处理
```

这表示**来自不同频道的已消费消息可以进入同一位 Agent 的上下文**，并不表示频道全部历史会自动装入模型。消息携带 `channel_id`、目标类型、发送者等信息，Agent 再按需要读取历史、按原消息的 `target` 回复。因此频道的成员与访问边界，也不能当作同一 Agent 内部的模型上下文隔离边界。

这里还有一处同名陷阱：Raft 的讨论线程是协作对象，而 Codex 协议中的 `threadId` 是原生会话 ID；它在这条适配链中对应 `agents.sessionId`，并不对应 Raft 的讨论线程 ID。代码里的 `RuntimeSession` 则是管理运行实例的接口，和持久保存的 session ID 也不是同一种对象。

关系依据：[频道成员表](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/server/src/db/schema.ts#L1590)、[按 Agent 管理执行实例](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/daemon/src/agentProcessManager.ts#L1130)、[消息的来源与回复目标](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/daemon/src/drivers/systemPrompt.ts#L159)。

### Agent 的身份不会随 session 更换而消失

在 `agents` 表里，最值得先看的不是模型名称，而是这些字段之间的关系：

| 字段 | 回答的问题 |
| --- | --- |
| `id`、`serverId`、`name` | 哪个协作空间中的哪位 Agent？ |
| `runtime`、`model`、`runtimeConfig` | 用什么执行引擎与配置？ |
| `sessionId` | 尝试延续哪段运行时会话？ |
| `machineId`、`executionMode` | 交给哪个执行载体？ |
| `status`、`lastRuntimeError` | 平台记录的状态与最近错误是什么？ |

**Atlas 的身份是 Agent 记录，会话是与这份身份关联的执行上下文。** 进程则是某个时刻承载 runtime 的操作系统对象。一轮工作是 runtime 报告或管理的一段执行边界。这四者可以相互关联，但不能用同一个 ID、同一个布尔值来代替。

例如，`resetAgentSession()` 更新的是 `sessionId: null` 和状态，并没有删除 Agent 记录。更上层的 `planResetActions()` 进一步区分了三种操作：

| 操作模式 | 停止当前执行 | 清除会话引用 | 重置工作目录 |
| --- | --- | --- | --- |
| `restart` | 是 | 否 | 否 |
| `session` | 是 | 是 | 否 |
| `full` | 是 | 是 | 有绑定机器时发出重置请求 |

是否随后启动，还由计划的 `restart` 条件决定。保留会话引用也只意味着仍可尝试恢复，并不保证 runtime 一定能找回原始上下文。反过来，完整重置会触及工作目录，因此不能把“文件跨会话保留”理解成任何重置都不会影响文件。

### 哪些时机会创建新 session？

**一位 Agent 通常延续一个当前 session，但在自己的生命周期内可以先后使用多个 session。** `agents.sessionId` 保存当前引用，并不是该 Agent 从出生到删除都不变的身份标识。以下讨论本文主要关注的受管理 Claude / Codex 路径。

| 时机 | 当前实现的处理 |
| --- | --- |
| 首次实际启动，尚无 `sessionId` | 创建新会话；不是仅创建 Agent 数据库记录就已经有模型会话 |
| 收到下一条消息、进入下一轮、消息来自另一个频道 | 沿当前 Agent 的执行路径处理，不以这些事件作为按频道新建 session 的条件 |
| 普通 `restart`、正常退出后恢复执行 | 保留 session 引用，尝试恢复旧上下文；换进程不等于换 session |
| 显式 `session` 或 `full` 重置 | 清空引用；随后实际启动时创建新会话。若暂不启动，引用就先保持为空 |
| 切换 runtime，例如 Claude → Codex | 设置更新路径清空旧引用；触发重启时使用 `session` 重置 |
| 修改 Codex 模型并请求重启 | 服务端强制使用 `session` 重置，让新模型通过新 `thread/start` 生效；仅改 reasoning effort 不触发这条强制规则 |
| 恢复命中特定可回退错误 | 例如 Claude 找不到旧会话，或 Codex 缺少 rollout / thread writer busy，进入新会话恢复路径 |
| 执行机器迁移完成持有者投影 | `finalizeAgentHolderProjection()` 清空 session 引用；目标机器后续启动不能仅凭旧 ID 当作已经恢复 |

最直观的证据是 Codex 的 `buildThreadRequest()`，其分支可以简化成：

```ts
// 解释性节选：实际实现还带工作目录、模型与提示词等参数
if (config.sessionId) {
  return { method: "thread/resume", params: { threadId: config.sessionId } };
}
return { method: "thread/start", params: { /* 新会话配置 */ } };
```

Claude 的启动参数则在有旧 ID 时加上 `--resume`。runtime 建立会话后，Daemon 处理 `session_init`，更新执行实例的 `sessionId` 并通过 `agent:session` 上报。可以把时间线理解成：`Atlas → session A1 → 多轮工作 → session 重置 → session A2`；Atlas 的身份、频道成员关系和共享消息记录仍是各自的数据对象。

**恢复失败也不是一律静默重开。** Codex 的分类函数只对识别出的缺失记录与 writer busy 等情况选择新 thread；权限拒绝和未知错误保留为错误。新 session 也不等于旧模型上下文完整迁移成功，仍需要依靠消息历史和工作目录恢复工作。单纯看到新 turn、进程重启或上下文压缩事件，不能据此断言 session ID 已经变化。

创建与恢复依据：[Codex 创建与恢复](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/daemon/src/drivers/codex.ts#L809)、[恢复错误分类](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/daemon/src/drivers/codex.ts#L722)、[Claude 启动参数](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/daemon/src/drivers/claudeLaunch.ts#L89)、[Claude 缺失会话后的冷启动](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/daemon/src/agentProcessManager.ts#L3409)、[设置变更的重置规则](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/server/src/routes/agents.ts#L2297)、[机器迁移清空引用](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/server/src/services/agentMigrationService.ts#L1003)、[新会话 ID 上报](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/daemon/src/agentProcessManager.ts#L7166)。

### 进程状态是另一条轴

Daemon 侧的 `AgentLifecycleRecord` 又描述了 `queued`、`starting`、`running`、`idle`、`cooldown` 和 `terminal` 等状态。它们与数据库的 `active`、`inactive`、`stopped` 不是同一组枚举：前者服务于机器上的执行管理，后者是平台层的状态表达。

其中，空闲和冷却记录可以保存 `AgentRestartSnapshot`，带着配置、会话与 launch 信息等待后续恢复。这些记录使用进程内的 Map 管理，不能自动当作跨 Daemon 重启的持久存储。阅读分布式系统时，“可以恢复”必须接着问：**恢复所需的事实放在哪里，能承受哪一层重启？**

源码入口：[会话重置](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/server/src/services/agentService.ts#L1111)、[重置计划](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/server/src/services/agentOrchestrator.ts#L1538)、[生命周期记录](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/daemon/src/agentLifecycleRecord.ts)。

## 04 · 一条消息怎样变成一次行动 {#message-path}

现在，用户在频道里说：“Atlas，检查这个项目的部署流程。”为便于阅读，下面展示受平台管理的 Agent 的主要链路，省略跨副本转发、迁移和外部 Agent 等分支。

{{< raft-lab kind="delivery" >}}

### 同一频道的多位 Agent，会一起处理新消息吗？

**它们可能并行处理，但没有“频道内只选一位 Agent 回答”的统一调度规则，也不保证每位成员在同一时刻开始执行。** 普通频道先通过 `getChannelAgents()` 取得成员，再逐个构造投递内容；最后为各接收者发起 `deliverMessage()`，用 `Promise.all` 汇集这些异步操作。它不会等 Atlas 完成任务，才把同一条消息交给 Nova。

还要把三个阶段分开看：

| 阶段 | 决定什么 |
| --- | --- |
| 选择接收者 | 普通频道以成员为基础；线程按其跟随与投递规则选人。过滤 Agent 自己发出的消息，并处理静音与可见 @mention 等条件 |
| 投递或唤醒 | 每位接收者独立检查 scope、目标访问权、停止状态、重置窗口、机器与 runtime 状态；可能直接投递、排队、唤醒或丢弃 |
| 消费与行动 | 各自读取 Inbox、进入或继续自己的执行轮次，再决定是否回复、领取任务或保持安静 |

所以，“我只在正文里 @Atlas”不能简单理解成“其他频道成员收不到”。普通未静音成员仍在投递候选中；@mention 会影响目标可见性、通知和部分静音穿透等行为。对于频道外的 Agent，也不能只凭写出一个名字就假设已经投递，源码还有发送者侧的 mention 解析和 notify/add 路径。线程同样不是无条件广播给父频道的所有 Agent。

到机器端，空闲的持久 runtime 可以先收到不含正文的 Inbox 更新提示，再通过 check/read 消费内容；忙碌时的处理取决于投递分支。例如，被追踪的 @mention 会先进入队列并等待观察到的轮次边界，不能仅根据 driver 声明支持 `steer`，就说所有新消息都会立即打断执行。**可并行的是不同 Agent 的执行；同一 Agent 收到多条消息，不会因此按频道自动拆成多个独立 session。**

### Atlas 发出的消息，Nova 怎样处理？

假设 Atlas 和 Nova 都是 `#deploy` 的成员，并且权限、静音与运行状态允许投递：

1. Atlas 调用 `raft message send` 发送“检查完成，请 Nova 复核”，服务端保存一条发送者类型为 `agent` 的普通协作消息。
2. 分发循环跳过 `agent.id === senderId` 的 Atlas，向 Nova 等其他符合条件的接收者投递；Nova 得到的是消息内容、发送者身份与来源目标，**不是 Atlas 的 runtime session、完整思考过程或工具上下文**。
3. Nova 在自己的 Inbox / session 中消费消息，结合自己的上下文判断是否需要复核。若回复，仍调用 `raft message send`，这条新消息又可能投递给 Atlas。

这形成了通过共享消息通信的协作，而不是两个模型互相接管会话。源码跳过发送者，能避免一条消息直接回投给自己；但它并不能独自阻止 Atlas 和 Nova 轮流发送新消息。提示词里的沟通约定要求尊重正在进行的对话、不重复汇报别人的工作、没有可行动内容时不广播。**这些是 Agent 应遵守的行为约定，不是服务端保证不会互相刷屏的硬性机制。** 真正涉及动手工作的归属，还要接到第 07 节的 task claim。

分发与响应依据：[频道接收者查询](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/server/src/services/channelService.ts#L3186)、[接收者过滤与并行投递](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/server/src/services/messageService.ts#L9070)、[唤醒决策](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/server/src/services/agentLifecycleReducer.ts#L223)、[忙碌 mention 队列](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/daemon/src/agentProcessManager.ts#L4492)、[空闲 Inbox 提示](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/daemon/src/agentProcessManager.ts#L4617)、[沟通约定](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/daemon/src/drivers/raftCliGuide.ts#L379)。

### 从业务消息到执行输入

服务端的 `messageService` 组织投递内容，包含频道、发送者、正文、消息 ID 和序号等信息，再调用 `agentOrchestrator.deliverMessage()`。后者先读取 Agent 的权威状态，检查被动接收权限与目标访问权，再决定直接投递、排队或执行唤醒计划。

这里有一个容易忽略的细节：权限收回后，部分消息会被丢弃，而不是等待以后恢复权限再补发。因为“曾经接收过的频道流量”与“现在有权阅读的历史”不能混成同一条规则。对实时协作系统来说，投递也是权限边界的一部分。

Daemon 收到消息后，会把它交给 `agentManager.deliverMessage()`。这个方法不会直接无条件写入 stdin：它先检查重复投递与已见状态，再处理启动中的缓存、终止错误、空闲恢复和运行中的输入。`true` 也可能意味着已经接纳进缓冲，或者确认这条内容无需重复注入，不能直接翻译成“模型已经回答”。

### 已送达、已见和已完成，分别需要证据

`AgentVisibleDeliveryLedger` 用消息 ID 集合和按目标区分的序号边界，记录平台能够证明的内容可见状态。代码中特别强调：一个唤醒信号不代表连续消费了正文；看到一条序号很大的 @mention，也不代表较早消息全部读过。

假设频道中有 10、11、12 三条消息，Atlas 只在输入中见到了 12。如果直接把“已读水位”推进到 12，10 和 11 就可能被误判成无需再投递。实现因此区分精确的消息 ID 与有连续消费依据的边界，同时按频道、线程、私信隔离这些记录。

上面的多 Agent 例子也要按这个边界理解：同一条消息在 Atlas 那里已消费，不代表 Nova 也已消费。这里的“模型已见”是工程上的内容投递记录，不是对模型是否理解、记住或遵从消息的证明。任务完成还需要独立的任务状态与结果判断。

### 模型输出如何成为频道回复

Daemon 对 runtime 的 `text` 事件会调用 `queueTrajectoryText()`，形成执行轨迹。它不会因为看见一段 stdout，就把那段内容自动当作正式频道回复。

与此同时，`drivers/systemPrompt.ts` 明确要求 Agent 通过 `raft message send` 进行可见沟通。于是执行中可以同时存在“模型产生了文本”“平台记录了活动”和“Agent 正式发送了消息”三种事实。这个分离有实际意义：推理过程、工具活动与对团队作出的答复，拥有不同的用途和展示方式。

当用户在 Atlas 忙碌时补充“不要修改配置，只做检查”，新的输入还需要经过 runtime 的忙碌投递能力处理。Inbox 侧的检查也会在一些写操作前发现尚未消费的上下文，并可能返回 `held` 让 Agent 先看到新内容。这是在降低基于过期上下文行动的概率；它并没有把所有外部副作用都变成统一的强制事务。

源码入口：[投递决策](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/server/src/services/agentOrchestrator.ts#L10920)、[Daemon 投递分支](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/daemon/src/agentProcessManager.ts#L4252)、[可见内容账本](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/daemon/src/agentVisibleDeliveryLedger.ts)、[Inbox 状态机](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/daemon/src/agentInboxStateMachine.ts)、[输出轨迹](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/daemon/src/agentProcessManager.ts#L7192)。

## 05 · Runtime 适配：统一行为，保留差异 {#runtime}

把 Claude Code 或 Codex 接进平台，表面看是启动命令，真正的难点是生命周期和协议不同：怎样确认启动成功？空闲时如何输入？忙碌时能否追加信息？用哪个事件判断轮次结束？恢复失败以后怎么办？

Raft 将这些差异放进 `RuntimeDriver` 的行为契约，再通过 `RuntimeSession` 提供操作边界。下面是 `RuntimeLifecycleContract` 的节选：

```ts
export type RuntimeLifecycleContract =
  | {
      kind: "persistent";
      stdin: "direct" | "notification";
      inFlightWake: "queue" | "steer";
    }
  | {
      kind: "per_turn";
      start: "immediate" | "defer_until_concrete_message";
      exit: "natural" | "terminate_on_turn_end";
      inFlightWake: "spawn_new" | "coalesce_into_pending";
    };
```

这些字段表达了管理者需要知道的行为：能不能保留会话进程、什么时候启动、执行中有新输入怎么办。它们比“支持发送消息”这样的单一布尔值更精确。

**当前快照的 ClaudeDriver 和 CodexDriver 都声明 `kind: "persistent"`。** 因此，不能把 Raft 的所有 Agent 概括为每轮都新建进程。更准确的说法是：平台支持多种生命周期；持久身份不要求进程永不退出，也不要求进程每轮退出。

| 观察点 | Claude driver | Codex driver |
| --- | --- | --- |
| 当前生命周期声明 | `persistent` | `persistent` |
| 输入与通信实现 | Claude 的 stream-json 输入输出 | Codex app-server 协议与请求 |
| 忙碌时的契约 | 支持 steer | 支持 steer |
| 会话恢复策略声明 | `resume_or_fresh` | `resume_or_fresh` |
| 学习重点 | 流式事件与输入顺序 | 初始化、thread 与 turn 的协议边界 |

相同声明不表示相同实现。`RuntimeSessionDescriptor` 还区分 transport、readiness、turnBoundary、postTurn 等维度。管理者可以依据能力做决策，而不用把每一种 runtime 的细节都散落进上层流程。

`RuntimeSendResult` 也值得注意：成功时描述是作为 `prompt`、`steer`、`notification` 还是 `request` 被接纳；失败时区分不支持、忙碌拒绝、已关闭和运行错误。这样，上层才能决定保留输入、重试或报告错误，而不是把所有情况压成一个“发送失败”。

从这种抽象中可以学到：设计统一接口时，首先找出调用者真正要作出的决定。过于统一会抹掉差异，过于具体又会让上层绑定某个供应商。Raft 的契约把差异变成可检查的数据，但新增 driver 仍要实现和验证自己的协议。

源码入口：[运行时契约与 Session 接口](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/daemon/src/drivers/types.ts)、[ClaudeDriver](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/daemon/src/drivers/claude.ts#L33)、[CodexDriver](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/daemon/src/drivers/codex.ts#L780)、[子进程 Session 包装](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/daemon/src/drivers/runtimeSession.ts)。

## 06 · 记忆放在哪里：三种历史，三种职责 {#memory}

Atlas 明天恢复工作，需要找回“之前发生过什么”。但这里至少有三类不同的信息：

| 信息 | 保存的内容 | 主要用途 |
| --- | --- | --- |
| 平台消息与任务记录 | 团队说过什么、任务处于什么状态 | 共同可见的协作事实 |
| Runtime 会话历史 | 某次模型会话中的上下文与执行记录 | 尝试延续原生会话 |
| Agent 工作目录 | `MEMORY.md`、`notes/`、脚本与产物 | 由 Agent 组织的长期知识与工作材料 |

`initializeAgentWorkspace()` 会创建工作目录，在 `MEMORY.md` 不存在时写入初始内容，并确保 `notes/` 目录存在。它还按“缺失才写入”的方式创建种子文件。这说明普通初始化的目标是补齐基础内容，而不是每次启动都覆盖记忆。

但创建文件不等于模型会自动维护它。`raftCliGuide.ts` 通过提示词说明：`MEMORY.md` 是索引入口，详细信息应放入 notes；重要进展后更新记录；长任务开始前写下当前工作上下文。**文件初始化是程序行为，记忆整理主要是一项要求 Agent 遵循的工作约定。**

这个区分决定了我们如何评价设计。文本记忆易于检查、编辑和迁移，也便于人在恢复失败时追踪结论来源。但它是否完整、是否过时、是否记录了错误结论，仍取决于写入与复核。不能因为使用 Markdown，就宣称记忆天然可靠。

Raft 的 cleaner 实现还会测量指定 `MEMORY.md` 的大小，超过配置阈值时产生提醒。这种机制能提醒索引过大，却不能独自判断哪条知识正确、哪段应该删除。

当 runtime 无法恢复旧会话时，平台可以退回新会话，并提示从平台对话历史与本地文件中恢复工作。此时，保留下来的 Agent 身份、任务归属和工作材料仍然有用；丢失的原生上下文也应明确承认。这样的恢复路径比“重启后完全没有区别”的承诺更符合实际。

源码入口：[工作目录初始化](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/daemon/src/workspaces.ts#L12)、[记忆与压缩恢复约定](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/daemon/src/drivers/raftCliGuide.ts#L416)、[cleaner](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/daemon/src/apps/cleaner/runtime.ts)、[Codex 恢复行为测试](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/daemon/src/agentProcessManager.codex.test.ts)。

## 07 · 多 Agent 协作，先解决谁负责 {#coordination}

频道容纳讨论，线程限定某个讨论上下文，Inbox 帮助 Agent 发现需要关注的信息，任务则记录可追踪的工作与归属。多个 Agent 共用这些对象以后，“谁来做”就不能只留在自然语言里。

例如 Atlas 和 Nova 都看到“检查部署流程”。如果它们各自说“我来处理”，模型本身不会自动形成互斥关系。平台需要一个可以竞争、可以拒绝、可以查询的任务领取操作。

### 任务已经是独立的数据对象

手册仍有“任务是在消息上附加元数据”的描述，但当前 `schema.ts` 有独立的 `tasks` 表，`claimTaskDetailed()` 调用 `writeCanonicalClaim()` 写入这张表。任务通过 `messageId` 与聊天消息关联，同时拥有自己的状态、领取者、领取时间和 `revision`。这正是需要以实际读写路径校准文档的地方。

任务的常见状态路径是 `todo → in_progress → in_review → done`，另有 `closed` 表达终止且未成功完成的情况。这些状态用于表达工作过程，并不构成模型已正确解决问题的自动证明。

### Claim 的关键在更新条件

`writeCanonicalClaim()` 先检查可领取性，再执行带条件的数据库更新。下面是对关键部分的简化，省略事件记录和冲突返回：

```ts
// 解释性节选：省略其他字段与返回处理
update(tasks)
  .set({
    claimedByType,
    claimedById,
    revision: observed.revision + 1,
  })
  .where(and(
    canonicalRevisionCas(observed),
    canonicalClaimCasPredicate(claimedByType, claimedById),
  ));
```

`canonicalRevisionCas()` 要求行 ID 与读取时的版本一致；另一个条件重新确认领取者与领取状态允许当前操作。条件必须进入 UPDATE 本身：如果仅仅先 SELECT 看一下，再无条件写入，两位 Agent 就可能同时通过前置检查。

{{< raft-lab kind="claim" >}}

在这个简化例子里，两个调用都看到 revision 7。一个成功写入 revision 8，另一个携带的旧版本条件便无法匹配。失败方再读取当前状态形成冲突结果。源码还重新检查具体归属条件，用来补充“版本号只有在写入者记得递增时才有效”的限制。

预先 assignment 与真正 claim 也不同：分配给另一位成员的 `todo` 可以保留归属而尚未开始，指定成员 claim 后才记录开始。数据库列名叫 `claimedById`，却也承担预分配关系，因此判断时要同时看 `status` 和 `claimedAt`。

### 平台协作与 Runtime 子 Agent 是两层结构

Atlas 和 Nova 是 Raft 中可独立识别的成员。Claude 等 runtime 在一次执行中启动的子 Agent，则可能通过事件中的 lineage 进入执行轨迹。两者并不能因为都叫 Agent，就自动拥有相同的频道身份、权限与任务归属。

Claim 解决的是平台内一项任务的归属竞争。它不能锁住任意 shell 命令或远端资源，也无法阻止一个不遵守结果的 Agent 继续工作。协议约束、服务端检查与 Agent 的协作习惯，需要分别评价。

源码入口：[tasks 表](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/server/src/db/schema.ts#L3415)、[领取入口](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/server/src/services/taskService.ts#L1907)、[条件更新](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/server/src/services/taskService.ts#L2416)、[领取测试](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/server/src/services/taskService.claim.test.ts)、[子 Agent 事件类型](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/daemon/src/drivers/types.ts#L150)。

## 08 · 权限与凭据：边界需要落到执行路径 {#permissions}

Atlas 通过 CLI 发消息时，需要以某种身份调用服务端。直接把服务端凭据放进模型可见的启动环境，会让它在命令、日志或错误输出中出现的机会增加。Raft 在受管理的调用链中使用 Daemon 本地凭据代理。

```text
Agent 调用 raft CLI
  │  本地代理 token
  ▼
Daemon credential proxy
  │  校验注册信息、限制目标 origin
  │  替换为真实服务端凭据和 Agent 身份头
  ▼
Raft Server
     验证身份、scope 与目标对象访问权限
```

`registerAgentCredentialProxy()` 生成随机本地 token，并在 Daemon 内保存它与 Agent、目标 Server、真实 API key 和 launch 的绑定。处理请求时，代理先找到这个注册项，再构造目标 URL。

这里一个很具体的防护是 **origin 校验必须发生在添加真实凭据之前**。如果调用者使用绝对 URL 等形式改变目标，代理会拒绝 origin 不匹配的请求。通过检查之后，代理忽略来路的 Authorization，再设置自己的 `Authorization` 与 `X-Agent-Id` 等头部。

本地 token 因而也是有权限的凭据，只是权限通过注册绑定和转发路径约束。它不能被当成无害文本；这种设计减少真实服务端凭据在 Agent 调用路径中的直接暴露，却不意味着消除了全部凭据风险。

服务端仍是授权的权威来源。`agentScopesService` 的说明明确区分服务端检查与 Daemon 的协作性缓存，频道服务也提供 Agent 的访问和发言检查。更值得注意的是，当前 scope 实现为缺少配置记录的已有 Agent 合成默认全量可授予 scope。**拥有 scope 系统，与默认采用最小权限，是两个不同命题。**

操作系统隔离是另一层问题。当前 Claude 启动参数包含 `--dangerously-skip-permissions` 与 `bypassPermissions`。这是一个源码可见的执行选择，说明不能用“有凭据代理”推导出“所有文件访问与 shell 命令都被平台沙箱隔离”。若研究它的安全性，需要继续核对实际运行用户、目录权限、运行时配置与部署环境。

这一节最值得带走的是检查边界的方法：请求以什么身份进入？谁决定权限？凭据在哪一层被附加？拒绝路径在哪里？边界外还有哪些能力？这些问题比把安全能力概括成一个名词更有帮助。

源码入口：[代理校验与转发](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/daemon/src/agentCredentialProxy.ts#L410)、[代理注册](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/daemon/src/agentCredentialProxy.ts#L1674)、[scope 权威读取与默认值](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/server/src/services/agentScopesService.ts)、[Agent 频道访问](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/server/src/services/channelService.ts#L5472)、[Claude 启动参数](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/daemon/src/drivers/claudeLaunch.ts#L61)。

## 09 · 带着问题继续读源码 {#reading-map}

从 Atlas 的一次工作回看，Raft 的实现把许多容易混淆的概念分开了：身份与进程、接纳与消费、轨迹与回复、分配与开始、恢复入口与完整历史、凭据代理与系统隔离。

这些分离会增加数据结构、分支和测试数量，却让错误有机会被准确描述。Agent 没有回复，可以继续问它是否获得访问权、是否投递、是否进入启动缓冲、runtime 是否接纳、是否通过 CLI 发出了消息。任务冲突，也能回到一个可查询的归属与版本，而不只是在聊天记录里寻找承诺。

如果想继续阅读，可以按下面的顺序，每次只带一个问题进入源码：

| 顺序 | 源码入口 | 要回答的问题 |
| --- | --- | --- |
| 1 | [schema.ts](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/server/src/db/schema.ts) | 身份、消息、机器、任务怎样关联？ |
| 2 | [agentOrchestrator.ts](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/server/src/services/agentOrchestrator.ts) | 什么条件允许启动、投递和重置？ |
| 3 | [core.ts](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/daemon/src/core.ts) 与 [agentProcessManager.ts](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/daemon/src/agentProcessManager.ts) | 消息到机器后如何变成执行？ |
| 4 | [drivers/types.ts](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/daemon/src/drivers/types.ts) | 哪些 runtime 差异必须显式表达？ |
| 5 | [workspaces.ts](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/daemon/src/workspaces.ts) 与 [raftCliGuide.ts](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/daemon/src/drivers/raftCliGuide.ts) | 哪些内容被保存，模型怎样找到它们？ |
| 6 | [taskService.ts](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/server/src/services/taskService.ts) | 并发修改时，归属如何保持一致？ |
| 7 | [agentCredentialProxy.ts](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/daemon/src/agentCredentialProxy.ts) | 哪层持有凭据，哪层执行授权？ |

然后阅读对应测试，特别是恢复失败、重复投递、任务领取竞争与代理目标校验的用例。测试可以帮助理解作者试图保护的行为；是否在自己的环境中通过，仍需实际运行后再判断。

如果把这些思路用到一个更小的 Agent 产品，第一步可以先明确自己的工作流需要哪些持久对象、哪些输入确认和哪些授权边界，再决定是否需要完整的频道、桌面端和多 runtime 支持。Raft 的模块很多，有价值的学习方式是理解每个机制在解决什么故障，而不是把所有复杂度都当成起步条件。

对本文而言，“长期协作的参与者”最终落在一组可解释的关系上：Atlas 有稳定身份，有能找回工作的材料，有受约束的执行入口，也有与他人共享、能够追踪的任务。模型能力在这些关系中发挥作用，平台则负责让执行与协作留下足够清晰的事实。

---

*阅读基线：公开快照 `05f7d8fd77d2535f993d5d90b85118438bc18216`。文中源码与测试链接均固定到该提交；交互图为教学示意。本文未启动 Raft 服务、调用模型或运行其集成测试。*
