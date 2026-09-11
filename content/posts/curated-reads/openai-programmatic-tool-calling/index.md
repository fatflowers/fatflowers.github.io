---
title: "OpenAI Programmatic Tool Calling: Orchestrate Tools with JavaScript"
date: 2026-09-11
author: OpenAI
categories: ["Curated Reads"]
tags: ["OpenAI", "Responses API", "Agents API", "Programmatic Tool Calling", "Function Calling", "Tool Search"]
draft: false
description: "An English companion to OpenAI's Programmatic Tool Calling guide: run model-generated JavaScript to coordinate tools, reduce intermediate context, and implement safe continuation loops."
---

> **Source:** [OpenAI API documentation — Programmatic Tool Calling](https://developers.openai.com/api/docs/guides/tools-programmatic-tool-calling)
>
> **Editorial note:** This independently published companion preserves the guide's technical structure and key examples in a condensed form. The official documentation is the canonical and most current reference.

Programmatic Tool Calling lets a model write and run JavaScript that coordinates its available tools. Instead of returning every intermediate result to the model, a generated program can run calls concurrently, apply loops and conditions, combine results, and emit a smaller final value.

This is especially useful for predictable multi-call stages or large tool outputs. In the Responses API, the application decides whether the capability is available and which tools may be called directly, programmatically, or both. Client-owned functions still run in the application. In the Agents API, Programmatic Tool Calling is enabled by default and the managed harness handles the agent loop.

Always check the current model page before enabling this feature.

## Runtime environment

Every generated program runs in a fresh, isolated V8 runtime. It supports JavaScript and top-level `await`, but it is deliberately not Node.js. The runtime has:

- no package installation;
- no direct network access;
- no general-purpose filesystem;
- no subprocess execution;
- no console;
- no persistent JavaScript state between executions.

Programs reach external systems only through tools enabled in the request. They emit results with `text(...)` or `image(...)`.

Responses API workflows can use Programmatic Tool Calling with Zero Data Retention without a persistent code container. ZDR must already be enabled for the organization or project; `store: false` creates a stateless continuation pattern but does not enable ZDR by itself. Retention eligibility depends on the complete request, including the model, tools, and external services.

## When to use it

Programmatic orchestration works best when control flow is predictable and code can reduce several intermediate outputs into a compact result.

| Task shape | Recommended approach |
| --- | --- |
| One lookup or action | Direct tool call |
| Filter, join, rank, deduplicate, aggregate, or validate several results | Programmatic Tool Calling |
| Dependent calls with predictable data flow | Programmatic Tool Calling with explicit limits and failure behavior |
| Adaptive search or semantic evaluation after every result | Direct tool calling |
| Writes or approval-sensitive actions | Direct calls by default, preserving a clear authorization boundary |
| Final citation or native-artifact validation | Direct calls unless the program preserves and validates every required item |

The important boundary is judgment. Code is a good fit for deterministic coordination and reduction; the model should remain in the loop when each result changes the meaning of the next step.

## Responses API configuration

Add the hosted `programmatic_tool_calling` tool and set `allowed_callers` on every tool that generated JavaScript may invoke.

```json
[
  {
    "type": "function",
    "name": "get_inventory",
    "description": "Return sku and available_units.",
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

`allowed_callers` determines how a tool can be invoked:

| Value | Behavior |
| --- | --- |
| Omitted or `["direct"]` | Direct model calls only |
| `["programmatic"]` | Calls only from a generated `program` |
| `["direct", "programmatic"]` | Either route is allowed |

`parameters` defines the function's input. If it returns predictable structured data, use `output_schema` to describe the JSON object encoded in `function_call_output.output`. Generated JavaScript can then access known fields reliably.

### Supported tools

Programmatic callers are supported by:

- function and custom tools;
- MCP tools;
- Apply Patch;
- local and hosted shell;
- Code Interpreter.

An MCP tool's `require_approval` policy may pause a program until approval is granted. Before putting an OpenAI-hosted tool inside a program, review its data-retention and security guidance.

### Combining it with Tool Search

Tool Search remains a top-level Responses API tool; generated JavaScript cannot run it. Deferred function, custom, and MCP tools are unavailable to programs until the model has loaded them. The correct sequence is:

1. Let the model search for and load the deferred tool.
2. Start a later program that uses it through `tools.*`.
3. Ensure the loaded definition permits the `programmatic` caller.

An already-running program cannot pause to discover a new deferred tool.

## Route each workflow stage explicitly

If a function supports both direct and programmatic calls, vague instructions such as “use Programmatic Tool Calling efficiently” are not enough. Assign each route to a bounded stage:

```text
<tool_orchestration>
Use Programmatic Tool Calling for [bounded stage] with only [eligible tools].
Run independent calls concurrently when safe. Use documented input and output
fields only.

Reduce the intermediate data and emit exactly [result shape], including the
evidence required for the final answer.

Stop when [condition] is met. Retry transient failures at most [R] times.
Do not repeat completed calls or perform side effects. Return a structured
failure if a required result remains missing.

Use direct calls for [semantic judgment, approval, or final validation].
</tool_orchestration>
```

For example, a program can fetch inventory and demand concurrently, then emit only `sku`, `available_units`, `requested_units`, and the calculated `shortage_units`. Any inventory-changing action remains a direct, approval-gated call.

Define one clear handoff between the two routes. Avoid switching back and forth or repeating work. If a fallback exists, specify it once and cap retries.

## Response items and execution relationships

The API still returns a normal Responses object. Programmatic execution adds several possible top-level items to `response.output`:

- `program`: generated JavaScript, its `call_id`, and an opaque `fingerprint` used for replay or continuation.
- `function_call`: a tool invocation made by the program. It has its own `call_id`; `caller.caller_id` points to the program call.
- `program_output`: the program's emitted result and a `completed` or `incomplete` status. Its `call_id` matches the program.

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

After the application returns both client-owned function results, a later response may contain:

```json
{
  "type": "program_output",
  "call_id": "call_prog_123",
  "result": "{\"sku\":\"sku_123\",\"available_units\":42,\"requested_units\":31,\"shortage_units\":0}",
  "status": "completed"
}
```

The JSON inside `program_output.result` follows the result shape requested in your instructions. The outer `program_output` follows the API contract. A final assistant `message` may arrive in the same or a later response, so the application must continue until it sees that message.

OpenAI runs the JavaScript. Your application executes client-owned function calls and returns each result as a `function_call_output`. Copy the function call's `caller` field unchanged so the service can resume the correct program.

## Continuation loop for client-owned functions

A program may pause several times. A robust application loop should:

1. Send the request with the hosted capability and program-enabled tools.
2. Execute every returned client-owned `function_call`.
3. Return outputs with the original `call_id` and `caller`.
4. Handle incomplete responses.
5. Continue if no pending function calls and no final message exist.
6. Stop only when a final assistant message arrives.

This condensed JavaScript loop uses stateless continuation:

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
    description: "Return sku and available_units.",
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
    description: "Return sku and requested_units.",
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

const input = [{ role: "user", content: "Compare inventory and demand for sku_123." }];

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

With stored responses, use `previous_response_id` and send only new function outputs. With `store: false`, replay the complete ordered history: programs, reasoning items, function calls, outputs, and program outputs. Stateless reasoning-model workflows must also replay every reasoning item, including its encrypted content.

## Designing program-friendly tools

- Return compact structured data that JavaScript can inspect without parsing prose.
- Define `output_schema` and document error behavior when the output shape is predictable.
- Keep a tool direct if its output shape is unknown and requires model inspection.
- Specify the exact program result and the evidence it must retain.
- Return a structured failure when the program cannot produce a valid result.
- Make functions idempotent where possible; retries must not repeat unsafe side effects.
- Validate arguments and permissions inside the application for every call.
- Use specific tool names and descriptions.
- Require application-level approval for high-impact actions regardless of caller.

## Evaluation

Programmatic Tool Calling can reduce how much intermediate data enters model context, but the benefit depends on the task and tool responses. Use direct calls as a baseline, then compare both modes on representative workloads.

Measure more than efficiency:

- final-answer correctness, completeness, and evidence coverage;
- input and total tokens, latency, and cost;
- model turns, tool calls, retries, and recovery;
- safety outcomes for side effects and approvals;
- whether the executed route matched the intended workflow stage.

Define the quality bar and required evidence before optimizing tokens. Any accepted quality tradeoff should be explicit.

## Agents API

The Agents API runs Programmatic Tool Calling inside OpenAI's managed agent harness and enables it by default. The harness gives the agent an `exec` tool and exposes existing tools to generated JavaScript; there is no need to wrap them as command-line programs.

Disable the feature explicitly with:

```json
{
  "type": "programmatic_tool_calling",
  "enabled": false
}
```

Omitting the entry, omitting `enabled`, or using only `{ "type": "programmatic_tool_calling" }` leaves it enabled.

Conversation-only sessions can use it with `environment.type: "none"`. Tools that actually need a sandbox—such as Bash or executor MCPs—still require an execution environment.

JavaScript orchestration does not relocate tool execution. Shell commands still run in the sandbox, executor MCPs still use that environment, and function tools still call the application server. The agent receives their results and decides what should enter model context.

## Related guides

- [Function calling](https://developers.openai.com/api/docs/guides/function-calling)
- [Tool search](https://developers.openai.com/api/docs/guides/tools-tool-search)
- [Conversation state](https://developers.openai.com/api/docs/guides/conversation-state)
- [Data controls](https://developers.openai.com/api/docs/guides/your-data)
