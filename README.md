# TeacherAgent

> 上传你的资料，我把它变成一门可以问、可以练、可以听讲的课。

想学一样新东西，手头往往有一堆散资料：教材 PDF、课件 PPT、官方文档站、一个开源仓库。TeacherAgent 把它们变成一门课——**所有回答都基于你给的资料，并标明出处**。

## 能做什么

- 📁 **喂什么都行** — PDF / Word / PPT / Excel / Markdown 文件，任意网址或文档站，甚至一个 GitHub 仓库链接
- 💬 **问答有据可查** — 每个回答都标出处，点击跳回原文；资料里没有的会直说，不编
- 🗺️ **生成学习计划** — 告诉它你的目标和每天能花多久，它排出分阶段的学习路径
- 📝 **自动出题** — 选择题、填空、简答；生成后再次检查答案是否能由资料支持
- 🧭 **过程透明** — Router、检索、规划和出题的每一步结果、模型与用量都显示在调用链中
- 🌐 **显式联网** — 只有用户主动要求或点击时才搜索，可先查看候选资料再决定是否入库

> **当前进度**：Phase 1 已完成代码级收口；Phase 2 的完整摄取、学习大纲、计划、基础题库和显式联网主链路已经实现，正在进行真实大仓库/真实文档的发布验收。正式测验、掌握度和 Lecture 属于后续阶段。完整设计见 [docs/](docs/README.md)，开发路线见 [路线图](docs/08-roadmap.md)。

## 快速开始

需要先装好 Docker、[uv](https://docs.astral.sh/uv/)、Node 20+ 和 pnpm。

```bash
make setup   # 起数据库 + 装依赖 + 建表（只需跑一次）
make dev     # 启动
```

然后打开 http://localhost:5300 —— 页面上三项都是绿点就说明环境正常。

后端接口文档在 http://localhost:8000/docs。

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

不填 key 也能启动，只是用不了需要模型的功能。

## 技术栈

TypeScript + React 前端，Python + FastAPI 后端，LangGraph 做 Agent 编排，PostgreSQL + pgvector 存向量，Redis 做队列。
