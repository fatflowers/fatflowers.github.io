---
title: "Claude Code 源码解析（二）：Skills 如何进入 System Prompt"
date: 2026-02-19
tags: ["Claude Code", "Agent", "System Prompt", "Skills", "TypeScript"]
categories: ["Original Tech"]
---

> 本文是 [Claude Code 源码逆向系列]({{< ref "/posts/original-tech/cc-source-1-restore" >}}) 的第二篇，聚焦 Skills 发现与 System Prompt 注入机制。

我最先关心的问题是：`AGENTS.md` 里的规则到底怎么进入模型上下文？

恢复后，这条链路大致是：

- `src/core/skills/agentsFile.ts`：从工作目录向上查找并读取 `AGENTS.md`
- `src/core/skills/prompt.ts`：解析可用 skill，并构造可注入的 prompt 片段
- `src/core/model/request.ts`：把 skills prompt 追加到 `system` 消息块
- `src/core/tools/skill.ts`：提供内置 Skill 工具，支持运行时查询/加载

一个典型的 TS 片段（示意，保留结构）是这样的：

```ts
// src/core/model/request.ts
if (params.skills && params.skills.trim()) {
  systemBlocks.push({
    type: "text",
    text: params.skills,
  });
}
```

对应伪代码：

```text
skillsPrompt = discoverSkillsFromAgentsFile(cwd)
if skillsPrompt exists:
  append skillsPrompt into system messages
send request to model
```

这块我有个明确取舍：先把 Skills 恢复成独立模块，不急着耦合进 `runCli` 主流程。原因很简单，Skills 的输入输出边界很清晰，独立后更容易做逐步校验，也更适合后续替换解析策略。

还有一个小细节很关键：我没有"全量递归读技能目录"，而是按 `AGENTS.md` 中可见引用做最小读取，避免把无关上下文灌进系统提示。

## 1. AGENTS.md 的发现机制

Claude Code 的技能系统以 `AGENTS.md` 为中心。这个文件起到了类似 `package.json` 或 `Makefile` 的作用，定义了当前工作区可用的"技能"（Skill）。

在 `src/core/skills/agentsFile.ts` 中，查找逻辑非常直观：它会从当前工作目录（CWD）开始，沿着目录树向上递归查找，直到找到 `AGENTS.md` 或到达根目录。

```typescript
// src/core/skills/agentsFile.ts

export async function findNearestAgentsFile(startDir: string, options: AgentsSearchOptions = {}) {
  const maxLevels = options.maxLevels ?? 12;
  let current = resolve(startDir);

  for (let i = 0; i <= maxLevels; i += 1) {
    const candidate = join(current, AGENTS_FILE_NAME);
    try {
      const content = await readFile(candidate, "utf8");
      return { path: candidate, content };
    } catch {
      const parent = dirname(current);
      if (parent === current) break;
      current = parent;
    }
  }

  return null;
}
```

这种设计允许开发者在项目根目录放置一个 `AGENTS.md`，以此控制整个项目范围内 Agent 的可用能力，也支持在子目录覆盖配置。

## 2. 技能定义的解析

找到 `AGENTS.md` 后，`src/core/skills/agentsParser.ts` 负责解析文件内容。它使用正则匹配特定的 Markdown 列表项格式：

```typescript
// src/core/skills/agentsParser.ts

const SKILL_LINE_REGEX = /^-\s+([a-zA-Z0-9._-]+):\s+(.*?)\s+\(file:\s*([^)]+)\)\s*$/gm;

export function parseSkillsFromAgentsInstructions(text: string): SkillDescriptor[] {
  const skills: SkillDescriptor[] = [];
  for (const match of text.matchAll(SKILL_LINE_REGEX)) {
    // ...提取 name, description, path
    skills.push({ name, description, path });
  }
  return dedupeSkills(skills);
}
```

这意味着 `AGENTS.md` 里的每一行形如 `- skill-name: description (file: path/to/skill.md)` 的列表项，都会被识别为一个可用技能。

## 3. 智能触发与按需加载 (Lazy Loading)

这是整个设计中最精妙的部分。Claude Code **不会**一股脑地把所有技能的内容都塞进 System Prompt。相反，它采用了一种"按需加载"的策略：

1.  **告知存在**：首先在 System Prompt 中列出所有**可用**技能的名称和简介。
2.  **检测意图**：检查用户的输入（Prompt），看是否触发了某个技能。
3.  **动态注入**：只有被触发的技能，其完整内容（`SKILL.md` 的正文）才会被读取并注入到 System Prompt 中。

触发逻辑在 `src/core/skills/agentsParser.ts` 的 `detectTriggeredSkills` 函数中实现：

```typescript
// src/core/skills/agentsParser.ts

export function detectTriggeredSkills(request: string, available: SkillDescriptor[]): SkillDescriptor[] {
  const normalized = request.toLowerCase();
  const out: SkillDescriptor[] = [];

  for (const skill of available) {
    const name = skill.name.toLowerCase();
    // 触发条件 1: 变量形式引用，例如 $git-commit
    const asVariable = `$${name}`;
    const mentionedByVariable = normalized.includes(asVariable);
    
    // 触发条件 2: 完整单词匹配
    const mentionedByWord = new RegExp(`\\b${escapeRegExp(name)}\\b`, "i").test(request);
    
    if (!mentionedByVariable && !mentionedByWord) continue;
    out.push(skill);
  }

  return dedupeSkills(out);
}
```

只要用户在对话中提到了技能名（通过 `$name` 显式引用或单词全匹配），系统就会认为该技能"被激活"。

## 4. System Prompt 的最终组装

`src/core/skills/prompt.ts` 将上述逻辑串联起来，生成最终注入给模型的文本块。

```typescript
// src/core/skills/prompt.ts

export async function buildSkillsSystemPrompt(options: BuildSkillsSystemPromptOptions) {
  // 1. 加载并解析 AGENTS.md
  const fromAgents = await loadSkillsFromNearestAgentsFile(options.cwd, ...);
  const available = fromAgents.descriptors;

  // 2. 检测哪些技能被触发
  const resolved = await resolveTriggeredSkills(options.request, available);
  
  const lines: string[] = [];
  lines.push("# Skills");
  lines.push("Available skills:");
  
  // 3. 列出所有可用技能（轻量级）
  for (const skill of available) {
    lines.push(`- ${skill.name} - ${skill.description} (file: ${skill.path})`);
  }

  // 4. 对触发的技能，注入详细内容（重量级）
  for (const skill of resolved.loaded) {
    lines.push("");
    lines.push(`## Skill: ${skill.descriptor.name}`);
    lines.push(skill.body.trimEnd());
    
    // 甚至会读取 Skill 中引用的其他文件内容
    // ...
  }

  return { text: lines.join("\n").trim(), ... };
}
```

这种两段式设计极大地节省了 Context Window，同时也让 Agent 保持专注，不会因为无关的技能说明而产生幻觉。

## 5. 总结

通过逆向还原，我们清晰地看到了 Claude Code 在处理 Skills 时的工程考量：

1.  **去中心化配置**：通过 `AGENTS.md` 实现目录级的技能管理。
2.  **上下文优化**：区分"可用列表"和"激活内容"，避免 System Prompt 爆炸。
3.  **显式触发**：通过简单的字符串匹配（`$variable` 或单词）来决定加载哪些上下文，既直观又高效。

下一篇，我们将深入 Claude Code 的心脏——**Subagent / Agent Runtime 的执行闭环**，看看它是如何调度这些工具并完成复杂任务的。
