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

## Durable execution

每个复合 Chat 请求使用客户端稳定的 `request_id` 作为 execution key。Router 首次
生成的完整 DAG 写入 `task_executions`，每个节点在 `task_node_checkpoints` 中独立保存：

- 节点进入 provider I/O 前先持久化 `running + attempts`；
- 成功结果 materialize 为 JSONB，重启后不会重复运行已完成节点；
- 进程中断时 execution 标记为 `interrupted`，用户用同一 `request_id` 重试即可恢复；
- 重试会读取原始 DAG，不重新调用 Router 生成可能不同的计划；
- run-level lease 防止两个 worker 同时接管同一 execution，lease 丢失时 fail closed；
- 已恢复节点在调用链显示 `checkpoint=true`、`execution_id` 和 `resumed=true`。

这提供的是具备幂等结果复用的 at-least-once worker 语义。外部模型调用无法提供事务性
exactly-once：如果进程恰好在 provider 已响应、checkpoint 尚未提交之间退出，该节点仍
可能重发；有副作用的未来工具必须另外接受 execution/node idempotency key。

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
| `TASK_DAG_LEASE_SECONDS` | `120` | worker execution lease；过期后允许其他进程接管 |

## 当前边界与下一步

当前 Typed DAG 已覆盖 Web + RAG + Answer 的节点级 checkpoint、租约和重启恢复。
依赖仍全部是 required：任一 worker 最终失败时 synthesis 严格 blocked。可选依赖、
部分结果降级和有副作用工具的 provider-side idempotency key 仍是后续工作。
