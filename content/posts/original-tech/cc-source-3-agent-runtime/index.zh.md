---
title: "Claude Code 源码解析（三）：Subagent / Agent Runtime 的执行闭环"
date: 2026-02-19
tags: ["Claude Code", "Agent", "Runtime", "Subagent", "TypeScript"]
categories: ["Original Tech"]
---

> 本文是 [Claude Code 源码逆向系列]({{< ref "/posts/original-tech/cc-source-1-restore" >}}) 的第三篇，聚焦 Agent Runtime 的核心执行循环与子代理协作机制。

第二块是我认为最有"框架味"的部分：Agent 不是单次调用，而是一个带状态的循环执行体。

恢复后的模块拆分如下：

- `src/core/agent/runtime.ts`：核心循环，负责模型调用、tool_use 执行、结果回填
- `src/core/agent/types.ts`：运行时消息、事件、配置类型
- `src/core/agent/mailbox.ts`：队友/子代理消息邮箱（内存实现）
- `src/core/agent/manager.ts`：管理多个 in-process teammate
- `src/core/agent/protocol.ts`：控制消息协议（如 shutdown）
- `src/core/agent/inProcessRunner.ts`：轮询邮箱并驱动 runtime
- `src/core/agent/run.ts`：对外暴露的便捷入口，创建 runtime 并执行
- `src/core/agent/options.ts`：解析 teammate 选项

## 一、核心循环：`AgentRuntime.submitMessage`

整个 Agent Runtime 的灵魂是 `AgentRuntime` 类的 `submitMessage` 方法。它是一个 **AsyncGenerator**——不是简单的 async 函数，而是调用者可以按需消费每一步事件的异步迭代器。

核心循环可概括为：

```ts
// src/core/agent/runtime.ts (简化示意)
async *submitMessage(input: string): AsyncGenerator<AgentRuntimeEvent> {
  // 1. 首次调用时发送 init 事件
  yield { type: "system", subtype: "init", ... };

  // 2. 用户消息入队
  this.mutableMessages.push({ role: "user", content: input });
  yield { type: "user", message: userMessage, ... };

  // 3. 核心循环：最多 maxTurns 轮
  for (let turn = 0; turn < maxTurns; turn++) {
    const response = await callModel(client, {
      model, messages, tools, system, signal, skills
    });
    this.mutableMessages.push(assistantMessage);
    yield { type: "assistant", message: assistantMessage, ... };

    // 无 tool_use → 任务完成
    const toolUses = extractToolUses(response.content);
    if (toolUses.length === 0) {
      yield successResult(...);
      return;
    }

    // 逐个执行 tool，回填结果
    for (const toolUse of toolUses) {
      toolResults.push(await this.runLocalTool(toolUse));
    }
    this.mutableMessages.push({ role: "user", content: toolResults });
    yield { type: "tool_use_summary", ... };

    // 预算超限检查
    if (this.estimateCostUsd() > maxBudgetUsd) {
      yield { type: "result", subtype: "error_max_budget_usd", ... };
      return;
    }
  }
  // 达到最大轮次
  yield { type: "result", subtype: "error_max_turns", ... };
}
```

对应伪代码：

```text
emit init event (first call only)
push user message → emit user event
loop (max N turns):
  response = callModel(history, tools, system)
  push assistant message → emit assistant event
  if no tool_use → emit success result, return
  for each tool_use:
    check permission → run tool → collect result
  push tool results → emit tool_use_summary event
  if budget exceeded → emit budget error, return
emit max_turns error
```

这段看起来朴素，但真正决定可用性的细节隐藏在四个层面：

## 二、事件可观测：每一步都是 yield

`submitMessage` 返回的不是最终结果，而是一个事件流。类型定义在 `types.ts` 中：

```ts
// src/core/agent/types.ts
export type AgentRuntimeEvent =
  | AgentInitEvent        // system.init：工具列表、MCP server、模型名
  | AgentUserEvent        // user：用户消息或 tool_result
  | AgentAssistantEvent   // assistant：模型回复
  | AgentProgressEvent    // progress：中间进度
  | AgentStreamEvent      // stream_event：流式事件
  | AgentToolUseSummaryEvent  // tool_use_summary：工具执行摘要
  | AgentSystemEvent      // system：系统级通知
  | AgentResultEvent;     // result：最终结果（success / error_*）
```

这种设计的好处是**消费方完全解耦**：TUI 可以实时渲染每一条事件，SDK 可以只关心 `result`，日志系统可以全量记录。调用方只需要一个 `for await ... of`：

```ts
const runtime = new AgentRuntime(config);
for await (const event of runtime.submitMessage("帮我写一个函数")) {
  switch (event.type) {
    case "assistant": renderAssistant(event); break;
    case "result": handleResult(event); break;
  }
}
```

每个事件都带 `session_id` 和 `uuid`，可以做完整的链路追踪。`AgentResultEvent` 中还包含了 `duration_ms`、`duration_api_ms`、`num_turns`、`total_cost_usd`、`usage` 等指标，足够做后续分析。

## 三、工具执行：权限门控 + 错误隔离

工具执行是循环中最复杂的环节。`runLocalTool` 的完整流程是：

```ts
// src/core/agent/runtime.ts（接近原始代码）
private async runLocalTool(toolUse: ToolUseBlock): Promise<ToolResultBlock> {
  // 1. 查找工具定义
  const tool = this.cfg.tools.find(t => t.name === toolUse.name);
  if (!tool) return { type: "tool_result", content: "Error: Tool not found", is_error: true };

  // 2. 权限检查（canUseTool 回调）
  const decision = await this.maybeCanUseTool(tool, toolUse.input, toolUse.id);
  if (decision.behavior === "deny") {
    this.permissionDenials.push({ tool_name: tool.name, ... });
    return { type: "tool_result", content: decision.message, is_error: true };
  }

  // 3. 输入可能被权限系统修改（如路径重写）
  if (decision.updatedInput !== undefined) input = decision.updatedInput;

  // 4. 工具自身的 parse（输入校验/转换）
  if (tool.parse) input = tool.parse(input);

  // 5. 执行
  const output = await tool.run(input);
  return { type: "tool_result", content: output };
}
```

几个值得注意的设计：

- **权限检查是可选的**：如果 `AgentRuntimeConfig` 没有传 `canUseTool`，默认 `allow`。这让 SDK 模式和交互模式可以用同一套 runtime
- **权限系统可以修改输入**：`decision.updatedInput` 意味着权限层不仅是 "能不能用"，还可以是 "能用但要改参数"（比如把相对路径转绝对路径）
- **错误不会炸掉循环**：整个 `runLocalTool` 用 try/catch 包裹，任何工具级别的异常都会被捕获并转换为 `is_error: true` 的 `ToolResultBlock` 回填给模型。模型能感知到错误并决定是否重试
- **拒绝记录可审计**：所有被拒绝的工具调用都被追加到 `permissionDenials` 数组，最终出现在 `AgentResultEvent` 中

对于 MCP Server 工具（`server_tool_use`），走的是 `runServerTool` 分支，通过 `cfg.runServerTool` 回调委托给 MCP 层：

```ts
private async runServerTool(toolUse: ServerToolUseBlock): Promise<ToolResultBlock> {
  if (!this.cfg.runServerTool) {
    return { content: "Error: MCP tool unavailable", is_error: true };
  }
  const output = await this.cfg.runServerTool(
    { id: toolUse.id, name: toolUse.name, input: toolUse.input },
    { signal: this.abortController.signal }
  );
  return { type: "tool_result", content: output };
}
```

本地工具和 MCP 工具在循环中是**并列处理**的，先执行本地再执行远程，两类结果拼到同一个 `tool_result` 消息中。

## 四、中断与边界控制

Runtime 内置了三层安全边界：

**1. AbortController 中断**

```ts
private readonly abortController = new AbortController();

interrupt() {
  this.abortController.abort();
}
```

`abortController.signal` 被传递到模型调用和工具执行中，任何层级都可以响应中断。外部只需调用 `runtime.interrupt()` 就能终止当前正在进行的 API 请求或工具操作。

**2. maxTurns 限制**

```ts
const maxTurns = this.cfg.maxTurns ?? 16;
for (let turn = 0; turn < maxTurns; turn++) { ... }
```

防止模型进入无限工具调用循环。达到上限后会 yield 一个 `error_max_turns` 结果事件，而不是抛异常——调用方可以决定如何处理（提示用户、自动续接等）。

**3. maxBudgetUsd 预算控制**

```ts
if (this.estimateCostUsd() > this.cfg.maxBudgetUsd) {
  yield { type: "result", subtype: "error_max_budget_usd", ... };
  return;
}
```

每轮工具执行后检查累计消耗。`updateUsage` 方法在每次模型调用后累加 `input_tokens` 和 `output_tokens`，并按模型维度分桶统计。

这三层控制有一个共同特点：**不抛异常，而是产出结构化的结果事件**。调用方永远能拿到一个 `AgentResultEvent`，不需要 try/catch 来区分 "正常结束" 和 "超限结束"。

## 五、子代理协作：Mailbox / Manager / Runner

Claude Code 的 Agent 不是 "一个模型解决一切"，而是支持团队协作（Teammate）模式。这部分的架构分为三层：

### 5.1 消息邮箱 `InMemoryTeamMailbox`

```ts
// src/core/agent/mailbox.ts
export class InMemoryTeamMailbox implements TeamMailbox {
  private readonly entries = new Map<string, TeamMailboxMessage[]>();

  send(message): TeamMailboxMessage { ... }   // 投递消息到目标 agent
  dequeue(agentId): TeamMailboxMessage { ... } // 取出最早的未读消息
  peek(agentId): TeamMailboxMessage { ... }    // 查看但不消费
  list(agentId): TeamMailboxMessage[] { ... }  // 列出所有未读
  markRead(agentId, messageId): boolean { ... }
  clear(agentId): number { ... }
}
```

邮箱是 `Map<agentId, messages[]>` 的简单内存结构。每条消息有 `kind` 字段：`user`、`shutdown_request`、`shutdown_approved`、`task_notification`，分别对应不同的控制语义。

### 5.2 团队管理器 `AgentTeamManager`

```ts
// src/core/agent/manager.ts
export class AgentTeamManager {
  private readonly mailbox: TeamMailbox;
  private readonly runners = new Map<string, InProcessTeammateRunner>();

  createInProcessTeammate(config): InProcessTeammateRunner { ... }
  send(agentId, message, from?): TeamMailboxMessage { ... }
  requestShutdown(agentId, from, reason?): TeamMailboxMessage { ... }
  stop(agentId): boolean { ... }
  stopAll(): void { ... }
}
```

Manager 的职责是**创建和管理多个子代理**。每个子代理是一个独立的 `AgentRuntime` 实例，被 `InProcessTeammateRunner` 包裹驱动。Manager 持有共享的 mailbox，所有子代理通过同一个邮箱通信。

### 5.3 子代理执行器 `InProcessTeammateRunner`

```ts
// src/core/agent/inProcessRunner.ts
export class InProcessTeammateRunner {
  async *run(initialPrompt: string): AsyncGenerator<AgentRuntimeEvent> {
    // 先执行初始任务
    yield* this.cfg.runtime.submitMessage(initialPrompt);

    // 然后进入轮询模式：监听邮箱
    while (!this.stopped) {
      const next = await this.waitForNextMessage(); // 轮询 mailbox.dequeue
      if (next.type === "aborted") return;

      if (next.type === "shutdown_request") {
        // 如果配置了自动审批，直接回复并停止
        if (this.cfg.autoApproveShutdown) {
          this.cfg.mailbox.send({ kind: "shutdown_approved", ... });
          this.stopped = true;
        }
        yield { type: "system", subtype: "shutdown_request", ... };
        if (this.stopped) return;
        continue;
      }

      // 普通用户消息 → 交给 runtime 处理
      yield* this.cfg.runtime.submitMessage(next.message.text);
    }
  }
}
```

这个设计非常巧妙：子代理执行完初始任务后，并不退出——它进入一个 **mailbox 轮询循环**。Lead Agent 可以随时通过 Manager 发送新的任务消息或 shutdown 请求，子代理会自动响应。

### 5.4 控制协议 `protocol.ts`

团队间的控制消息使用类 XML 的文本协议：

```ts
// src/core/agent/protocol.ts
formatShutdownRequest("lead", "task done")
// → <shutdown_request from="lead" reason="task done" />

formatShutdownApproved("worker-1")
// → <shutdown_approved from="worker-1" />
```

`parseTeamControlMessage` 可以从普通的 user 消息文本中解析出控制指令。这意味着 shutdown 请求既可以通过 `mailbox.send({ kind: "shutdown_request" })` 直接发送，也可以嵌在模型生成的文本中由 Runner 自动识别。

### 5.5 整体协作流程

```text
Lead Agent（主代理）
  │
  ├── AgentTeamManager.createInProcessTeammate(config)
  │     → new AgentRuntime(config)
  │     → new InProcessTeammateRunner(runtime, mailbox)
  │
  ├── runner.run("分析这个代码库") → 子代理开始执行
  │     ├── runtime.submitMessage("分析这个代码库")
  │     │     └── model → tools → results → ...
  │     └── 进入 mailbox 轮询
  │
  ├── manager.send(agentId, "再检查测试覆盖率") → 投递新任务
  │     └── 子代理从 mailbox 取出 → runtime.submitMessage(...)
  │
  └── manager.requestShutdown(agentId, "lead", "任务完成")
        └── 子代理收到 → 回复 shutdown_approved → 退出
```

## 六、ToolRunner：另一个循环实现

除了 `AgentRuntime`，代码中还有一个 `src/core/model/toolRunner.ts` 中的 `ToolRunner` 类。它实现了类似的 tool 循环，但定位不同：

```ts
// src/core/model/toolRunner.ts
export class ToolRunner implements AsyncIterable<any> {
  async *[Symbol.asyncIterator]() {
    while (true) {
      if (iteration >= max_iterations) break;

      // 调用模型（支持 stream 和非 stream 两种模式）
      const response = await client.beta.messages.create(params);
      yield response;

      // 自动构建 tool results
      const toolResponse = await this.generateToolResponse();
      if (toolResponse) messages.push(toolResponse);
      else break; // 无工具调用，结束
    }
  }
}
```

`ToolRunner` 更像是 Anthropic SDK 官方的 "便捷工具循环"，它：

- 不发事件，直接 yield 原始 response
- 不做权限检查
- 支持中途修改 params（`setMessagesParams` / `pushMessages`）
- 提供 `runUntilDone()` 一步到位拿结果

而 `AgentRuntime` 是 Claude Code 自己的 "产品级运行时"，加了事件系统、权限门控、预算控制、中断机制、子代理协作等完整能力。两者的关系是：**ToolRunner 是模型调用层的工具循环，AgentRuntime 是产品层的执行引擎**。

## 七、小结

回头来看，Agent Runtime 的设计可以用一句话概括：**用 AsyncGenerator 把 ReAct 循环变成可观测、可中断、可组合的事件流**。

几个我认为最值得借鉴的点：

| 设计点 | 实现方式 | 为什么重要 |
|--------|----------|-----------|
| 事件流而非返回值 | `async *submitMessage` → yield 事件 | 消费方解耦，TUI/SDK/日志可独立处理 |
| 权限前置 | `canUseTool` 回调在 `tool.run` 之前 | Human-in-the-Loop 是安全的硬要求 |
| 错误不炸循环 | try/catch → `is_error: true` 回填 | 模型能感知错误并自主决定重试 |
| 结构化终止 | `error_max_turns` / `error_max_budget_usd` | 不用异常区分正常结束 vs 超限，调用方统一处理 |
| 子代理通信 | mailbox 轮询 + XML 协议 | 多 agent 协作不污染单 agent 循环 |

我当时做了最小验证，不跑大测试，只确认结构已可达：

```bash
rg "class AgentRuntime|InMemoryTeamMailbox|AgentTeamManager" src/core/agent
```

关键输出摘要：

- 三个核心类均可定位
- `runtime/manager/mailbox` 职责清晰，不再糊在单文件里
- `ToolRunner` 与 `AgentRuntime` 分别处于 `model/` 和 `agent/` 目录，层次分明
