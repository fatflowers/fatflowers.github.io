---
title: "Claude Code 源码解析（四）：权限与沙箱如何约束工具调用"
date: 2026-02-19
tags: ["Claude Code", "Agent", "权限控制", "沙箱", "TypeScript"]
categories: ["Original Tech"]
---


> 本文是 [Claude Code 源码逆向系列]({{< ref "/posts/original-tech/cc-source-1-restore" >}}) 的第四篇，聚焦权限系统与沙箱在工具调用前的门控机制。

第三块是"安全边界"核心：工具不是想调就调，必须经过权限判定。这也是 Claude Code 敢于在用户本地机器上运行 `rm -rf` 或 `curl` 的底气所在。

## 1. 架构总览：双层防御体系

在恢复代码的过程中，我发现 Claude Code 的安全机制并非铁板一块，而是清晰地分成了两个层级：

1.  **Sandbox（沙箱）**：系统级的硬约束。例如"绝对禁止读取 `/etc/passwd`"或"只允许访问 `github.com`"。这是一道不可逾越的红线。
2.  **Permissions（权限）**：用户意图的软确认。例如"可以运行这个命令吗？"或"确认写入这个文件吗？"。这通过 Human-in-the-Loop（人机回环）来实现安全兜底。

主要涉及的代码目录：

- `src/core/sandbox/`：沙箱策略、路径标准化、网络白名单。
- `src/core/permissions/`：权限决策引擎、上下文状态、规则匹配。
- `src/core/agent/runtime.ts`：执行循环中的拦截点。

## 2. Sandbox：绝对的系统边界

沙箱的核心逻辑在 `src/core/sandbox/policy.ts`。它不关心"用户同不同意"，只关心"系统允不允许"。

### 文件系统限制
最基本的防御是文件路径检查。`SandboxPolicy` 类中有一个关键的细节：**路径标准化**。

```ts
// src/core/sandbox/policy.ts
private resolvePath(input: string) {
  if (input === ".") return resolve(this.cwd);
  if (input.startsWith("/")) return resolve(input);
  return resolve(this.cwd, input); // 相对路径转绝对路径
}
```

这一点非常重要。如果没有这一步，攻击者（或幻觉中的模型）可能会尝试用 `../../` 逃逸出工作目录。恢复后的代码显示，所有的 `checkRead` 和 `checkWrite` 都会先调用 `resolvePath`，然后与 `denyRead` / `denyWrite` 列表进行比对。

### 网络访问控制
对于 `WebFetch` 和 `WebSearch` 工具，沙箱检查的是域名：

```ts
// src/core/sandbox/policy.ts (简化)
checkNetwork(target: string): SandboxDecision {
  const hostname = this.extractHostname(target);
  // 1. 黑名单检查
  if (this.matchesDomain(hostname, denied)) return { allowed: false, ... };
  // 2. 白名单检查 (如果配置了白名单)
  if (allowed.length > 0 && !this.matchesDomain(hostname, allowed)) {
    return { allowed: false, reason: "allowedDomains" };
  }
  return { allowed: true };
}
```

这意味着企业用户可以通过配置 `allowedDomains` 来强制 Claude Code 只能访问内网文档或特定的 API 服务，杜绝数据外泄风险。

## 3. Permissions：动态的决策引擎

如果沙箱说"No"，操作直接被拦截。如果沙箱说"Yes"，操作并不一定会执行，还要过第二关：权限引擎。

这一层的核心入口是 `src/core/permissions/engine.ts` 中的 `evaluateToolPermission` 函数。它是一个聚合点，把**沙箱结果**和**用户上下文**结合起来。

```ts
// src/core/permissions/engine.ts
export function evaluateToolPermission(params: PermissionEvaluationInput): PermissionDecision {
  // 1. 先查沙箱 (Priority 1)
  if (params.sandbox) {
    const violation = checkSandbox(params.sandbox, params.toolName, params.input);
    if (violation && violation.allowed === false) {
      return { behavior: "deny", message: violation.message, ... };
    }
  }

  // 2. 再查权限上下文 (ToolPermissionContext)
  const toolContext = new ToolPermissionContext(params.context);
  return toolContext.decide(params.toolName, params.input);
}
```

### 决策逻辑
`ToolPermissionContext` (`src/core/permissions/context.ts`) 维护了当前的运行模式。有趣的是，Claude Code 定义了几种不同的"信任模式"：

- **default**: 默认模式，大部分敏感操作需要 `ask`。
- **plan**: 规划模式，只思考不执行（但源码中似乎对某些只读工具是 allow 的）。
- **act** (或 code): 允许执行大部分代码编辑。
- **bypass**: 越狱模式（开发调试用），全自动 allow。

具体的规则匹配逻辑在 `decide` 方法中：

```ts
// src/core/permissions/context.ts (逻辑还原)
decide(toolName, input) {
  // 按照优先级：Deny规则 > Ask规则 > Allow规则 > 模式默认行为
  if (matchRule(denyRules)) return { behavior: "deny" };
  if (matchRule(askRules)) return { behavior: "ask" };
  if (matchRule(allowRules)) return { behavior: "allow" };

  // 模式兜底
  if (mode === "dontAsk") return { behavior: "deny" }; // 激进安全
  return { behavior: "ask" }; // 默认安全
}
```

## 4. 集成：Runtime 中的拦截

在上一篇提到的 `AgentRuntime` 执行循环中，工具执行前会显式调用这个检查。

```ts
// src/core/agent/runtime.ts
private async runLocalTool(toolUse: ToolUseBlock): Promise<ToolResultBlock> {
  // ...
  // 核心拦截点
  const decision = await this.maybeCanUseTool(tool, input, toolUse.id);
  
  if (decision.behavior === "deny") {
    // 记录拒绝事件，并不再执行工具
    this.permissionDenials.push({ ... });
    return {
      is_error: true,
      content: decision.message ?? "Permission denied" 
    };
  }

  // 如果是 ask，在 maybeCanUseTool 内部通常会挂起等待用户输入
  // (注：Ask 的具体交互实现通常由上层 CLI 注入的回调处理)

  if (decision.behavior === "allow") {
    // 执行工具
    return tool.run(input);
  }
}
```

这里的 `maybeCanUseTool` 是个依赖注入的接口。runtime 自身不知道怎么"问用户"，它只知道"我要检查权限"。CLI 层会注入一个函数，当结果是 `ask` 时，在这个函数里打印 "(Y/n)" 并阻塞等待用户敲键盘。

## 5. 逆向复盘

这块代码的恢复让我对 Agent 的安全性有了更深的理解：

1.  **关注点分离**：执行器（Runtime）不应包含安全策略。安全策略应该由独立的 Policy Engine 负责。
2.  **纵深防御**：Sandbox 负责"物理隔离"（文件/网络），Permissions 负责"逻辑授权"（意图确认）。两者缺一不可。
3.  **默认安全**：在 `context.ts` 的最后，如果不匹配任何规则，默认返回的一定是 `ask` 或 `deny`，绝不是 `allow`。这是安全系统的基本原则。

至此，Claude Code 的三大核心——**Skills (输入)**、**Runtime (循环)**、**Permissions (边界)**——都已经拆解完毕。这套架构展现了一个工业级 Agent 应有的严谨：既有大模型的灵活，又有传统软件工程的稳固。
