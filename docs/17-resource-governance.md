# 成本预算、缓存与熔断

## 目标

Agent 一次 query 可能触发 Router、并发 Web/RAG worker、reranker、Answer synthesis
和结构化修复。只在末尾统计 token，无法阻止并发超支；无统一缓存会重复调用外部服务；
无熔断时 provider 故障会让每个请求都等待同一超时。

本模块把三者收敛为一套 request-scoped Resource Governance：

```text
Chat turn
  └─ ResourceLedger (ContextVar)
       ├─ Budget: reserve → invoke → reconcile
       ├─ Cache: workspace hash → Redis → single-flight
       └─ Circuit: closed → open → half-open → closed
```

结果进入 Chat 的 `resource_policy/resource_summary` 调用链、Message usage、
AgentRun/AgentSpan、OpenTelemetry root attributes 和 Evaluation Result。

## 预算

模型 I/O 前先按配置的 input/output token 估计做 reservation。DAG 同层协程共享同一个
可变 ledger，因此第二个并发节点能看到第一个尚未返回的预留量。

- 预计达到 soft ratio 且请求 Smart 时，选择 Fast 并记录原 tier、实际 tier 和模型；
- 预计超过 model-call、token 或已知美元成本任一 hard limit 时，不再发起 provider I/O；
- provider 返回后用实际 usage reconciliation，并释放对应 reservation；
- 单次响应大于预估时无法撤回已经发生的花费，但会标记 hard stop，阻止后续调用；
- 未配置价格的模型仍受调用数和 token 限制，美元预算明确标记为“不完整执行”，不会把
  unknown 当作零成本。

默认值：

| 配置 | 默认 |
|---|---:|
| `TURN_BUDGET_MAX_MODEL_CALLS` | 12 |
| `TURN_BUDGET_MAX_TOKENS` | 50,000 |
| `TURN_BUDGET_MAX_COST_USD` | 1.00 |
| `TURN_BUDGET_SOFT_RATIO` | 0.80 |
| `TURN_BUDGET_ESTIMATED_INPUT_TOKENS` | 2,000 |
| `TURN_BUDGET_ESTIMATED_OUTPUT_TOKENS` | 1,000 |

## 缓存

首批只缓存满足明确失效边界的结果：

- Router：key 包含完整 history/question 的哈希、实际 Fast model trace、prompt
  version/hash；prompt 激活或模型切换会自然 miss；
- Web Search：key 包含 provider、query、top-k、site filter，使用短 TTL 保持时效性；
- Answer、Lecture、Quiz 不缓存，避免复用带用户进度或生成随机性的输出。

Redis key 形如：

```text
governance:cache:v1:{namespace}:{workspace_sha256_prefix}:{payload_sha256}
```

key 不包含 workspace UUID、query 或历史原文。每个进程再用 keyed `asyncio.Lock`
做 single-flight，首个 miss 计算时，同进程相同请求等待并复用结果。Redis 故障时缓存
fail-open：直接计算，事件标记 `bypass/error`，不阻断学习请求。

## 熔断

LLM、Tavily 和外部 reranker 共用 Redis-backed breaker。依赖名按 provider/model
分组，Redis key 使用依赖名哈希。

```text
closed --连续失败达到阈值--> open
open --recovery 到期--> half-open（跨 worker 只允许一个 probe）
half-open --成功--> closed
half-open --失败--> open
```

默认 60 秒 failure window 内 3 次失败开路，30 秒后允许一个 probe，probe 最长占用
15 秒。状态转移由 Redis Lua 原子执行，避免多 worker 同时穿透 half-open。Redis
不可用时进入 30 秒 cooldown，并使用进程内同语义状态机；这保留保护能力，但 outage
期间不同 worker 的状态可能暂时分叉。

## 可观测与评测

治理 payload 只记录预算数值、cache namespace/key hash、dependency、状态和异常类型，
不记录用户原文或凭据。OpenTelemetry root span 增加 downgrade、hard stop、cache hit
和 circuit event 指标。

`resource_governance` model-free suite 覆盖：

1. soft budget Smart → Fast；
2. 并发 reservation 阻止超支；
3. token hard limit fail-closed；
4. cache key 稳定、tenant 隔离且不泄露内容；
5. closed/open/half-open/closed 故障恢复。

该 suite 进入 `make eval-fast`。单元测试另覆盖 Redis cache single-flight 和未知价格语义。

## 当前边界

- 当前 hard budget 是单 turn 的部署配置，不是按订阅套餐持久化的日/月配额；
- streaming 单次调用只能在调用前预留、结束后核算，尚未按输出 token 中途取消；
- Redis outage 时缓存绕过、breaker 本地降级，不保证跨 worker 强一致；
- Web 页面正文没有缓存，只缓存搜索候选；页面抓取仍服从 SSRF 和抓取上限；
- Embedding 计入 token/cost，但当前没有独立 provider circuit proxy。
