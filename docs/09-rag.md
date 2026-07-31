# 09. RAG 管线

所有 RAG 相关代码集中在 `backend/app/rag/`。

```
app/rag/
  pdf_convert.py   PDF → Markdown（mistral / gemini / text 可插拔）
  crawl.py         网址抓取（同目录 BFS、robots、SSRF 防护、页→章节）
  repo.py          GitHub 仓库（浅克隆 → 过滤 → 代码分窗成 fenced 段）
  extract.py       其他格式（md/docx/pptx/xlsx 走 markitdown）
  chunking.py      语义切分 + 父子分块
  dense.py         稠密向量检索（pgvector 余弦距离）
  sparse.py        BM25 稀疏检索（按 workspace 建索引并缓存）
  fusion.py        RRF 融合
  rerank.py        重排（jina / cohere / voyage / llm / none 可插拔）
  retriever.py     混合检索编排：dense + sparse → RRF → rerank
  evaluation/      评估集、指标、运行器
```

LangGraph 的 agent 图不在这里，统一放 `backend/app/agents/`（每个 agent 一个模块）：
`qa.py`、`planner.py`、`quiz.py`、`outline.py`（单次调用，非图）；Lecture / Search 后续加入。分界很简单：**agent 负责决策流程，
rag 负责把上下文找出来**。

## 检索链路

```
query
 ├── dense：问题向量化 → pgvector 检索子块 → 归并到父块（取 30 个候选）
 └── sparse：BM25 词项匹配 → 归并到父块（取 30 个候选）
              ↓
          RRF 融合（k=60，按排名而非分数合并）
              ↓
          Reranker 对候选重排，取 top-6
              ↓
          父块全文作为上下文交给 LLM
```

**为什么用 RRF 而不是加权求和**：余弦距离和 BM25 词频权重量纲完全不同，直接加权需要为每个语料重新调参。RRF 只用排名位置，无需归一化。

**为什么保留 BM25**：稠密向量对精确标识符不敏感——查 `FLM-419` 或 `Kaczmarz` 时向量只能匹配到"话题相近"，BM25 直接命中词项本身。实测数据见下。

## 图片

OCR 返回的插图会存到 `storage/<workspace>/images/<source>/`，父块的 `images` 字段记录本节引用了哪几张。
检索时图片元数据随父块一起返回；生成阶段 `agents/vision.py` 把图片附到 prompt 上（数量由
`MAX_ANSWER_IMAGES` 限制，`ANSWER_WITH_IMAGES=false` 可整体关闭）；前端通过带鉴权的
`/workspaces/{id}/images/{source_id}/{image_id}` 取图，在引用卡片里展示。

这一步不是可有可无：讲义里单位球、分离超平面这类图承载的信息，正文并不会重复描述一遍。

## 成本计量

一次提问会散成多个调用：查询向量化、相关性判定、重排、生成。`app/services/usage.py`
用 contextvar 把它们汇总成一笔账，随 SSE 的 `usage` 事件下发并落库到 `messages.usage`，
前端在回答下方用小字显示，可展开看逐项分解。

价格是配置项而非硬编码：`MODEL_PRICES` / `RERANK_PRICES` 以**合并**方式覆盖内置表，
加一个模型不会把默认的挤掉。**价格表里没有的模型只报 token、不猜成本**——
宁可显示"未知"，也不给一个看起来精确实则编造的数字。

## 评估

以下命令是保留的 RAG 专项实验入口；日常产品评测统一由
[AI Evaluation Platform](10-evaluation-platform.md) 的 `rag_retrieval` suite 持久化并与 baseline 比较。

```bash
# 从语料生成评估集（问题由 LLM 从父块生成，该父块即 ground truth）
uv run python -m app.rag.evaluation.runner build --workspace Optimisation --size 30

# 只跑检索指标（快，不调用评审模型）
uv run python -m app.rag.evaluation.runner run --no-judge

# 完整评估（含 faithfulness / correctness，LLM 评审）
uv run python -m app.rag.evaluation.runner run --variant dense_only --variant hybrid_rrf_rerank
```

结果写入 `logs/rag-eval-<时间戳>.{json,md}`。

**指标定义**

| 指标 | 计算方式 |
|---|---|
| Recall@k | 生成该问题的父块是否出现在 top-k。精确计算，无需评审 |
| MRR | 该父块排名的倒数 |
| Faithfulness | LLM 评审把回答拆成事实断言，统计有多少条被检索到的上下文支持 |
| Correctness | LLM 评审对比回答与参考答案，判 correct / partial / incorrect |
| decline_rate_out_of_scope | 资料外问题被正确拒答的比例 |

**评估集的已知局限**：问题由 LLM 从 chunk 生成，早期版本用词与原文高度重合，导致 dense_only 的 recall@3 就到 1.0，天花板效应掩盖了各组件的差异。现在的生成 prompt 要求改写措辞、不复用原文句式，指标才拉开差距。这仍是合成评估集，不能替代真实用户查询。
