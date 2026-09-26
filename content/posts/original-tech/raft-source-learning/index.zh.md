---
title: "Raft 源码学习：一个 Agent 如何成为长期协作的参与者"
date: 2026-09-26
author: "Simon Sun · AI 撰文"
description: "从身份、消息投递、Runtime、记忆、任务领取和权限边界，学习 Raft 如何把 Agent 组织成持续参与工作的成员。"
tags: ["Agent", "Raft", "源码阅读", "Runtime", "多智能体"]
categories: ["Original Tech"]
layout: raft-study
ShowToc: false
disableShare: true
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

## 03 · Atlas 是谁：身份、会话、轮次和进程 {#identity}

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

Daemon 侧的 `AgentLifecycleRecord` 又描述了 `queued`、`starting`、`running`、`idle`、`cooldown` 和 `terminal` 等状态。它们与数据库的 `active`、`inactive`、`stopped` 不是同一组枚举：前者服务于机器上的执行管理，后者是平台层的状态表达。

其中，空闲和冷却记录可以保存 `AgentRestartSnapshot`，带着配置、会话与 launch 信息等待后续恢复。这些记录使用进程内的 Map 管理，不能自动当作跨 Daemon 重启的持久存储。阅读分布式系统时，“可以恢复”必须接着问：**恢复所需的事实放在哪里，能承受哪一层重启？**

源码入口：[会话重置](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/server/src/services/agentService.ts#L1111)、[重置计划](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/server/src/services/agentOrchestrator.ts#L1538)、[生命周期记录](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/packages/daemon/src/agentLifecycleRecord.ts)。

## 04 · 一条消息怎样变成一次行动 {#message-path}

现在，用户在频道里说：“Atlas，检查这个项目的部署流程。”为便于阅读，下面展示受平台管理的 Agent 的主要链路，省略跨副本转发、迁移和外部 Agent 等分支。

{{< raft-lab kind="delivery" >}}

### 从业务消息到执行输入

服务端的 `messageService` 组织投递内容，包含频道、发送者、正文、消息 ID 和序号等信息，再调用 `agentOrchestrator.deliverMessage()`。后者先读取 Agent 的权威状态，检查被动接收权限与目标访问权，再决定直接投递、排队或执行唤醒计划。

这里有一个容易忽略的细节：权限收回后，部分消息会被丢弃，而不是等待以后恢复权限再补发。因为“曾经接收过的频道流量”与“现在有权阅读的历史”不能混成同一条规则。对实时协作系统来说，投递也是权限边界的一部分。

Daemon 收到消息后，会把它交给 `agentManager.deliverMessage()`。这个方法不会直接无条件写入 stdin：它先检查重复投递与已见状态，再处理启动中的缓存、终止错误、空闲恢复和运行中的输入。`true` 也可能意味着已经接纳进缓冲，或者确认这条内容无需重复注入，不能直接翻译成“模型已经回答”。

### 已送达、已见和已完成，分别需要证据

`AgentVisibleDeliveryLedger` 用消息 ID 集合和按目标区分的序号边界，记录平台能够证明的内容可见状态。代码中特别强调：一个唤醒信号不代表连续消费了正文；看到一条序号很大的 @mention，也不代表较早消息全部读过。

假设频道中有 10、11、12 三条消息，Atlas 只在输入中见到了 12。如果直接把“已读水位”推进到 12，10 和 11 就可能被误判成无需再投递。实现因此区分精确的消息 ID 与有连续消费依据的边界，同时按频道、线程、私信隔离这些记录。

这里的“模型已见”是工程上的内容投递记录，不是对模型是否理解、记住或遵从消息的证明。任务完成还需要独立的任务状态与结果判断。

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
