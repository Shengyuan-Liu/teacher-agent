# TeacherAgent

> 上传你的资料，我把它变成一门可以问、可以练、可以听讲的课。

想学一样新东西，手头往往有一堆散资料：教材 PDF、课件 PPT、官方文档站、一个开源仓库。TeacherAgent 把它们变成一门课——**所有回答都基于你给的资料，并标明出处**。

## 能做什么

- 📁 **喂什么都行** — PDF / Word / PPT / Excel / Markdown 文件，任意网址或文档站，甚至一个 GitHub 仓库链接
- 💬 **问答有据可查** — 每个回答都标出处，点击跳回原文；资料里没有的会直说，不编
- 🗺️ **生成学习计划** — 告诉它你的目标和每天能花多久，它排出分阶段的学习路径
- 📝 **自动出题** — 选择题、填空、简答；生成后再次检查答案是否能由资料支持
- ⏱️ **测验与复习** — 限时测验、客观/主观题判分、错题本和间隔重复复习
- 📈 **掌握度驱动** — 每次测验更新主题掌握度，自动影响后续出题和学习计划
- 📖 **系统讲解** — 基于资料、前置依赖和薄弱点生成结构化讲义与知识图谱
- 🎓 **互动讲课** — 在 Chat 中分节讲解、检验理解，插入问题后回到原进度，并可跨天恢复
- 🧭 **过程透明** — Router、检索、规划和出题的每一步结果、模型与用量都显示在调用链中
- 🧩 **复合问题多 Agent 协作** — 一次提问可同时拆给 Web 与 RAG Agent，再合并成带统一引用的回答
- 🔀 **Durable Typed Task DAG** — 显式依赖、拓扑并发、PostgreSQL 节点 checkpoint、worker lease、重启恢复与失败传播都可检查
- 📊 **Multi-Agent 消融实验** — 同一数据集比较四策略，发布 deterministic/live 的 P50/P95、bootstrap CI、token 与真实成本
- 📄 **PDF 精确定位** — 摄取时保留原始页码，引用可直接打开对应页面
- 🌐 **显式联网** — 只有用户主动要求或点击时才搜索，可先查看候选资料再决定是否入库
- 🧪 **统一质量评测** — 版本化 golden set、逐 case 结果、模型/成本快照、baseline 对比与 CI 回归 gate
- 🔭 **Agent 可观测与 Replay** — OpenTelemetry traces、Agent waterfall、按模型统计延迟/token/成本，并隔离重跑历史输入
- 🛡️ **成本与韧性治理** — 每轮预算预留、Smart→Fast 软降级、workspace 隔离缓存和 Redis 分布式熔断都进入调用链
- 🚦 **负载与故障证据** — 50 并发 DAG timeout/retry、cache stampede、预算争抢、circuit recovery 和 HTTP readiness SLO 均有版本化报告

问答、详细讲解、随堂练习、正式测试、错题复习和掌握度从 Chat 发起；Lecture 同时拥有与 Chat 平级的课程入口、历史列表和专属播放页，并继续复用 Chat 的插问与 Agent 编排能力。当 Router 无法确定方向时，会先给出可点击选项让用户决定。

> **当前进度**：Phase 1–4 主链路和 Phase 5 AI Engineering 生产化已完成：统一 Evaluation、OpenTelemetry/Replay、Durable Typed DAG、Multi-Agent 消融、Prompt Registry、安全红队、资源治理及负载/故障报告。实验没有假设多 Agent 必然更好，真实结果与边界见 [Benchmark](docs/reports/multi-agent-live.md) 和 [Resilience](docs/reports/agent-resilience.md)。

## 快速开始

需要先装好 Docker、[uv](https://docs.astral.sh/uv/)、Node 20+ 和 pnpm。

```bash
make setup   # 起数据库 + 装依赖 + 建表（只需跑一次）
make dev     # 启动
```

然后打开 http://localhost:5300 —— 页面上三项都是绿点就说明环境正常。

后端接口文档在 http://localhost:8000/docs。

无需模型即可运行快速质量 gate：

```bash
make eval-fast
make benchmark-report   # 30-repeat deterministic ablation + bootstrap CI
make resilience-report  # 50 并发 retry/cache/budget/circuit profile
```

本地查看完整 OpenTelemetry waterfall：

```bash
make observability-up       # Jaeger UI: http://localhost:16686
make observability-backend  # API 启用 OTLP export
```

## 配置

复制一份配置文件，填上你的 API key：

```bash
cp .env.example .env
```

| 变量 | 说明 |
|---|---|
| `LLM_PROVIDER` | 用哪家模型：`anthropic` / `openai` / `ollama`（本地跑） |
| `LLM_FAST_MODEL` / `LLM_SMART_MODEL` | 可选的轻任务/高智能任务模型覆盖；OpenAI 默认使用 Luna/Terra |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | 对应的密钥 |
| `WEB_SEARCH_ENABLED` | 是否允许联网补充资料，默认关闭 |
| `OBSERVABILITY_ENABLED` / `OTEL_TRACES_EXPORTER` | Agent trace 持久化开关与外部 `none|otlp|console` 导出 |
| `OTEL_CAPTURE_CONTENT` | 是否为 Replay 在 PostgreSQL 保留输入内容；关闭后仍记录指标 |
| `TASK_DAG_MAX_NODES` / `TASK_DAG_NODE_TIMEOUT_SECONDS` | DAG 节点数量上限与单次执行超时 |
| `TASK_DAG_MAX_ATTEMPTS` / `TASK_DAG_LEASE_SECONDS` | DAG 节点默认最大尝试次数与 durable worker lease |
| `PROMPT_CACHE_TTL_SECONDS` | workspace active prompt 的进程内缓存 TTL；激活/回滚会主动清除当前进程 |
| `TURN_BUDGET_MAX_MODEL_CALLS` / `TURN_BUDGET_MAX_TOKENS` / `TURN_BUDGET_MAX_COST_USD` | 单次 Chat turn 的模型调用、token 与美元硬预算 |
| `TURN_BUDGET_SOFT_RATIO` | 达到该预算比例后把新的 Smart 调用降级为 Fast |
| `ROUTER_CACHE_TTL_SECONDS` / `WEB_SEARCH_CACHE_TTL_SECONDS` | workspace 隔离 Redis cache TTL；设为 0 可按能力关闭 |
| `CIRCUIT_BREAKER_FAILURE_THRESHOLD` / `CIRCUIT_BREAKER_RECOVERY_SECONDS` | LLM、Web、reranker 分布式熔断阈值与恢复窗口 |

不填 key 也能启动，只是用不了需要模型的功能。

## 技术栈

TypeScript + React 前端，Python + FastAPI 后端，LangGraph 做 Agent 编排，PostgreSQL + pgvector 存向量，Redis 做队列。

## License

[MIT](LICENSE)
