---
title: "深度解析 MiroThinker 1.7：长时序推理 Agent 的工程优化与护栏设计"
date: 2026-04-29
tags: ["MiroThinker", "Agent", "LLM", "长时序推理", "MCP", "上下文管理", "容错机制"]
categories: ["Original Tech"]
---

> MiroThinker 1.7 在长问题推理领域取得了SOTA的成绩，优秀的成绩是由强Model与扎实的Harness共同组成的，本文是对其Harness实现中的关键工程优化的记录。

## 1. 模型行为护栏
MiroThinker中实现了多种预防模型出错、幻觉的机制。
### 1.1 Rollback机制
当模型某一回合的输出不符合预期时，假装这一回合没发生过，让模型重新生成。它的核心思想是让“失败的一步”不消耗上下文与推理步数。  

具体的做法简单直接：把模型上一轮的回答扔掉，这一轮重新来过
```python
message_history.pop()            # 把模型刚刚的 assistant 消息扔掉
turn_count -= 1                  # turn 预算回退
consecutive_rollbacks += 1       # 连续失败计数 +1
```
> 📍 源码：[`orchestrator.py:210`](https://github.com/MiroMindAI/MiroThinker/blob/370f9836/apps/miroflow-agent/src/core/orchestrator.py#L210-L218)

#### 效果：
- 对模型而言：这次糟糕的输出从未存在，下次重新生成不会被它"污染"思路
- 对预算而言：max_turns=200 是有效推进次数；rollback 不计入
- 对死循环防护而言：total_attempts（= max_turns + 200）和 consecutive_rollbacks（≤5）这两个独立计数器仍在递增，防止无限重来

#### 4 类 Rollback 触发点

| 触发点 | 关联函数 | 触发条件详情 | 触发结果 |
| :--- | :--- | :--- | :--- |
| **1. MCP 标签格式错误** | [`_handle_response_format_issues`](https://github.com/MiroMindAI/MiroThinker/blob/370f9836/apps/miroflow-agent/src/core/orchestrator.py#L180) | 模型本该使用结构化 `tool_call`，但输出了 `<mcp:...>` 之类的纯文本标签（命中 [`mcp_tags`](https://github.com/MiroMindAI/MiroThinker/blob/370f9836/apps/miroflow-agent/src/utils/prompt_utils.py#L69) 关键字）。 | Rollback |
| **2. 拒答关键词** | [`_handle_response_format_issues`](https://github.com/MiroMindAI/MiroThinker/blob/370f9836/apps/miroflow-agent/src/core/orchestrator.py#L180) | 模型输出类似 `"As an AI..."`、`"I cannot..."` 的拒绝回答句式开头（命中 [`refusal_keywords`](https://github.com/MiroMindAI/MiroThinker/blob/370f9836/apps/miroflow-agent/src/utils/prompt_utils.py#L78)）。 | Rollback |
| **3. 重复查询检测** | [`_check_duplicate_query`](https://github.com/MiroMindAI/MiroThinker/blob/370f9836/apps/miroflow-agent/src/core/orchestrator.py#L257) | 同一个 agent 用同一个 tool 查询过同一个 query（按 `cache_name = agent_id + tool_name` 隔离的缓存）。<br><br>**[各工具单独的"指纹"提取逻辑](https://github.com/MiroMindAI/MiroThinker/blob/370f9836/apps/miroflow-agent/src/core/tool_executor.py#L102)：**<br>• `google_search` → 提取 `arguments["q"]`<br>• `scrape_website` → 提取 `arguments["url"]`<br>• `scrape_and_extract_info` → 提取 `url + info_to_extract` | Rollback，<br>逼模型换个角度 |
| **4. 工具结果错误** | [`should_rollback_result`](https://github.com/MiroMindAI/MiroThinker/blob/370f9836/apps/miroflow-agent/src/core/tool_executor.py#L240) | 工具返回值满足以下任一情况：<br>1. `"Unknown tool: ..."`<br>2. `"Error executing tool ..."`<br>3. Google 搜索返回 `organic: []`（空结果） | Rollback，<br>让模型重新规划 |

### 1.2 简易错误纠正
实现了“已知模型错误”明确写入代码的硬护栏

#### [`fix_tool_call_arguments`](https://github.com/MiroMindAI/MiroThinker/blob/370f9836/apps/miroflow-agent/src/core/tool_executor.py#L68) —— 自动纠正模型常犯的参数错误

在 [`tool_executor.py`](https://github.com/MiroMindAI/MiroThinker/blob/370f9836/apps/miroflow-agent/src/core/tool_executor.py) 中，针对模型常犯的参数命名错误进行了**自动映射纠正**。

**典型纠正场景：**
- **`scrape_and_extract_info`**：将错写的 `description` 或 `introduction` 自动转为 `info_to_extract`。
- **`run_python_code`**：
  - 将错写的 `code` 自动转为 `code_block`。
  - 若漏传 `sandbox_id`，自动填入 `"default"`，触发无状态（stateless）降级执行。

#### [`INVALID_SANDBOX_IDS`](https://github.com/MiroMindAI/MiroThinker/blob/370f9836/libs/miroflow-tools/src/miroflow_tools/mcp_servers/python_mcp_server.py#L31) —— Sandbox ID 幻觉黑名单

在 [`python_mcp_server.py`](https://github.com/MiroMindAI/MiroThinker/blob/370f9836/libs/miroflow-tools/src/miroflow_tools/mcp_servers/python_mcp_server.py) 中，梳理了 21 个模型常“凭直觉”瞎编的无效 ID（如 `"default"`, `"sandbox"`, `"auto"` 等）作为黑名单。

一旦命中该黑名单，系统会返回**友好报错**，或自动**降级到无状态（stateless）执行**。

## 2. 工具设计
### 2.1 MCP 子进程隔离
每个工具都是独立的 FastMCP 进程，主进程通过 `stdio` 与其通信。这样做的好处：
- **高容错性**：单个工具崩溃不会影响主循环运行。
- **环境隔离**：工具可以使用任意 Python 依赖，不会污染主程序的运行环境。
- **并发友好**：天然支持多工具并发执行。

### 2.2 Server 内细粒度屏蔽（`tool_blacklist`）
支持在 YAML 配置中对工具进行细粒度屏蔽（黑名单机制）：
```yaml
tool_blacklist:
  - ["search_and_scrape_webpage", "sogou_search"]
  - ["tool-python", "download_file_from_sandbox_to_local"]
```

### 2.3 Sub-Agent 统一工具抽象（[`expose_sub_agents_as_tools`](https://github.com/MiroMindAI/MiroThinker/blob/370f9836/apps/miroflow-agent/src/config/settings.py#L383)）
Sub-agent 在系统中并非一种特殊类型，而是以 `agent-` 作为名称前缀，直接注册到主 Agent 的工具列表中。主 Agent 调用 `agent-browsing(subtask=...)` 与调用普通工具毫无区别，实现了系统级的统一抽象。

## 3. 上下文参数处理
### 3.1 运行时压缩：[`_remove_tool_result_from_messages`](https://github.com/MiroMindAI/MiroThinker/blob/370f9836/apps/miroflow-agent/src/llm/base_client.py#L124)
这个优化点在论文中也有提及。
在标准 ReAct 里，所有工具输出都会保留在历史里，导致上下文很快被撑爆。MiroThinker 改成只保留最近 K 次工具的输出，但完整保留所有"思考"和"行动"记录。这样既省上下文，又不丢推理链条——这是它能支持"几百步工具调用"的关键工程技巧。  
这可能会丢一些KV cache，但对于浏览网页这种特别长的工具调用结果的场景，是一个值得的权衡。

```python
# Preserve the message structure but replace content
if isinstance(msg.get("content"), list):
    # For Anthropic format
    msg["content"] = [
        {
            "type": "text",
            "text": "Tool result is omitted to save tokens.",
        }
    ]
else:
    # For OpenAI format
    msg["content"] = "Tool result is omitted to save tokens."
```
> 📍 源码：[`base_client.py:202-218`](https://github.com/MiroMindAI/MiroThinker/blob/370f9836/apps/miroflow-agent/src/llm/base_client.py#L202-L218)
### 3.2 错误总结：[`generate_failure_summary`](https://github.com/MiroMindAI/MiroThinker/blob/370f9836/apps/miroflow-agent/src/core/answer_generator.py#L170)

3.1 是单任务内的"机械裁剪"，但当任务跑满 `max_turns` 或上下文逼近上限仍没拿到答案时，就需要更彻底的压缩——让模型自己把整段对话浓缩成一段失败经验。

具体做法是：在历史末尾追加一个 summary prompt，调一次 LLM 让它输出结构化总结：

```python
failure_summary_history.append({"role": "user",      "content": FAILURE_SUMMARY_PROMPT})
failure_summary_history.append({"role": "assistant", "content": FAILURE_SUMMARY_ASSISTANT_PREFIX})
```
> 📍 源码：[`answer_generator.py:202-213`](https://github.com/MiroMindAI/MiroThinker/blob/370f9836/apps/miroflow-agent/src/core/answer_generator.py#L202-L213) ｜ Prompt 模板：[`FAILURE_SUMMARY_PROMPT`](https://github.com/MiroMindAI/MiroThinker/blob/370f9836/apps/miroflow-agent/src/utils/prompt_utils.py#L44) / [`FAILURE_SUMMARY_ASSISTANT_PREFIX`](https://github.com/MiroMindAI/MiroThinker/blob/370f9836/apps/miroflow-agent/src/utils/prompt_utils.py#L61)

总结被强制归到 4 类之一：
- **incomplete**：步数不够，没跑完
- **blocked**：工具一直失败，卡住了
- **misdirected**：方向走错了
- **format_missed**：答出来了但格式错

#### 关键设计：摘要不喂回当前会话，而是重启任务

生成的 `failure_experience_summary` 不会注入到当前对话让模型继续，而是被抛回 pipeline 外层，拼接到**下一次完整任务**的 `task_description` 后面：

```
原始任务 + "上次失败经验：[incomplete] 尝试了 X，找到了 Y，卡在 Z..."
```

新一次 attempt 拿到全新的 256K 窗口，但 prompt 里能看到上次（们）踩过的坑。默认重试 3 次，最后一次 `is_final_retry=True` 关闭"避免瞎猜"逻辑，强制走兜底答案。

这样设计的好处是 reasoning chain 不会被"半截压缩"污染——要么完整跑、要么完全重启，避免了滚动 summary 常见的"模型对自己编的摘要再二次脑补"问题。

### 3.3 assistant prefill [`continue_final_message`](https://github.com/MiroMindAI/MiroThinker/blob/370f9836/apps/miroflow-agent/src/llm/providers/openai_client.py#L153-L155)

```python
  if messages_for_llm[-1].get("role") == "assistant":
      params["extra_body"]["continue_final_message"] = True
      params["extra_body"]["add_generation_prompt"] = False
```
> 📍 源码：[`openai_client.py:153-155`](https://github.com/MiroMindAI/MiroThinker/blob/370f9836/apps/miroflow-agent/src/llm/providers/openai_client.py#L153-L155)
  
当 message_history 末尾是 assistant 消息（比如格式纠错时塞了个开头），LLM 直接续写而不是从零开始。利用 vLLM/SGLang 的扩展能力做了精细控制。

message_history 末尾出现 assistant 消息有几个场景：

- 场景 1：Failure summary 引导
- 场景 2：Rollback 后的重生成边界
- 场景 3：截断恢复
    - finish_reason == "length" 触发 max_tokens *= 1.1 重试时 —— 上一次的 assistant 输出被截断了，下次调用如果保留这条 truncated assistant，加上continue_final_message=True，模型就能接着被截断的地方往下补完，不用重新生成前面已经写过的部分。