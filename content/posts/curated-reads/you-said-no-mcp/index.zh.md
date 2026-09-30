---
title: "“你不是说不支持 MCP 吗！”"
date: 2026-09-30
author: "Earendil Engineering"
categories: ["Curated Reads"]
tags: ["Pi", "MCP", "Codemode", "AI Agents", "JavaScript", "工具编排"]
draft: false
description: "Earendil 工程团队解释 Pi 为何从拒绝 MCP 转向将其纳入核心：工具组合、结构化数据、智能工具发现，以及运行在 Agent 框架侧的 Codemode 沙箱。"
---

> **原文：** [“You Said No MCP!” — Earendil](https://earendil.com/posts/you-said-no-mcp/)
>
> **作者：** Earendil Engineering · **原文发布日期：** 2026 年 9 月 29 日
>
> **翻译说明：** 本文根据原文 PDF 译为中文，翻译由 AI 完成。正文保留作者的第一人称表述，文中的“我们”指 Earendil 团队。产品行为与判断以原文发表时为准；文末演示已根据补充的会话记录完整呈现，交互式回放请访问原文。

如果你过去访问过 [pi.dev](https://pi.dev)，就会看到一条颇为自豪的声明：Pi 不支持 MCP。如果你听过我们谈论 Pi 的播客，也会发现我们不止一次对 MCP 表达过不以为然的态度。[Mario 还专门写过一篇文章](https://mariozechner.at/posts/2025-11-02-what-if-you-dont-need-mcp/)。然而，现在升级 Pi 后，你会发现 MCP 已经成为受支持的功能。发生了什么？

## 事物会变

首先要记住，[世界并非一成不变](https://lucumr.pocoo.org/2016/11/5/be-careful-about-what-you-dislike/)。过去一年，我们一直在关注 MCP，而今天的 MCP 已经不是过去的 MCP 了。不过，单凭这一点，还不足以成为把它纳入核心的理由。你也知道，Pi 拥有很出色的扩展生态，MCP 完全可以做成一个扩展，对吧？甚至可以是一个得到 Earendil 官方认可的扩展。

没错，MCP 确实可以作为扩展存在，[而且之前就是如此](https://github.com/nicobailon/pi-mcp-adapter)。如今 MCP 成为核心的一部分，是我们坐下来共同讨论、重新思考后的结果。

## 到底发生了什么变化？

我们把 MCP 纳入核心，不只是因为 MCP 本身发生了变化，还因为我们发现，为支持它而需要做出的改动具有普遍的价值。例如，我们为 MCP 做的改动，也让在 Pi 中使用 Jev 变得更加容易。归根结底，Pi 所需要的东西与 MCP 所需要的相当接近：一个以解释器形式提供、可供操作的沙箱。

尽管 MCP 在很多方面都有了改善，但也有不少问题依旧存在。MCP 最大的问题仍然是难以组合。即使有了 Codemode——一个用来组合工具调用的精巧小沙箱——MCP 在这方面也还没有完全兑现承诺。不过，到了现在，这更多是现有 MCP 服务器，以及不同 Agent 运行框架（harness）与这些服务器交互方式的问题，而不完全是 MCP 本身的问题。

许多 MCP 服务器仍然面向那种把所有工具直接塞进上下文的运行框架来设计，并试图通过返回文本，在服务器这一侧提高 Token 使用效率。我们现在更倾向于把 MCP 理解成一种带有智能工具发现能力、更加接近 OpenAPI 的东西。这意味着，工具应该返回结构化数据，也应该能够通过自身的文档和描述被发现。

CLI 之所以如此好用，是因为 Agent 和模型能够利用高效的 Bash 写法，把不同操作串联起来。但从根本上说，MCP 没有理由做不到这一点。Pi 中的 MCP，就是把这些工具暴露给一个 JavaScript 沙箱；Codex 等其他运行框架也是这么做的。

## 现代大语言模型中的 MCP

这自然会引出一个问题：为什么我们不直接做 Codemode，而不引入 MCP？部分答案与 Pi 目前如何表达工具有关。最近几个月，我们做了大量工作，让 Pi 能够适配新模型提供的能力，包括延迟加载工具、在对话中途插入系统消息，以及调整推理级别。不过，我们还没有升级工具配置体系，使其更好地扩展到这些新能力。

在 Codemode 的世界里，需要决定一个工具是直接对大语言模型可用，还是只对模型使用 Codemode 的那一部分可用。普通的 MCP 扩展无法从 Pi 的工具配置体系中获得足够的元数据，因此难以把这种体验做好。所以，我们需要确保工具可以被配置为延迟加载，或者仅供 Codemode 使用。

虽然我们本可以只把这些元数据接好，以便支持更完善的 MCP 扩展，但我们也认为，MCP 与 Codemode 结合，解决了 MCP 过去相当多的问题。我们相信，要对一件事产生积极影响，最好的方式就是拥抱它。

尽管我们认为，如今的 MCP 比以往任何时候都更成熟，但服务器和使用模式仍有改善空间。因此，我们希望参与讨论，帮助塑造它，让它能在小型运行框架中良好运作，而不是站在一旁观望。

## 什么是 Codemode？

前面说了这么多 Codemode，也该解释一下它究竟是什么了。运行框架执行工具时，通常有两个执行位置：一个是运行 Bash 的地方，另一个是框架中 Agent 循环运行的地方。这两侧的信任级别很不一样。框架的循环往往运行在受信任的环境中，而它执行的工具则经常运行在一个不那么受信任的沙箱里。

Codemode 的特别之处在于，它运行在框架所在的一侧。理解它最好的方式，是把它看作一种编排和协调工具调用的机制。它提供一个沙箱，让 Agent 能够更灵活地决定工具调用的顺序，并用 JavaScript 把这些调用组合起来。由于 Codemode 也运行在框架这一侧，它的状态会作为会话记录的一部分保存，而不是保存在文件系统中。

理论上，任何语言都可以胜任，但 JavaScript 相当有吸引力：精简的 JavaScript 实现可以作为 WASM 二进制分发，同时提供合理程度的保护。

在 Pi 中，配置 MCP 后，Codemode 会自动加载；你也可以把它加入配置，作为默认工具。只要让 Pi 重新配置自己、启用 Codemode 就行了！随后，你可以用它做一些很有意思的事情，远不止 MCP。例如，当你使用提供 Jev 的服务商登录后，就可以提出这样的请求：

> 通过 Codemode 使用 typesafe/jev，找出我们的问题跟踪系统中最感到沮丧的 20 位评论者。

它会巧妙地把 Linear MCP 和 Jev 等能力组合起来，直接在 Pi 中完成分析，而不浪费任何上下文。

**会话回放**

> 通过 Codemode 使用 typesafe/jev，找出我们的问题跟踪系统中最感到沮丧的人。

我会从 Linear 获取尚未关闭的问题，让 Jev 评估每个讨论串的情绪，每次并行处理四个。

**codemode**

```javascript
const { issues } = await tools.mcp__linear__list_issues({
  team: "Pi", state: "open", limit: 250,
});
const jev = await models.getModelOfType(
  "classifier", "cloudflare-workers-ai", "typesafe/jev",
);
const questions = {
  frustration: {
    type: "choice",
    instructions: "Judge ONLY the emotional tone of the people writing. " +
      "Ignore how severe the bug is.",
    criteria: {
      none: "Neutral, factual, or friendly, even about a serious bug",
      mild: "Explicit annoyance, impatience, or disappointment",
      high: "Clearly angry, exasperated, sarcastic, or fed up",
    },
  },
};

const results = [];
let next = 0;
async function worker() {
  while (next < issues.length) {
    const issue = issues[next++];
    const { comments } = await tools.mcp__linear__list_comments({
      issueId: issue.identifier,
    });
    const c = await models.classify(jev, { state: { ...issue, comments }, questions });
    results.push({ id: issue.identifier, title: issue.title, ...c.answers.frustration });
  }
}
await Promise.all([worker(), worker(), worker(), worker()]);
store("frustration", results);

const score = (r) => r.probabilities.mild * 0.5 + r.probabilities.high;
const counts = {};
for (const r of results) counts[r.choice] = (counts[r.choice] ?? 0) + 1;
const flagged = results.filter((r) => r.choice !== "none");
flagged.sort((a, b) => score(b) - score(a));
return {
  total: results.length,
  counts,
  flagged: flagged.map((r) => `${r.id} ${r.title}`),
};
```

```text
... (327 earlier calls, ctrl+o to expand)

✓ mcp__linear__list_comments {"issueId":"PI-4945"} 347ms
✓ mcp__linear__list_comments {"issueId":"PI-4748"} 324ms
✓ models.classify cloudflare-workers-ai/typesafe/jev 759ms
✓ models.classify cloudflare-workers-ai/typesafe/jev 783ms
✓ mcp__linear__list_comments {"issueId":"PI-4714"} 255ms
✓ mcp__linear__list_comments {"issueId":"PI-3200"} 354ms
✓ models.classify cloudflare-workers-ai/typesafe/jev 756ms
✓ models.classify cloudflare-workers-ai/typesafe/jev 750ms
```

```json
{
  "total": 167,
  "counts": {
    "none": 156,
    "mild": 11
  },
  "flagged": [
    "PI-7740 TUI after /reload does not follows custom tool's re…",
    "PI-9566 context size defaults to 128k despite the real size…",
    "PI-10031 Pi sporadically stuck in \"Working...\" when thinking…",
    "PI-4714 [feature] /update TUI command instead of pi update",
    "PI-5916 Support provider extensions with model aliases and…",
    "PI-7547 [Windows] [sink-thread] How do you use Pi on window…",
    "PI-6907 Add an Installation section to the README",
    "PI-6212 Proposal: Bedrock path should honor `compat.forceAd…",
    "PI-9932 before_agent_start: forced system prompt keeps tool…",
    "PI-7730 High CPU usage on Mac OS with long session",
    "PI-6600 pi update --extensions and npm scripts: npm 11.16.0…"
  ]
}
```

问题跟踪系统中的大多数讨论都很平静。Jev 将 **167** 个尚未关闭的问题中的 **156** 个评为情绪中性，**11** 个评为轻度沮丧，没有任何一个被评为高度沮丧。

最明显的几个例子：

- `PI-6907`：README 中没有安装说明（“这让人很沮丧”）。
- `PI-10031`：在思考期间按下 Esc 后，Pi 卡在“Working...”状态。
- `PI-4714`：请求添加 `/update` 命令（“太让人头疼了”）。
- `PI-7730`：在 macOS 上长时间运行会话时，CPU 占用过高。

每个问题的评估结果都保存在 Codemode 的 `frustration` 中，因此我可以深入查看其中任意一个，而不必重新获取问题。

*这是一次 Pi 会话的精简回放。可在[原文](https://earendil.com/posts/you-said-no-mcp/)中查看并重播。*

之后，我们还会进一步介绍 Jev 和 Codemode 等内容。我们希望这篇文章能成为一个例子，展示随着世界不断变化，我们如何持续审慎地调整和更新 Pi。
