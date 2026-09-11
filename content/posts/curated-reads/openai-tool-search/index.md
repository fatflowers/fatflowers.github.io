---
title: "OpenAI Tool Search: Load Large Tool Catalogs on Demand"
date: 2026-09-11
author: OpenAI
categories: ["Curated Reads"]
tags: ["OpenAI", "Responses API", "Agents API", "Tool Search", "Function Calling", "MCP"]
draft: false
description: "An English companion to OpenAI's Tool Search guide, covering deferred loading, namespaces, hosted and client-executed search, caching, the Agents API, and MCP tools."
---

> **Source:** [OpenAI API documentation — Tool search](https://developers.openai.com/api/docs/guides/tools-tool-search)
>
> **Editorial note:** This is an independently published companion to the official guide. It preserves the technical structure and key examples in a condensed form. The official documentation remains the canonical and most current reference.

As an agent's tool catalog grows, loading every definition into the model context becomes expensive. Parameter schemas consume input tokens even when the associated tools are irrelevant to the current task, and changing those definitions can invalidate a cached prompt prefix.

**Tool Search** addresses this problem by allowing a model to discover and load only the tools it needs at runtime. Newly discovered definitions are appended to the end of the context, helping preserve the existing cache while reducing initial context usage.

> In the Responses API, `tool_search` is supported by `gpt-5.4` and later models.

## Quick start

Enabling Tool Search in the Responses API requires two changes:

1. Add `{ "type": "tool_search" }` to the request's `tools` array.
2. Set `defer_loading: true` on each function or MCP server that should be loaded on demand.

```json
{
  "tools": [
    {
      "type": "namespace",
      "name": "crm",
      "description": "CRM tools for customer lookup and order management.",
      "tools": [
        {
          "type": "function",
          "name": "list_open_orders",
          "description": "List open orders for a customer ID.",
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

## Prefer namespaces

Tool Search works with deferred functions, namespaces, and MCP servers. Namespaces and MCP servers are generally the better organization layer because the model initially sees only their top-level names and descriptions. The detailed function definitions remain outside the context until a search loads them.

An individually deferred function is different: its name and description are still visible at the start of the request, so only most of its parameter schema is deferred. This usually produces smaller token savings.

For a namespace, `defer_loading` belongs on its functions, not on the namespace object. Deferred and eager functions can coexist in one namespace. Functions without `defer_loading: true` are callable immediately.

Give every namespace a concise description that clearly identifies its business purpose. As a practical guideline, keep a namespace below roughly ten functions; split larger catalogs along meaningful domain boundaries.

## Hosted and client-executed search

Tool Search has two execution models:

| Mode | Search owner | Best fit | Main characteristic |
| --- | --- | --- | --- |
| Hosted | OpenAI | The candidate inventory is known when the request is created | The API searches and returns the loaded subset in the same response |
| Client-executed | Your application | Availability depends on tenant, project, permissions, or live state | The application owns retrieval and sends the matching definitions back |

Hosted search is the simplest default. Client-executed search is appropriate when the initial request cannot practically contain or determine the complete inventory.

## Hosted Tool Search

With hosted search, declare the searchable inventory, add the built-in Tool Search entry, and let the API load the relevant subset.

In this example, `get_customer_profile` is immediately available while `list_open_orders` is deferred:

```javascript
import OpenAI from "openai";

const client = new OpenAI();

const crmNamespace = {
  type: "namespace",
  name: "crm",
  description: "CRM tools for customer lookup and order management.",
  tools: [
    {
      type: "function",
      name: "get_customer_profile",
      description: "Fetch a customer profile by customer ID.",
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
      description: "List open orders for a customer ID.",
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
  input: "List open orders for customer CUST-12345.",
  tools: [crmNamespace, { type: "tool_search" }],
  parallel_tool_calls: false,
});

console.log(response.output);
```

When the model needs a deferred function, two records appear before the eventual function call:

- `tool_search_call` records the hosted discovery step.
- `tool_search_output` contains the subset that has become callable.

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

Hosted search records use `execution: "server"` and a `null` search `call_id`. A single search can load more than one namespace or MCP server for a cross-domain task.

## Client-executed Tool Search

Client execution gives the application full control over discovery. A common implementation has two turns:

1. The model emits a `tool_search_call` using an argument schema defined by the application.
2. The application searches its registry and returns a `tool_search_output` containing trusted matching definitions.

```python
from openai import OpenAI

client = OpenAI()

first_response = client.responses.create(
    model="gpt-6-astra",
    input="Find the shipping ETA tool, then use it for order_42.",
    tools=[
        {
            "type": "tool_search",
            "execution": "client",
            "description": "Find project-specific tools needed for the task.",
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
        "description": "Look up shipping ETA details for an order.",
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

Two details are essential in client mode: `execution` must be `client`, and the output must echo the exact `call_id` received in the search call. Once loaded, a tool behaves like an ordinary function in the following model turn.

## Advanced behavior and caching

### Descriptions drive discovery

The model uses a namespace description to decide whether it should search that area. Keep the top-level description brief and specific. Put richer details in the deferred functions, where they enter context only after discovery.

### The loaded set persists

`tool_search_output.tools` defines the tools the model can call in later turns. Tools outside that array remain unavailable, and already-loaded tools do not normally need to be loaded again.

Removing a tool from the output item that defined the loaded set can disable it. However, changing that historical set invalidates the model cache from that point onward.

### Dynamic injection requires trust

Client-executed search may return definitions that were not present in the original request. Treat this as an advanced pattern: validate schemas carefully and expose definitions only from a trusted registry.

### Why cache preservation works

Both search modes add newly loaded definitions to the end of the model context. The stable prefix can remain cached, improving speed and reducing repeated input cost.

### Injecting tools at a precise point

An `additional_tools` input item makes tools available at a specific point in a conversation:

```json
{
  "type": "additional_tools",
  "role": "developer",
  "tools": [
    {
      "type": "function",
      "name": "get_customer",
      "description": "Look up a customer by ID.",
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

These tools become available only after the input item appears. Applications that manually replay conversation items must preserve its position.

## Using Tool Search with the Agents API

The Agents API loads function definitions eagerly by default. To defer selected functions:

- Add `{ "type": "tool_search" }` to `agent.tools`.
- Set `defer_loading: true` on each function that should be discovered on demand.

Adding Tool Search alone does not defer every function. The session request still includes each function's complete name, description, and parameter schema; discovery controls when that definition reaches the model.

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
        description: "Find an account by its account number.",
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
  input: "Look up account 42.",
});

console.log(result.id);
```

### Choosing a loading strategy

| Strategy | Configuration | Useful when | Tradeoff |
| --- | --- | --- | --- |
| Eager | Omit `defer_loading`, or set it to `false` | The catalog is small or a tool is needed for most tasks | Unused definitions occupy context and changes may invalidate the cached prefix |
| Deferred | Set `defer_loading: true` and include Tool Search | The catalog is large and each task needs only a few tools | Discovery adds a step and depends on matching the right tool |

The Agents API supports a mixture of eager and deferred functions, though extensive mixing is usually unnecessary. Evaluate completion rate, input-token usage, and end-to-end latency on representative tasks before selecting a default.

### MCP and plugin tools

When the model and provider support search, the Agents API automatically discovers MCP tools. The runtime defers searchable MCP tools and adds Tool Search as necessary across remote MCPs, executor MCPs, and MCP tools supplied by plugins.

For Agents API MCP tools, you therefore do not need to add Tool Search solely for the MCP server or apply a function-level `defer_loading` flag to that server. Configure it through the Agents API MCP connection mechanism. This differs from the Responses API setup described earlier.

## Deployment checklist

- Split large catalogs into clearly named business namespaces, ideally with fewer than ten functions each.
- Eagerly load a small set of common tools and defer the long tail.
- Use hosted search when the inventory is known; use client execution for tenant-, permission-, or project-dependent catalogs.
- In client mode, validate every dynamic schema and preserve the search `call_id`.
- Keep historical tool sets and conversation item ordering stable to protect cache hits.
- Measure task success, input tokens, and total latency on real workloads.

## Related guides

- [Function calling](https://developers.openai.com/api/docs/guides/function-calling)
- [Using tools](https://developers.openai.com/api/docs/guides/tools)
- [MCP and Connectors](https://developers.openai.com/api/docs/guides/tools-connectors-mcp)
- [Agents API](https://developers.openai.com/api/docs/guides/agents-api/overview)
