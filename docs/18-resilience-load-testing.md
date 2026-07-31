# 18. Agent Resilience 与 Load Testing

## 两层测试

### Orchestration / governance profile

`make resilience-report` 不调用模型，直接压真实 `TaskDAGExecutor`、
`ResourceLedger`、`ResourceCache` 和 `CircuitBreaker`：

- 200 个 healthy DAG turns，50 并发；
- 200 个首次 provider timeout、第二次恢复的 retry turns；
- 200 个永久 timeout，验证 downstream synthesis 全部 blocked；
- 50 个预算竞争调用，验证 admission reservation 不 oversubscribe；
- 50 个相同 cache 请求，验证 single-flight 只计算一次；
- open → half-open → closed，验证只放行一个恢复 probe。

版本化结果见 [agent-resilience.md](reports/agent-resilience.md)，case-level JSON 见
`reports/agent-resilience.json`。CI 使用 40 turns / 20 concurrency 的 smoke profile。

### HTTP readiness SLO

`make http-load` 对正在运行的 `/api/v1/health/ready` 发出 500 个请求、50 并发，
检查 availability ≥ 99% 且 P95 ≤ 500ms。最近一次本地结果见
`reports/http-readiness.json`：500/500 成功，SLO PASS。

如需测试部署环境：

```bash
cd backend
uv run python ../scripts/http_load.py \
  --url https://your-deployment/api/v1/health/ready \
  --requests 1000 --concurrency 50 \
  --output ../docs/reports/http-production.json
```

## 结论边界

readiness profile 覆盖网络、FastAPI、PostgreSQL 和 Redis 可用性，但不调用付费模型，
不能代表 Chat 端到端延迟。Live coordination report 提供模型阶段延迟；正式部署还应
增加带固定预算的少量 authenticated Chat canary、长时间 soak test 和外部区域压测。
