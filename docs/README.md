# 项目文档索引

本目录是「万物可学」学习助手 Agent 项目的需求与设计文档集合。Phase 1–4 主链路已实现，当前正在建设面向生产与 AI Engineer 工程能力展示的 Phase 5。

工作代号：**TeacherAgent**（可后续改名）

## 文档列表

| 文档 | 内容 |
|---|---|
| [00-overview.md](00-overview.md) | 项目背景、目标、用户画像、核心价值主张、非目标 |
| [01-requirements.md](01-requirements.md) | 功能性需求（FR）与非功能性需求（NFR）、用户故事 |
| [02-features.md](02-features.md) | 各功能模块详细设计：资料摄取、问答、学习计划、题库、系统讲解、Lecture 模式、联网检索 |
| [03-architecture.md](03-architecture.md) | 系统架构、组件划分、数据流、部署形态 |
| [04-data-model.md](04-data-model.md) | 核心数据模型 / ER 设计 |
| [05-api-design.md](05-api-design.md) | API 设计总览（REST + 流式接口） |
| [06-agent-design.md](06-agent-design.md) | Agent / LangGraph 编排设计、工具（tools）与状态设计 |
| [07-references.md](07-references.md) | 参考的开源项目调研与借鉴点 |
| [08-roadmap.md](08-roadmap.md) | 分阶段路线图（MVP → V2 → V3） |
| [09-rag.md](09-rag.md) | RAG 管线：混合检索、RRF、重排、评估方法与指标 |
| [10-evaluation-platform.md](10-evaluation-platform.md) | 统一 AI Evaluation Platform：数据集、suite、运行、baseline、回归 gate 与 Dashboard |
| [11-observability-replay.md](11-observability-replay.md) | OpenTelemetry Agent traces、模型/成本聚合、waterfall、隐私策略与隔离 Replay |
| [12-typed-task-dag.md](12-typed-task-dag.md) | Typed Task DAG：显式依赖、拓扑并发、blackboard、超时/重试、失败传播与调用链 |
| [13-agent-engineering-log.md](13-agent-engineering-log.md) | Agent 工程日志：历次优化、故障根因、解决方案、验证证据、取舍与遗留问题 |
| [14-multi-agent-benchmark.md](14-multi-agent-benchmark.md) | Multi-Agent 策略矩阵、消融设计、指标、deterministic/live 运行与结论边界 |

## 阅读顺序建议

1. 先看 `00-overview.md` 了解项目定位
2. 再看 `01-requirements.md` 和 `02-features.md` 明确"做什么"
3. 然后看 `03-architecture.md`、`04-data-model.md`、`05-api-design.md`、`06-agent-design.md` 明确"怎么做"
4. 最后看 `07-references.md` 和 `08-roadmap.md` 了解参考对象和落地节奏

## 技术栈速览（详见 03、06 文档）

- **前端**：TypeScript + React（建议 Vite），Chat UI 可参考/复用 `assistant-ui`
- **后端**：Python + FastAPI，异步任务队列（Celery/RQ 或 Arq）
- **Agent 框架**：LangGraph（多 agent 编排）+ LangChain（模型/工具/检索器封装）
- **向量库**：Qdrant 或 pgvector（二选一，详见 03 文档权衡）
- **关系数据库**：PostgreSQL
- **文档解析**：MarkItDown / Unstructured 处理 PDF、Word、PPT、Excel
- **代码仓库摄取**：参考 gitingest / repomix 思路自研摄取管线
- **联网检索**：`SearchProvider` 抽象（Tavily / Brave / SearXNG 可选），**仅用户主动触发**，可全局关闭
