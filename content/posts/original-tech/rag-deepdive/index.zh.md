---
title: "[WIP]深入拆解RAG技术"
date: 2026-06-02
categories: ["Original Tech"]
tags: ["RAG", "LLM"]
draft: false
---

RAG（Retrival Augumented Generation）技术对于扩充模型的知识，减少幻觉，提高实用性上非常重要。这篇文章主要是介绍一个RAG系统的各个工作环节，对应的可调节参数以及应用场景。



RAG系统的工作流程

Ingestion

- parsing（pdf html）
- chunking
  - metadata 来源、标题、章节、日期、权限
  - contextual retrieval
- Vector db indexing
  - dense model choice 模型维度、语言支持、是否非对称
  - sparse / bm25 indexing
- embedding/index 的更新机制

Retrieve

- Query processing
  - rewrite
  - expand
  - HyDE
  - query routing
- metadata filtering
- Hybrid search 融合 RRF 或加权
- rerank
- 上下文压缩 / 去重(把检索到的内容裁剪、去冗余,省 context window)
- chunk 扩展(检索命中小 chunk,但喂给 LLM 时取回它的相邻上下文或父文档,即 "small-to-big" / parent-document retrieval)

Generation

- no-answer / 兜底
- prompt 拼装(把 query + 检索结果组织成 prompt)

- 生成

- citation / attribution(标注答案出自哪个 chunk,可溯源)

- 幻觉检测 / groundedness 校验(答案是否真的基于检索内容)

Evaluation

- **端到端 / 系统级指标**——latency、成本(每 query 的 token 和检索开销)、命中率。
- 检索质量(recall@k、precision、MRR、nDCG)

- 生成质量(faithfulness/groundedness、answer relevance)

- 常用框架如 RAGAS。没有评估就没法调前面所有的参数(包括我们前面讨论的 chunking 阈值)。

