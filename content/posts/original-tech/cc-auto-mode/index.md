---
title: "How Claude Code Auto Mode Works"
date: 2026-06-14
tags: ["Claude Code", "Agent", "Permission Control", "Prompt Injection", "AI Safety"]
categories: ["Original Tech"]
---

This is a short note and some thoughts after reading this post: [How we built Claude Code auto mode: a safer way to skip permissions](https://www.anthropic.com/engineering/claude-code-auto-mode)

When using Claude Code, you often run into permission prompts, also known as human-in-the-loop approval. If you give CC a complex task and then walk away from your computer, the task may get stuck waiting for one of these approvals. So people have come up with all kinds of hacks, such as using [happy](https://github.com/slopus/happy) or remote desktop software to connect back to their machines.

CC has now officially added auto mode, which uses AI as much as possible to simplify the approval flow while avoiding the potentially catastrophic damage caused by `--dangerously-skip-permissions`.

## Why This Is Possible

The system is built on one data premise: **CC users approve 93% of permission requests.**

This is a crucial premise. It gives auto mode a lot of room to work: only a small percentage of cases need to be rejected. If the situation were reversed and **CC users approved only 7% of permission requests**, auto mode would have to handle a much more complex problem.

## How It Works

### Prompt Injection Probe

1. The CC team implemented a server-side prompt injection probe. It scans tool messages and inserts warning messages when it detects risk.

`Why do we need this prompt injection probe?` Because the two-layer decision mechanism does not take tool results as input. That could allow prompt injection to succeed. If a warning message is present, the chance of the agent being hijacked drops significantly.

### Two-Layer Decision Mechanism

2. On the client side, CC implements a two-layer decision mechanism using Claude Sonnet 4.6. The first layer simply decides whether to block. If it decides to block, the request enters the second layer, which spends more reasoning tokens to make a more accurate judgment. The inputs are user messages plus tool messages, with assistant messages and tool results removed.

`Why two layers?` Because most requests will not be blocked. Only when the first layer blocks does the system spend extra tokens on a more careful judgment. The effect is the same, but the cost is lower.

`Why remove assistant messages and tool results?` Because assistant messages may mislead the LLM, and tool results may contain toxic injected content.

With both server-side and client-side automatic review, hijacking the main agent becomes much harder.

## What Happens After a Block in Auto Mode

1. The tool is not executed, and the model is told: find a safer path, don't try to route around the block.
2. If it is blocked three times in a row, or 20 times in total, the decision is handed back to a human.
3. If it is running in headless mode, execution exits.

This is pretty clever. If a tool call is blocked by mistake, retries can reduce the false-positive impact as much as possible.

## How to Evaluate It, and How Well It Works

First, the results: on everyday traffic, the false block rate dropped from 8.5% to 0.4%, but 17% of overeager actions still slip through.

`Why is the miss rate so high?` Because the hard part is not "can we see the danger?" but "did the user actually authorize this?" The model can recognize force-pushes and branch deletions, but if the user says "clean up the branches," does that authorize deleting many branches? The classifier often struggles with this boundary, and prompt engineering does not have a good answer for now.

`So is it worth using?` It depends on what you compare it against. Compared with running naked with `--dangerously-skip-permissions`, this is a real improvement. Compared with carefully approving each request one by one, it is actually a step backward: you are replacing your own judgment with a classifier that occasionally makes mistakes. So it is not for everyone. It is meant for the group mentioned earlier: users worn down by approval fatigue, but who still do not dare to run fully naked. For them, it is the official alternative to hacks like happy.
