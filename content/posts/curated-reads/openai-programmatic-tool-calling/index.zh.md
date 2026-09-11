---
title: "OpenAI 程序化工具调用：用 JavaScript 编排工具"
date: 2026-09-11
author: OpenAI
categories: ["Curated Reads"]
tags: ["OpenAI", "Responses API", "Agents API", "Programmatic Tool Calling", "Function Calling", "Tool Search"]
draft: false
description: "OpenAI Programmatic Tool Calling 中文译介：让模型生成 JavaScript 来并发、循环和聚合工具调用，减少中间上下文，并安全续跑客户端函数。"
---

> **来源：** [OpenAI API 文档 — Programmatic Tool Calling](https://developers.openai.com/api/docs/guides/tools-programmatic-tool-calling)
>
> **说明：** 本文是基于 OpenAI 官方文档整理的中文译介，保留原文技术结构与关键示例。API 能力可能更新，请以官方文档为准。

程序化工具调用（Programmatic Tool Calling）允许模型编写并运行 JavaScript 来协调可用工具。生成的程序可以并发调用工具、使用循环和条件、组合中间结果，再向模型返回一个更小的最终结果，而不是把每一份原始输出都塞回模型上下文。

这特别适合控制流可预测的多工具阶段，以及中间数据量很大的任务。在 Responses API 中，应用决定是否开放该能力，以及每个工具可被直接调用、程序调用或两者皆可。客户端函数依然由应用执行。在 Agents API 中，这项能力默认开启，托管框架会负责 Agent 循环。

启用前应查看最新模型页面，确认所选模型当前支持该能力。

## 运行时环境

每段生成的程序都在一个全新、隔离的 V8 运行时中执行。它支持 JavaScript 和顶层 `await`，但不是 Node.js。运行时不提供：

- 安装包；
- 直接网络访问；
- 通用文件系统；
- 子进程执行；
- 控制台；
- 跨程序执行持久化的 JavaScript 状态。

程序只能通过请求中启用的工具访问外部系统，并用 `text(...)` 或 `image(...)` 输出结果。

Responses API 可以在不使用持久代码容器的情况下，将程序化工具调用用于零数据保留（ZDR）工作流。但组织或项目必须已经启用 ZDR；`store: false` 只代表无状态续传，并不会自行开启 ZDR。实际保留资格取决于完整请求，包括模型、工具和第三方服务。

## 何时适合使用

当一个阶段的控制流可预测，而且代码能够把多份中间输出压缩为较小的结构化结果时，程序化编排最有价值。

| 任务形态 | 推荐方式 |
| --- | --- |
| 单次查询或操作 | 直接工具调用 |
| 过滤、连接、排序、去重、聚合或校验多份结果 | 程序化工具调用 |
| 数据流可预测的依赖调用 | 程序化工具调用，并明确限制与失败行为 |
| 每个结果都会影响下一步判断的自适应搜索或语义评估 | 直接工具调用 |
| 写操作或需要审批的动作 | 默认直接调用，保留清晰授权边界 |
| 最终引用或原生制品校验 | 默认直接调用；除非程序能保留并验证所有必需项 |

真正的分界线是“是否需要重新判断”。确定性的协调和缩减适合交给代码；如果每个结果都会改变下一步的语义决策，就应让模型重新参与。

## 配置 Responses API

在请求中加入托管工具 `programmatic_tool_calling`，再为允许生成代码调用的工具设置 `allowed_callers`。

```json
[
  {
    "type": "function",
    "name": "get_inventory",
    "description": "返回 sku 和 available_units。",
    "parameters": {
      "type": "object",
      "properties": { "sku": { "type": "string" } },
      "required": ["sku"],
      "additionalProperties": false
    },
    "output_schema": {
      "type": "object",
      "properties": {
        "sku": { "type": "string" },
        "available_units": { "type": "number" }
      },
      "required": ["sku", "available_units"],
      "additionalProperties": false
    },
    "allowed_callers": ["programmatic"]
  },
  { "type": "programmatic_tool_calling" }
]
```

`allowed_callers` 决定工具的调用路径：

| 值 | 行为 |
| --- | --- |
| 省略或 `["direct"]` | 只能由模型直接调用 |
| `["programmatic"]` | 只能由生成的 `program` 调用 |
| `["direct", "programmatic"]` | 两条路径都可以 |

`parameters` 描述函数输入。如果函数会返回可预测的结构化数据，应使用 `output_schema` 描述编码在 `function_call_output.output` 字符串里的 JSON 对象，让生成的 JavaScript 能可靠访问已知字段。

### 支持的工具

下列工具可以开放给程序调用：

- function 与 custom 工具；
- MCP 工具；
- Apply Patch；
- 本地与托管 Shell；
- Code Interpreter。

MCP 工具的 `require_approval` 策略可能让程序暂停，直到审批通过。把 OpenAI 托管工具交给程序前，应先检查对应的数据保留与安全说明。

### 与 Tool Search 组合

Tool Search 是 Responses API 的顶层工具，生成的 JavaScript 不能在内部调用它。延迟加载的 function、custom 和 MCP 工具在模型加载之前对程序不可见。正确顺序是：

1. 先让模型搜索并加载延迟工具；
2. 再启动后续程序，通过 `tools.*` 使用它；
3. 确认已加载的定义允许 `programmatic` 调用者。

已经运行的程序不能临时发起 Tool Search 来发现新工具。

## 明确划分每个工作流阶段

当一个函数既能直接调用，又能在程序中调用时，“高效使用程序化工具调用”之类的笼统指令并不能划清边界。应该把每条路径分配给具体阶段：

```text
<tool_orchestration>
在 [有界阶段] 使用程序化工具调用，并且只能使用 [允许的工具]。
安全时并发执行互相独立的调用，只使用文档声明的输入和输出字段。

处理并缩减中间结果，最终严格输出 [结果结构]，其中必须包含回答所需证据。

达到 [停止条件] 后停止。瞬态错误最多重试 [R] 次。
不要重复已经完成的调用，也不要执行有副作用的动作。
如果必需结果仍然缺失，返回明确的结构化失败。

[语义判断、审批或最终验证] 使用直接工具调用。
</tool_orchestration>
```

例如，可以让程序并发查询库存和需求，最终只输出 `sku`、`available_units`、`requested_units` 与计算后的 `shortage_units`。任何修改库存的动作仍使用直接调用，并经过审批。

两种路径之间只定义一次明确交接，避免来回切换或重复劳动。如果存在安全回退，也只定义一次并限制重试次数。

## 理解响应项与执行关系

API 仍然返回标准 Responses 对象。程序化执行可能在 `response.output` 中新增几类顶层项：

- `program`：生成的 JavaScript、程序 `call_id`，以及用于重放或续传的不透明 `fingerprint`。
- `function_call`：程序发出的工具调用。它有自己的 `call_id`，而 `caller.caller_id` 指向所属程序。
- `program_output`：程序输出的最终结果及 `completed` 或 `incomplete` 状态，其 `call_id` 与程序相同。

```json
[
  {
    "type": "program",
    "call_id": "call_prog_123",
    "code": "const [stock, demand] = await Promise.all([tools.get_inventory({ sku: 'sku_123' }), tools.get_demand({ sku: 'sku_123' })]); text(JSON.stringify({ sku: stock.sku, shortage_units: Math.max(demand.requested_units - stock.available_units, 0) }));",
    "fingerprint": "opaque_replay_state"
  },
  {
    "type": "function_call",
    "call_id": "call_inventory_123",
    "name": "get_inventory",
    "arguments": "{\"sku\":\"sku_123\"}",
    "caller": {
      "type": "program",
      "caller_id": "call_prog_123"
    }
  },
  {
    "type": "function_call",
    "call_id": "call_demand_123",
    "name": "get_demand",
    "arguments": "{\"sku\":\"sku_123\"}",
    "caller": {
      "type": "program",
      "caller_id": "call_prog_123"
    }
  }
]
```

应用返回两个客户端函数的结果后，后续响应可能包含：

```json
{
  "type": "program_output",
  "call_id": "call_prog_123",
  "result": "{\"sku\":\"sku_123\",\"available_units\":42,\"requested_units\":31,\"shortage_units\":0}",
  "status": "completed"
}
```

`program_output.result` 内部的 JSON 字符串遵循提示中约定的结果结构；外层 `program_output` 则遵循 API 契约。最终 assistant `message` 可能同时出现，也可能在下一次响应才出现，所以应用必须继续运行到收到最终消息。

JavaScript 由 OpenAI 执行，客户端函数仍由应用执行。应用需要用 `function_call_output` 返回每个函数结果，并原样复制函数调用上的 `caller` 字段，服务才能恢复正确的程序。

## 客户端函数的续跑循环

程序可能多次暂停等待客户端工具。可靠的应用循环应该：

1. 发送带有程序化能力和合格工具的请求；
2. 执行所有返回的客户端 `function_call`；
3. 用原始 `call_id` 和 `caller` 返回结果；
4. 处理不完整响应；
5. 如果既没有待执行函数，也没有最终消息，则继续响应；
6. 只有收到最终 assistant 消息才停止。

下面是精简后的无状态 JavaScript 循环：

```javascript
import OpenAI from "openai";
import { toResponseInputItems } from "openai/lib/responses/ResponseInputItems";

const client = new OpenAI();

const implementations = {
  get_inventory: async ({ sku }) => ({ sku, available_units: 42 }),
  get_demand: async ({ sku }) => ({ sku, requested_units: 31 }),
};

const tools = [
  {
    type: "function",
    name: "get_inventory",
    description: "返回 sku 和 available_units。",
    parameters: {
      type: "object",
      properties: { sku: { type: "string" } },
      required: ["sku"],
      additionalProperties: false,
    },
    output_schema: {
      type: "object",
      properties: {
        sku: { type: "string" },
        available_units: { type: "number" },
      },
      required: ["sku", "available_units"],
      additionalProperties: false,
    },
    allowed_callers: ["programmatic"],
    strict: true,
  },
  {
    type: "function",
    name: "get_demand",
    description: "返回 sku 和 requested_units。",
    parameters: {
      type: "object",
      properties: { sku: { type: "string" } },
      required: ["sku"],
      additionalProperties: false,
    },
    output_schema: {
      type: "object",
      properties: {
        sku: { type: "string" },
        requested_units: { type: "number" },
      },
      required: ["sku", "requested_units"],
      additionalProperties: false,
    },
    allowed_callers: ["programmatic"],
    strict: true,
  },
  { type: "programmatic_tool_calling" },
];

const input = [{ role: "user", content: "比较 sku_123 的库存与需求。" }];

while (true) {
  const response = await client.responses.create({
    model: "gpt-6-astra",
    store: false,
    input,
    tools,
  });

  if (response.status !== "completed") {
    throw new Error(`Response ended with ${response.status}`);
  }

  input.push(...toResponseInputItems(response.output));
  const calls = response.output.filter((item) => item.type === "function_call");

  if (calls.length === 0) {
    const message = response.output.find((item) => item.type === "message");
    if (message) {
      console.log(response.output_text);
      break;
    }
    continue;
  }

  const outputs = await Promise.all(
    calls.map(async (call) => ({
      type: "function_call_output",
      call_id: call.call_id,
      output: JSON.stringify(
        await implementations[call.name](JSON.parse(call.arguments))
      ),
      caller: call.caller,
    }))
  );

  input.push(...outputs);
}
```

如果使用存储响应，可以通过 `previous_response_id` 续传，并只发送新的函数输出。如果使用 `store: false`，必须按原顺序重放完整历史：program、reasoning、function call、function output 与 program output。无状态推理模型请求还必须重放每个 reasoning 项，包括其中的加密内容。

## 为程序设计工具

- 返回紧凑的结构化数据，让 JavaScript 无需解析自然语言。
- 输出结构可预测时定义 `output_schema`，同时记录错误行为。
- 如果返回形态未知且需要模型理解，应保留为直接工具。
- 明确程序最终结果的结构，以及必须保留的证据。
- 无法产生有效结果时返回结构化失败。
- 尽量保证函数幂等，重试或重放不能重复危险副作用。
- 应用必须逐次验证参数和权限，即使调用来自托管程序。
- 使用具体的工具名和描述。
- 无论调用者是谁，高影响动作都要经过应用级审批。

## 如何评估

程序化工具调用可以减少进入模型上下文的中间数据，但收益取决于任务与工具输出。应先把直接调用作为基线，再用代表性工作负载比较两种方式。

评估不能只看效率：

- 最终答案的正确性、完整性与证据覆盖；
- 输入及总 Token、端到端延迟和成本；
- 模型轮次、工具调用、重试和恢复行为；
- 副作用与审批相关的安全结果；
- 实际执行路径是否符合预定工作流阶段。

在优化 Token 前先定义质量标准与证据要求，并明确记录任何可接受的质量取舍。

## Agents API

Agents API 在 OpenAI 托管的 Agent 框架中运行程序化工具调用，并且默认开启。框架会给 Agent 提供 `exec` 工具，同时让现有工具可供生成的 JavaScript 使用；无需把它们包装成命令行程序。

如需关闭，必须显式配置：

```json
{
  "type": "programmatic_tool_calling",
  "enabled": false
}
```

省略整个配置、只省略 `enabled`，或使用 `{ "type": "programmatic_tool_calling" }`，都会保持启用。

纯对话会话也可以在 `environment.type: "none"` 时使用它。Bash、executor MCP 等真正依赖沙箱的工具仍需要执行环境。

用 JavaScript 编排工具并不会改变工具实际运行的位置：Shell 命令仍在沙箱执行，executor MCP 仍使用其执行环境，function 工具仍调用应用服务器。Agent 接收结果后，再决定哪些信息进入模型上下文。

## 延伸阅读

- [Function calling](https://developers.openai.com/api/docs/guides/function-calling)
- [Tool search](https://developers.openai.com/api/docs/guides/tools-tool-search)
- [Conversation state](https://developers.openai.com/api/docs/guides/conversation-state)
- [Data controls](https://developers.openai.com/api/docs/guides/your-data)
