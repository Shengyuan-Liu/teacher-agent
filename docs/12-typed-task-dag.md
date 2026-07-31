# 12. Typed Task DAG

## 目标

Router 不再只返回“按数组顺序执行”的任务列表。复合请求会被规范化为有稳定
ID、类型和显式依赖的有向无环图（DAG），执行器根据依赖关系决定并发和阻塞：

```text
web_1 ─┐
       ├─> answer_1
qa_1  ─┘
```

单 Agent 请求仍然是单节点 DAG，因此原有 Quiz、Plan、Lecture 等业务流程和
`AgentTask("qa", query)` 调用方式保持兼容。

## 类型与约束

每个 `AgentTask` 包含：

| 字段 | 含义 |
|---|---|
| `id` | 稳定的 snake_case 节点 ID；Router 未提供时由后端确定性生成 |
| `agent` | 执行节点的 Agent；复合知识任务支持 `qa`、`web`、`answer` |
| `kind` | `knowledge`、`action` 或 `synthesis` |
| `query` | 已消解代词、可以独立执行的子任务 |
| `depends_on` | 必须完成的上游节点 ID |
| `timeout_seconds` | 单次尝试的硬超时 |
| `max_attempts` | 包含首次执行在内的最大尝试次数 |

后端不信任模型生成的图。执行前会检查：

- 节点 ID 非空且唯一；
- 依赖必须存在，禁止自依赖；
- 图必须无环；
- synthesis 必须依赖 knowledge 节点；
- action Agent 只能构成单节点 DAG；
- 节点总数不超过 `TASK_DAG_MAX_NODES`。

旧 Router 若只返回 `web + qa`，规范化阶段会自动添加依赖二者的 `answer`
节点。若联网未获用户明确授权，代码级 consent gate 会移除 Web 节点以及依赖
它的 synthesis，再重新构建安全的单节点 DAG。

## 执行语义

`TaskDAGExecutor` 按拓扑层执行。同一层的节点通过 asyncio 并发运行，不同层
严格等待依赖完成。所有 handler 共享 `TaskBlackboard`：

```text
pending -> running -> completed
                   └> failed -> downstream blocked
```

blackboard 保存每个节点的 `result`、`status`、`attempts` 和 `error`。handler
只能从已完成的依赖读取结果。当前策略是 required dependency 的严格失败传播：
任一知识节点在重试耗尽后失败，Answer 节点不会用不完整上下文偷偷作答，而是
标记为 blocked，并由 SSE 返回明确错误。

QA 与 Web 节点只收集原始证据。Answer 节点从 blackboard 读取全部依赖结果，
按 DAG 声明顺序统一编号来源，再调用一次 Smart 档模型，避免多个 Agent 各自
生成相互冲突的答案。

## 调用链、持久化与 Replay

SSE 调用链现在包含：

1. Router 返回完整 DAG 快照；
2. `task_dag` 显示拓扑层；
3. 每个节点显示 ID、依赖、状态、尝试次数、模型和原始返回；
4. Answer 返回最终状态的 DAG。

前端把并行层和依赖层画成紧凑的 DAG，并保留 Raw DAG JSON。最终 DAG 也写入
`AgentRun.output_json.task_dag`，与各 AgentSpan 共用 trace ID。因此 Replay
可以同时比较输出、节点状态、模型、token、成本和调用耗时。

## 配置

| 环境变量 | 默认值 | 说明 |
|---|---:|---|
| `TASK_DAG_MAX_NODES` | `8` | 单次 Router 计划允许的最大节点数 |
| `TASK_DAG_NODE_TIMEOUT_SECONDS` | `90` | 每个节点每次尝试的超时 |
| `TASK_DAG_MAX_ATTEMPTS` | `2` | 未单独指定时的最大尝试次数 |

## 当前边界与下一步

当前 Typed DAG 已覆盖 Web + RAG + Answer 的主要复合知识链路，并为更多
handler 留出了统一接口。下一阶段的 durable execution 会增加节点级幂等键、
独立 checkpoint、进程重启恢复、可选依赖和降级策略；这些语义不会再由业务
代码里的 `gather` 或数组下标隐式决定。
