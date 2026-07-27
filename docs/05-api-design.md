# 05. API 设计总览

本文档给出 API 的资源划分与关键端点草案（非最终实现，字段与路径可在开发中细化）。统一前缀 `/api/v1`。

## 1. 设计原则

- CRUD 类操作走标准 REST；生成式/对话式操作走流式接口（SSE）；耗时任务（摄取）走"提交任务 + 轮询/订阅状态"模式。
- 所有资源路径隐含按 `user_id` 鉴权过滤（通过 JWT 中间件注入当前用户，仓库层强制加 `owner_id` 过滤，防止越权访问他人数据）。
- 统一响应包裹：`{ "data": ..., "error": null }` / `{ "data": null, "error": { "code", "message" } }`。
- 分页统一用 `limit` + `cursor`（游标分页，避免大表 offset 分页性能问题）。

## 2. 鉴权

```
POST   /auth/register
POST   /auth/login              -> { access_token, refresh_token }
POST   /auth/refresh
POST   /auth/logout
GET    /auth/me
```

## 3. 学习空间（Workspace）

```
GET    /workspaces                        列表
POST   /workspaces                        创建 { name, description, language }
GET    /workspaces/{id}                   详情（含聚合状态、outline 摘要）
PATCH  /workspaces/{id}                   更新
DELETE /workspaces/{id}                   删除（级联删除 sources/plans/questions...）
GET    /workspaces/{id}/outline           获取知识大纲/知识图谱
POST   /workspaces/{id}/outline/regenerate  重新生成大纲（异步任务）
```

## 4. 资料源（Source）与摄取

```
POST   /workspaces/{id}/sources/upload        multipart 文件上传（pdf/docx/pptx/xlsx/md）
POST   /workspaces/{id}/sources/url           { url, crawl_depth?, scope? }
POST   /workspaces/{id}/sources/github        { repo_url, ref?, include_paths?, exclude_paths? }
GET    /workspaces/{id}/sources               列表（含各自 status）
GET    /workspaces/{id}/sources/{source_id}   详情
POST   /workspaces/{id}/sources/{source_id}/resync   重新摄取（增量）
POST   /workspaces/{id}/sources/{source_id}/retry    失败后重试
DELETE /workspaces/{id}/sources/{source_id}          删除（级联清理 chunk/向量）

GET    /workspaces/{id}/ingestion-jobs/{job_id}      查询摄取任务状态
GET    /workspaces/{id}/ingestion-jobs/{job_id}/stream  SSE 推送摄取进度（可选，替代轮询）
```

摄取任务状态返回示例：

```json
{
  "job_id": "job_123",
  "source_id": "src_456",
  "status": "chunking",
  "progress": 0.62,
  "detail": "42/68 files processed",
  "error": null
}
```

## 5. 问答（Chat）

```
POST   /workspaces/{id}/chat/sessions          创建会话 { mode: "strict"|"augmented" }
GET    /workspaces/{id}/chat/sessions          会话列表
GET    /chat/sessions/{session_id}/messages    历史消息（含 citations）
POST   /chat/sessions/{session_id}/messages    发送消息（非流式，测试/移动端可用）
GET    /chat/sessions/{session_id}/stream?message=...   SSE 流式问答（前端主用）
DELETE /chat/sessions/{session_id}             删除会话
```

SSE 事件流示例（`text/event-stream`）：

```
event: token
data: {"delta": "操作系统的"}

event: token
data: {"delta": "虚拟内存机制..."}

event: citation
data: {"chunk_id": "chk_789", "source_id": "src_456", "locator": "p.12"}

event: done
data: {"message_id": "msg_001"}
```

## 5.1 联网检索（Web Search，用户主动触发）

```
GET    /capabilities                            返回当前部署启用了哪些能力（含 web_search 开关），前端据此显示/隐藏入口

POST   /workspaces/{id}/web-search              执行搜索，返回候选列表（不入库）
       { query?, from_question?, top_k?, site_filter?[] }
POST   /workspaces/{id}/web-search/ingest       将用户勾选的候选纳入学习空间（异步摄取）
       { results: [{ url, title }], }           -> { job_ids: [...] }

GET    /chat/sessions/{session_id}/stream?message=...&web_search=true
                                                本轮问答允许联网（形态 A，一次性，不入库）
```

关键约定：

- `web_search=true` 必须由前端显式传入，后端**不会**因为检索不到本地资料就自动开启联网；QA Agent 在资料不足时返回的是一个"建议联网"的提示事件，由用户决定是否再发一次带 `web_search=true` 的请求。
- 若部署未启用该能力，以上端点返回 `403 WEB_SEARCH_DISABLED`。
- `/web-search` 与 `/web-search/ingest` 均受用户级限流保护。

搜索候选返回示例：

```json
{
  "data": {
    "queries_used": ["rust async executor 实现原理"],
    "results": [
      {
        "url": "https://example.com/async-book/executor",
        "title": "Building an Executor",
        "snippet": "...",
        "domain": "example.com",
        "recommended": true,
        "reason": "官方文档，直接覆盖你提问的 executor 实现细节"
      }
    ]
  },
  "error": null
}
```

SSE 中与联网相关的事件：

```
event: web_search_suggested
data: {"reason": "当前资料未覆盖该问题", "suggested_query": "rust async executor 实现原理"}

event: web_citation
data: {"url": "https://example.com/...", "title": "Building an Executor", "domain": "example.com", "fetched_at": "2026-07-26T10:00:00Z"}
```

## 6. 学习计划（Study Plan）

```
POST   /workspaces/{id}/study-plans                生成计划 { goal, deadline?, daily_minutes, level? }
GET    /workspaces/{id}/study-plans                历史计划列表
GET    /study-plans/{plan_id}                       详情（含 stages）
POST   /study-plans/{plan_id}/regenerate            调整/重新生成（可指定 from_stage）
PATCH  /study-plans/{plan_id}/stages/{stage_id}     更新阶段状态（如 mark done）
```

## 7. 题库与测验（Quiz）

```
POST   /workspaces/{id}/questions/generate     生成题目 { scope, topic_ids?, types[], difficulty, count }
GET    /workspaces/{id}/questions              题库列表（筛选 topic/type/difficulty）
GET    /questions/{question_id}                题目详情（含出处、解析）
DELETE /questions/{question_id}

POST   /workspaces/{id}/quiz-attempts          开始一次测验/练习 { question_ids[] or scope, mode }
POST   /quiz-attempts/{attempt_id}/answers     提交单题作答
POST   /quiz-attempts/{attempt_id}/submit      交卷，触发批改（客观题即时，主观题异步/同步 LLM 批改）
GET    /quiz-attempts/{attempt_id}             结果详情（得分、逐题反馈）

GET    /workspaces/{id}/review-queue           今日待复习错题（间隔重复）
```

## 8. 系统讲解 / Lecture

```
POST   /workspaces/{id}/explanations           生成系统讲解 { topic_id or scope }
GET    /explanations/{explanation_id}          获取讲义内容（结构化 Markdown + 大纲）

POST   /workspaces/{id}/lectures               开始 Lecture 会话 { study_plan_stage_id? or topic_ids? }
GET    /lectures/{lecture_id}/stream           SSE 流式讲课内容 + 互动问题事件
POST   /lectures/{lecture_id}/respond          用户回答/打断提问 { message }
POST   /lectures/{lecture_id}/control          控制指令 { action: "next"|"repeat"|"pause"|"resume" }
```

## 9. 进度与掌握度

```
GET    /workspaces/{id}/mastery                知识点掌握度列表（雷达图/列表数据源）
GET    /users/me/dashboard                     跨 workspace 的整体学习仪表盘数据
```

## 10. 错误码约定（节选）

| code | 说明 |
|---|---|
| `SOURCE_TOO_LARGE` | 上传文件/仓库超出大小限制 |
| `INGESTION_FAILED` | 摄取失败，`detail` 中带具体原因 |
| `INSUFFICIENT_CONTEXT` | 问答/出题时检索不到足够相关内容 |
| `RATE_LIMITED` | 触发用户级限流（如短时间内多次生成大批量题目） |
| `UNAUTHORIZED_RESOURCE` | 访问了不属于当前用户的资源 |
| `WEB_SEARCH_DISABLED` | 当前部署未启用联网检索能力 |
| `WEB_SEARCH_FAILED` | 搜索服务不可用或超时（不应影响本地资料问答的正常使用） |

## 11. 与 Agent 层的对接方式

API 层不直接拼 prompt，而是调用 `agents/` 模块暴露的服务函数（如 `qa_agent.astream(workspace_id, session_id, user_message)`），返回值为异步生成器，FastAPI 路由将其转为 SSE。这样 Agent 编排逻辑（LangGraph graph 定义）与 HTTP 细节解耦，便于单独测试 agent 行为（见 `06-agent-design.md`）。
