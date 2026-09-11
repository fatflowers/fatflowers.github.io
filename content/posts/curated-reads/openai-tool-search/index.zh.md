---
title: "OpenAI 工具搜索：按需加载大规模工具目录"
date: 2026-09-11
author: OpenAI
categories: ["Curated Reads"]
tags: ["OpenAI", "Responses API", "Agents API", "Tool Search", "Function Calling", "MCP"]
draft: false
description: "OpenAI Tool Search 中文译介：通过延迟加载、命名空间、托管式与客户端执行搜索，让模型只在需要时加载工具定义，减少上下文占用并保留提示缓存。"
---

> **来源：** [OpenAI API 文档 — Tool search](https://developers.openai.com/api/docs/guides/tools-tool-search)
>
> **说明：** 本文是基于 OpenAI 官方文档整理的中文译介，保留原文技术要点并对示例做了精简。API 能力可能更新，请以原文为准。

当 Agent 可用的工具越来越多，把所有工具定义一次性塞进模型上下文会带来明显成本：大量参数 Schema 占用输入 Token，工具变更还可能破坏提示缓存。**Tool Search（工具搜索）**解决的正是这个问题——模型在运行时搜索并加载当前任务真正需要的工具，而不是在请求开始时导入完整工具目录。

新发现的工具会被追加到上下文末尾。这种设计尽量保留已有缓存前缀，因此既能降低 Token 消耗，也有助于改善延迟。

> 在 Responses API 中，`tool_search` 仅受 `gpt-5.4` 及之后的模型支持。

## 快速开始

在 Responses API 中启用工具搜索需要两步：

1. 在 `tools` 数组中加入 `{ "type": "tool_search" }`。
2. 给希望按需加载的函数或 MCP Server 设置 `defer_loading: true`。

最小配置如下：

```json
{
  "tools": [
    {
      "type": "namespace",
      "name": "crm",
      "description": "用于查询客户与管理订单的 CRM 工具。",
      "tools": [
        {
          "type": "function",
          "name": "list_open_orders",
          "description": "按客户 ID 查询未完成订单。",
          "defer_loading": true,
          "parameters": {
            "type": "object",
            "properties": {
              "customer_id": { "type": "string" }
            },
            "required": ["customer_id"],
            "additionalProperties": false
          }
        }
      ]
    },
    { "type": "tool_search" }
  ]
}
```

## 尽量使用命名空间

工具搜索可以作用于延迟加载的单个函数、命名空间（namespace）或 MCP Server。OpenAI 更推荐后两种形式，因为模型主要针对这些搜索界面进行过训练，而且通常能节省更多 Token。

这里有一个重要差别：

- 对命名空间或 MCP Server，模型在请求开始时只看到顶层名称和描述；内部函数的完整定义会在搜索命中后才加载。
- 对单个延迟函数，模型一开始仍会看到函数名和描述；被推迟的主要是参数 Schema，因此节省空间的幅度通常较小。

`defer_loading` 应设置在命名空间内的函数上，而不是命名空间对象本身。一个命名空间也可以混合即时与延迟工具：没有设置 `defer_loading: true` 的函数可立即调用，其他函数则通过工具搜索加载。

为了让模型更可靠地找到正确工具，命名空间应该有简洁、明确的用途描述。OpenAI 建议每个命名空间尽量少于 10 个函数；如果目录更大，可以按业务边界继续拆分。

## 两种工具搜索模式

Tool Search 有两种执行方式：

| 模式 | 谁执行搜索 | 适用情况 | 关键特征 |
| --- | --- | --- | --- |
| 托管式（Hosted） | OpenAI | 创建请求时已经知道候选工具全集 | 配置最少，在同一次响应里返回已加载工具 |
| 客户端执行（Client-executed） | 你的应用 | 工具取决于项目、租户、权限或其他动态状态 | 应用完全控制检索逻辑，需要回传搜索结果 |

如果候选工具在发起请求时已经确定，优先从托管式搜索开始。只有当工具发现依赖应用掌握的动态状态时，再使用客户端执行模式。

## 托管式工具搜索

托管式搜索是最简单的接入路径：预先声明可搜索的函数、命名空间或 MCP Server，再把 `tool_search` 加进请求，由 API 决定加载哪些工具。

下面的 JavaScript 示例中，`get_customer_profile` 会立即可用，而 `list_open_orders` 只在模型判断需要时加载：

```javascript
import OpenAI from "openai";

const client = new OpenAI();

const crmNamespace = {
  type: "namespace",
  name: "crm",
  description: "用于查询客户与管理订单的 CRM 工具。",
  tools: [
    {
      type: "function",
      name: "get_customer_profile",
      description: "按客户 ID 获取客户资料。",
      parameters: {
        type: "object",
        properties: { customer_id: { type: "string" } },
        required: ["customer_id"],
        additionalProperties: false,
      },
    },
    {
      type: "function",
      name: "list_open_orders",
      description: "按客户 ID 查询未完成订单。",
      defer_loading: true,
      parameters: {
        type: "object",
        properties: { customer_id: { type: "string" } },
        required: ["customer_id"],
        additionalProperties: false,
      },
    },
  ],
};

const response = await client.responses.create({
  model: "gpt-6-astra",
  input: "列出客户 CUST-12345 的未完成订单。",
  tools: [crmNamespace, { type: "tool_search" }],
  parallel_tool_calls: false,
});

console.log(response.output);
```

如果模型决定使用延迟工具，最终函数调用之前会多出两个输出项：

- `tool_search_call`：记录托管搜索动作。
- `tool_search_output`：包含这次搜索加载的工具子集。

一个典型的输出顺序如下：

```json
[
  {
    "type": "tool_search_call",
    "execution": "server",
    "call_id": null,
    "status": "completed",
    "arguments": { "paths": ["crm"] }
  },
  {
    "type": "tool_search_output",
    "execution": "server",
    "call_id": null,
    "status": "completed",
    "tools": [
      {
        "type": "namespace",
        "name": "crm",
        "tools": [
          {
            "type": "function",
            "name": "list_open_orders",
            "defer_loading": true,
            "parameters": {
              "type": "object",
              "properties": { "customer_id": { "type": "string" } },
              "required": ["customer_id"],
              "additionalProperties": false
            }
          }
        ]
      }
    ]
  },
  {
    "type": "function_call",
    "name": "list_open_orders",
    "namespace": "crm",
    "call_id": "call_abc123",
    "arguments": "{\"customer_id\":\"CUST-12345\"}"
  }
]
```

托管模式下，搜索项的 `execution` 是 `server`，`call_id` 为 `null`。面对跨业务域任务，模型也可以在一次 `tool_search_call` 中同时加载多个命名空间或 MCP Server。

## 客户端执行工具搜索

客户端执行模式让应用完全控制工具发现过程。它适合以下情况：

- 不同租户能使用的工具不同；
- 工具来自项目级注册表；
- 搜索结果依赖用户权限或实时系统状态；
- 工具数量太大，不适合在初始请求中全部声明。

流程分为两轮：

1. 把 `tool_search` 配置成 `execution: "client"`，同时声明应用能够接收的搜索参数 Schema。
2. 模型返回 `tool_search_call` 后，应用执行检索，再用同一个 `call_id` 返回 `tool_search_output` 和匹配到的工具定义。

Python 示例：

```python
from openai import OpenAI

client = OpenAI()

first_response = client.responses.create(
    model="gpt-6-astra",
    input="先找到物流 ETA 工具，再查询 order_42。",
    tools=[
        {
            "type": "tool_search",
            "execution": "client",
            "description": "查找完成当前任务所需的项目级工具。",
            "parameters": {
                "type": "object",
                "properties": {"goal": {"type": "string"}},
                "required": ["goal"],
                "additionalProperties": False,
            },
        }
    ],
    parallel_tool_calls=False,
)

search_call = next(
    item for item in first_response.output
    if item.type == "tool_search_call"
)

loaded_tools = [
    {
        "type": "function",
        "name": "get_shipping_eta",
        "description": "查询订单的预计送达时间。",
        "defer_loading": True,
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
            "additionalProperties": False,
        },
    }
]

second_response = client.responses.create(
    model="gpt-6-astra",
    input=[
        *first_response.output,
        {
            "type": "tool_search_output",
            "execution": "client",
            "call_id": search_call.call_id,
            "status": "completed",
            "tools": loaded_tools,
        },
    ],
)

print(second_response.output)
```

客户端模式有两个不能忽略的约束：

- `execution` 必须是 `client`。
- 返回 `tool_search_output` 时，必须原样回传 `tool_search_call` 的 `call_id`。

第一轮通常只返回搜索调用并在此暂停；应用回传工具定义后，下一轮里这些工具就和普通函数一样可以被模型调用。

## 高级用法与缓存

### 写好命名空间描述

模型依靠命名空间描述决定是否进入其中搜索。顶层描述应该短而明确，说明业务用途；更丰富的细节放在延迟函数自身的描述里，因为它们只在真正需要时才进入上下文。

### 明确哪些工具已经加载

`tool_search_output.tools` 就是本次动态加载的工具集合。模型可以在后续轮次继续调用这些工具，因此客户端模式通常不需要重复加载同一工具。没有出现在该数组中的工具仍不可用。

如果想停用已加载工具，可以从定义该集合的 `tool_search_output` 项中移除它，但这会从变更点开始破坏模型缓存。

### 谨慎动态注入全新工具

多数集成会在请求的 `tools` 参数里声明候选工具。客户端执行模式也允许应用返回初始请求中从未出现过的工具，但这属于高级模式：必须仔细验证 Schema，并且只注入可信的工具定义。

### 为什么它有利于缓存

无论托管模式还是客户端模式，新加载的工具都会被放在模型上下文末尾。因此前面的上下文前缀可以继续命中缓存，减少重复输入成本并提升速度。

### 在输入的指定位置加入工具

如果应用绕过常规搜索流程加载工具，或需要复现此前响应中的工具顺序，可以使用 `additional_tools` 输入项：

```json
{
  "type": "additional_tools",
  "role": "developer",
  "tools": [
    {
      "type": "function",
      "name": "get_customer",
      "description": "按 ID 查询客户。",
      "parameters": {
        "type": "object",
        "properties": {
          "customer_id": { "type": "string" }
        },
        "required": ["customer_id"],
        "additionalProperties": false
      }
    }
  ]
}
```

其中的工具只会在该输入项出现之后变为可用。如果应用手动往返传递对话项，必须保留它在输入中的位置，让模型在同一时间点看到同一批工具。

## 在 Agents API 中使用

Agents API 默认立即加载函数定义。若要延迟其中一部分函数，需要：

- 在 `agent.tools` 中加入 `{ "type": "tool_search" }`；
- 逐个给需要按需发现的函数设置 `defer_loading: true`。

仅仅加入 `tool_search` **不会**自动延迟全部函数。会话请求仍然需要提供完整的函数名、描述和参数 Schema；工具搜索改变的只是这份定义何时进入模型上下文。工具被发现以后，应用照常处理函数调用并回传执行结果。

简化示例：

```javascript
import OpenAI from "openai";

const client = new OpenAI();

const result = await client.beta.agents.sessions.create({
  agent: {
    model: "gpt-6-astra",
    tools: [
      { type: "tool_search" },
      {
        type: "function",
        name: "lookup_account",
        description: "按账号查询账户。",
        parameters: {
          type: "object",
          properties: { account_id: { type: "string" } },
          required: ["account_id"],
          additionalProperties: false,
        },
        defer_loading: true,
      },
    ],
  },
  environment: { type: "none" },
  input: "查询账户 42。",
});

console.log(result.id);
```

### 即时加载还是延迟加载？

| 策略 | 配置 | 适用情况 | 代价 |
| --- | --- | --- | --- |
| 即时加载 | 不设置 `defer_loading`，或设为 `false` | 工具数量少，或绝大多数任务都会用到 | 未使用的定义仍占上下文；定义变化可能让缓存前缀失效 |
| 延迟加载 | 设置 `defer_loading: true` 并加入 `tool_search` | 工具目录很大，而单个任务只需少数工具 | 多一次发现步骤，效果依赖工具是否容易被正确检索 |

Agents API 支持混合两种策略，但 OpenAI 通常不建议大量混用。做默认选择前，应使用有代表性的请求比较任务完成率、输入 Token 和延迟，而不是只看工具数量。

### MCP 与插件工具

当模型和提供方支持工具搜索时，Agents API 会自动发现 MCP 工具。运行时会延迟 MCP 工具，并在存在可搜索工具时加入工具搜索；远程 MCP、执行器 MCP 和插件提供的 MCP 工具都适用。

因此，仅为 MCP 工具接入 Agents API 时，不需要手动加入 `{ "type": "tool_search" }`，也不需要在 MCP Server 上设置函数级 `defer_loading`。应按 Agents API 的 MCP 连接方式配置服务器。注意，这与前文 Responses API 的 MCP 配置并不相同。

## 实践检查清单

- 将大型目录按业务用途拆成清晰的命名空间，单个命名空间尽量控制在 10 个函数以内。
- 常用且数量少的工具即时加载；长尾工具使用 `defer_loading: true`。
- 候选全集已知时选托管式搜索；依赖租户、权限或项目状态时选客户端执行搜索。
- 客户端模式必须校验动态返回的工具 Schema，并回传正确的 `call_id`。
- 保持已加载工具集合和对话项顺序稳定，避免无谓破坏缓存。
- 用真实任务同时评估成功率、输入 Token 和端到端延迟。

## 延伸阅读

- [Function calling](https://developers.openai.com/api/docs/guides/function-calling)
- [Using tools](https://developers.openai.com/api/docs/guides/tools)
- [MCP and Connectors](https://developers.openai.com/api/docs/guides/tools-connectors-mcp)
- [Agents API](https://developers.openai.com/api/docs/guides/agents-api/overview)
