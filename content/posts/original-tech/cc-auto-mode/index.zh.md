---
title: "Claude Code auto mode 是咋实现的"
date: 2026-06-14
tags: ["Claude Code", "Agent", "权限控制", "Prompt Injection", "AI Safety"]
categories: ["Original Tech"]
---

本文是阅读这篇博文后的简单记录与思考： [How we built Claude Code auto mode: a safer way to skip permissions](https://www.anthropic.com/engineering/claude-code-auto-mode)


使用Claude Code时，经常会遇到各种权限的申请，也就是human-in-the-loop，导致提了一个复杂需求给CC后，如果人离开电脑了任务就会卡在这个申请上。所以网友各种hack，使用[happy](https://github.com/slopus/happy)或者是远程登陆软件连到自己电脑上。

CC官方新增了auto mode功能，尽可能的利用AI的能力来帮助简化审批流程。避免`--dangerously-skip-permissions`带来的灾难性破坏

## 为什么可以做
系统基于一个数据前提：**CC用户通过了93%的权限申请。**  

这是一个很关键的前提条件，他给auto mode的实现提供了很大的可能性：仅有很小比例的情况下是需要拒绝的，如果反过来**CC用户通过了7%的权限申请。**，那么auto mode需要处理的情况就复杂的多了。


## 怎么做的
### prompt注入探针
1. CC团队在server端实现了一个运行在服务器端的prompt注入探针，它扫描tool消息，并在发现风险时插入warning消息  
`为啥需要这个prompt注入探针？`原因是两层决策机制没有把tool result作为输入，这可能导致prompt注入成功，这时候如果有warning消息，则agent被劫持的概率会大大降低。

### 两层的决策机制
2. 在client端实现了两层的决策机制，使用Claude Sonnet 4.6 进行判断，第一层是简单输出是否拦截，当拦截时进入第二层，使用更多的reasoning token来给出更准确的判断。输入是user消息+tool消息，干掉了assistant消息和tool result    
`为啥是两层？`因为大部分情况下不会拦截，当第一层拦截时再花费更多token去进行判断。效果是一样的，可以节省更多成本。  
`为啥干掉assistant消息和tool result？`因为assistant消息可能会误导LLM，tool result可能会含有注入的有毒信息

结合服务端与客户端的两种自动审查，劫持主agent会变得很难

## auto mode下拦截后发生了什么
1. 不执行tool，并提示模型：find a safer path, don't try to route around the block.
2. 如果连续三次被拦截，或者是累积20次被拦截，则让人类来决策
3. 如果是headless模式，则退出执行

这还挺聪明的，如果发生了拦截了不该拦截的tool调用，则多次重试可以将false positive的情况降到最低

## 怎么评价，结果怎样

先看效果：日常流量的误拦率从 8.5% 压到了 0.4%，但 overeager 动作还是有 17% 会漏过去。

`为啥漏拦这么高？`因为难点不在「看没看见危险」，而在「用户到底授没授权」。force-push、删分支模型都识别得出来，但「清理一下分支」这句话算不算允许批量删除？分类器在这条边界上经常拿不准，prompt 工程暂时也没招。

`那它到底值不值得用？`得看跟谁比。跟 `--dangerously-skip-permissions` 裸奔比，这是实打实的进步；跟逐条认真审批比，反而是退步——你把自己的判断换成了一个会偶尔出错的分类器。所以它不是给所有人的，而是接住前文那批被 approval fatigue 折磨、又不敢裸奔的用户，做 happy 这类 hack 的官方替代品。
