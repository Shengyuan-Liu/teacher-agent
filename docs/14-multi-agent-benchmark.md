# 14. Multi-Agent Benchmark 与消融实验

## 实验问题

这个 benchmark 不把“用了更多 Agent”默认当成改进，而是回答三个可证伪问题：

1. specialized Web/RAG workers 是否比一个 Agent 直接读取全部上下文覆盖更多 claims？
2. 并发 fan-out 是否缩短了关键路径？
3. 最终 synthesis 是否提高跨来源回答的顺序、引用和连贯性，代价是多少？

所有策略使用同一个 Dataset、question、Web evidence、local evidence、expected claims
和评分器。只有协调策略变化，避免把数据变化误当架构收益。

## 策略矩阵

| Variant | Specialized workers | 并发 | Smart synthesis | 主要消融 |
|---|---:|---:|---:|---|
| `single_agent` | 否 | 否 | 是 | 多 Agent decomposition |
| `typed_dag` | 是 | 是 | 是 | 完整方案 |
| `sequential_dag` | 是 | 否 | 是 | parallel fan-out |
| `no_synthesis` | 是 | 是 | 否 | fan-in synthesis |

`typed_dag` 的执行图为：

```text
web_1 ─┐
       ├─> answer_1
qa_1  ─┘
```

## Case contract

```json
{
  "input": {
    "question": "Who was Poisson and what theorem is in the material?",
    "web_sources": [
      {"citation": "W1", "text": "Web evidence"}
    ],
    "local_sources": [
      {"citation": "L1", "text": "Local evidence"}
    ],
    "simulation": {
      "web_latency_ms": 80,
      "local_latency_ms": 120,
      "synthesis_latency_ms": 140,
      "single_latency_ms": 290,
      "web_tokens": 180,
      "local_tokens": 220,
      "synthesis_tokens": 420,
      "single_tokens": 700,
      "variant_omissions": {
        "single_agent": ["A known missed claim"]
      }
    }
  },
  "expected": {
    "claims": ["Expected claim one.", "Expected claim two."],
    "ordered_claims": ["Expected claim one.", "Expected claim two."],
    "citations": ["W1", "L1"],
    "min_quality": 0.9
  }
}
```

`variant_omissions` 只用于确定性实验表达已观察到的 strategy failure；真实 live
结果不会应用这个字段。

## 指标

| 指标 | 方向 | 计算 |
|---|---|---|
| `claim_recall` | 越高越好 | expected claims 的覆盖率 |
| `citation_coverage` | 越高越好 | required citation markers 的覆盖率 |
| `order_accuracy` | 越高越好 | claims 是否按用户要求出现 |
| `coherence` | 越高越好 | 单一完整答案优于未汇聚 worker 输出 |
| `answer_quality` | 越高越好 | 0.50 claim + 0.20 citation + 0.15 order + 0.15 coherence |
| `latency_efficiency` | 越高越好 | `1000 / critical_path_ms` |
| `parallelism_efficiency` | 越高越好 | `sum(stage_ms) / critical_path_ms` |
| `cost_efficiency` | 越高越好 | `1 / effective_cost` |

原始 `critical_path_ms`、stage latency、token、cost、agent calls 和 DAG 同时保存在
case output。Efficiency 指标是为了兼容“score 越高越好”的 baseline gate；分析报告
仍应展示原始单位。

## 两种执行模式

### Deterministic

- 不调用模型或网络；
- 使用 case 中固定的 latency/token 参数；
- 输出可复现，适合 CI 验证实验管线、指标和策略差异；
- 4 个 starter cases 已加入 `make eval-fast`。

### Live

- `single_agent`：一次 Smart 调用读取全部证据；
- `typed_dag`：两个 Fast worker 通过真实 `TaskDAGExecutor` 并发，Smart synthesis
  从 blackboard 读取依赖结果；
- `sequential_dag`：相同 worker/synthesis prompt，但顺序执行；
- `no_synthesis`：Fast workers 并发，各自回答后直接拼接；
- Runner 记录实际模型、token、USD cost 和 wall-clock。
- 策略生成完成后，由版本化 `benchmark.judge` Smart 模型按语义 entailment 评分；
  Judge 模型、Prompt hash、claim-level 分数和 JSON recovery 单独留痕，不计入策略成本。

首轮 live pilot 暴露了一个评测缺陷：完整句子 substring matcher 会把正确的同义改写
误判为 claim 丢失。确定性 contract 继续使用严格匹配，live 模式改用上述语义 Judge，
并由 schema 校验和 bounded repair 保证结构化返回。

Dashboard 的 “Run live matrix · uses models” 是显式付费动作。建议固定模型版本、reasoning
effort、Dataset version 和 Git SHA，每个策略至少重复 30 次，并报告均值、P50/P95、
标准差或 bootstrap confidence interval。

## 已发布报告

- [30-repeat deterministic report](reports/multi-agent-deterministic.md)：4 cases ×
  4 variants × 30 repeats，共 480 个机制验证样本；
- [3-repeat live pilot](reports/multi-agent-live.md)：Luna workers、Terra synthesis/Judge，
  共 48 个真实策略样本；
- JSON 产物保留全部 case-level answer、Judge evidence、模型选择、token、成本、
  Git SHA、固定 bootstrap seed 和 95% CI。

本次 live pilot 没有支持“多 Agent 全面优于单 Agent”：`single_agent` 平均质量最高；
`typed_dag` 相比 `sequential_dag` 平均质量仅高 0.017、平均关键路径短约 0.55 秒；
与 `no_synthesis` 的平均质量基本持平（低 0.002），但延迟和成本明显更高。当前证据
不支持把完整 DAG 作为所有 query 的默认策略，decomposition/synthesis 应由任务复杂度、
跨来源整合需求和置信度触发。3-repeat 仍是 pilot，不用于宣称稳定百分比收益；正式
结论仍需要 30+ repeats。

## 使用方式

1. 在 Workspace → Evaluations 创建 `multi_agent_coordination` starter；
2. “Run full DAG” 只跑 `typed_dag`；
3. “Run ablation matrix” 创建四个 deterministic Runs；
4. “Run live matrix · uses models” 创建四个 live Runs；
5. 在 Multi-agent ablation 表横向比较，点击 variant 下钻逐 case evidence。

自定义 Dataset 可通过 JSON import 创建，suite 选择
`multi_agent_coordination`。Run 的 baseline 按 `dataset + variant + execution_mode`
匹配，避免拿 deterministic single-agent 和 live typed-DAG 做错误回归比较。

## 结论边界

确定性实验说明的是协调机制和评测管线是否按设计工作，不是模型质量结论。Live 实验
仍会受供应商负载、模型漂移和 sampling 影响。只有固定实验条件、足够重复次数并保存
置信区间后，才能在简历或技术报告中声称某个策略带来具体百分比收益。
