# 13. Agent Engineering Log

## 目的与维护规则

这是 Agent 系统的工程决策与故障解决日志，不是产品发布文案。它记录：

- 当时观察到了什么失败；
- 为什么单靠 prompt 或 happy-path 测试不够；
- 最终把什么约束放进了代码、状态机、数据模型或评测；
- 如何验证，以及仍有哪些边界。

以后任何 Router、Agent、模型分层、编排、RAG、结构化输出、Evaluation、
Observability 或 Replay 的实质改动，都必须在同一个 change 中追加条目。仓库根目录
`AGENTS.md` 已把这一要求设为 coding agent 的项目规则。日志只保存脱敏后的问题，
不记录 API key、完整私有对话或受保护资料正文。

## 索引

| ID | 优化 | 核心工程措施 |
|---|---|---|
| AE-001 | 严格资料问答 | relevance gate、拒答、统一引用 |
| AE-002 | Chat-first Router | 单入口意图识别与对话内 artifact |
| AE-003 | 调用链透明化 | 每个 Agent 的 stage/result/model/usage |
| AE-004 | 模型智能分层 | Fast Luna / Smart Terra 接口 |
| AE-005 | 模型成本配置 | 可合并价格表与未知价格语义 |
| AE-006 | Plan 顺序修复 | source order + deterministic invariant |
| AE-007 | Router 不确定性 | confidence gate + 用户选项 |
| AE-008 | Web 安全授权 | 配置与用户授权双门控 |
| AE-009 | 多 Agent 复合查询 | Web/RAG fan-out + 单次 Answer synthesis |
| AE-010 | 结构化输出韧性 | 本地修复、schema 校验、一次模型修复 |
| AE-011 | Lecture 一等模块 | durable checkpoint、插问与恢复 |
| AE-012 | 测验与掌握度闭环 | 程序判分、Smart 评分、原子回流 |
| AE-013 | 统一 Evaluation Platform | dataset/suite/run/result/baseline gate |
| AE-014 | OpenTelemetry 与 Replay | run/span、waterfall、隔离重跑 |
| AE-015 | Typed Task DAG | 显式依赖、blackboard、重试与失败传播 |
| AE-016 | Multi-Agent Benchmark | 策略矩阵与消融实验 |

---

## AE-001 — 严格资料问答与可追溯引用

**问题**：普通生成模型容易在资料未覆盖时用参数知识补全，回答看似合理但无法证明
来自用户材料；PDF 引用若没有原始页码也难以复核。

**根因**：grounding 只是 prompt 要求，没有执行层 gate；引用和生成不是同一个
编号上下文；早期文本解析会丢失 PDF 位置。

**方案**：

- QA Graph 使用 `retrieve → grade_relevance → generate|decline`，未通过 relevance
  gate 时明确拒答；
- 生成只接收已编号的检索上下文，回答后的引用再次按实际使用编号过滤；
- 摄取和 chunk metadata 保留 source/page/heading，前端引用可返回原文件页；
- Web 与本地资料使用不同 citation 类型，避免来源混淆。

**验证**：Phase 1 自动化覆盖资料内回答、资料外拒答、引用编号与 PDF 页码。

**剩余边界**：扫描 PDF、复杂公式和跨页表格仍需要真实样本回归；faithfulness
judge 尚未进入 release gate。

## AE-002 — 所有学习动作收敛到 Chat-first Router

**问题**：Explain、Quiz、Test、Plan 等独立页面让用户必须先理解产品模块，无法用
自然语言连续完成“问答 → 练习 → 调整计划”。

**根因**：功能按后端能力而非用户对话组织；各模块拥有不同入口和状态传递方式。

**方案**：

- 所有 Chat 请求先经过 Router，统一分发 `qa/web/quiz/test/review/progress/plan/
  explain/lecture`；
- 结构化交互以 Chat artifact 呈现，选择、测试、复习和计划修改继续留在当前对话；
- Lecture 因宣传和媒体演进需要保留与 Chat 平级入口，但复用同一 chat runtime。

**验证**：Router 选择、显式 intent 绕过 Router、Chat artifact 和各业务闭环均有
集成测试。

**取舍**：前端入口减少，但后端 Router 契约成为关键基础设施，必须单独评测。

## AE-003 — 完整 Agent 调用链与模型透明化

**问题**：只显示最终回答时，无法判断错误来自 Router、检索、生成还是下游状态机；
也无法确认某一步究竟使用了什么模型。

**根因**：流式协议原先只传 token/citation，内部中间结果没有统一事件模型。

**方案**：

- SSE 增加 `stage` / `stage_result`，每步记录 Agent、label、完整结构化结果；
- `model_trace` 在模型选择处生成 provider/model/tier/reasoning effort，避免前端维护
  第二份映射；
- Message 持久化 trace，前端调用链可折叠查看每个 Agent 返回。

**验证**：ActivityTrace 测试覆盖完整 JSON、Fast/Smart 模型和 reasoning effort；
Chat 集成测试检查 Router、Web、QA、Answer 均出现在同一条 trace。

**剩余边界**：极大检索正文会增加 trace 体积，后续需要内容摘要与 blob 分离策略。

## AE-004 — 按智能程度分层的 LLM 接口

**问题**：Router、分类器等轻任务与最终回答共用大模型，延迟和成本不必要地上升；
若各 Agent 自行选模型又难以统一治理。

**根因**：模型名直接散落在业务代码，没有“任务智能等级”抽象。

**方案**：

- 引入 `IntelligenceTier.FAST/SMART` 和统一 `chat_model(tier)`；
- OpenAI 默认 Fast → `gpt-5.6-luna`，Smart → `gpt-5.6-terra`；
- Router、搜索改写、轻分类使用 Fast；回答、讲解、主观评分和 synthesis 使用 Smart；
- reasoning effort 与实际解析后的模型一起进入调用链和 Eval run snapshot。

**验证**：provider/model trace 测试和多 Agent 集成测试确认 Answer 使用 Smart，Router
使用 Fast。

**取舍**：tier 是业务稳定接口，具体模型仍可通过环境变量覆盖，避免把产品逻辑绑定
到单个供应商型号。

## AE-005 — 模型价格与未知成本语义

**问题**：新增 Luna/Terra/Sol 后，调用链能看到 token，却可能因为价格表不全而显示
错误成本；环境变量覆盖一个模型时还可能意外丢掉默认表。

**根因**：价格配置曾采用整体替换语义，并把未知价格和零成本混为一谈。

**方案**：

- 内置按百万 token 的模型价格表，并让 `MODEL_PRICES` 与默认价格 merge；
- 支持 Luna、Terra、Sol、Claude、embedding 和 reranker 的不同计费单位；
- 未知模型保留 token，但 cost 明确为 `null/unknown`，不猜测为 0。

**验证**：usage ledger、Eval Result 和 Observability 聚合共同使用同一价格源。

**剩余边界**：价格会变化，生产发布前仍需人工核对供应商账单和缓存/批处理折扣。

## AE-006 — 学习计划顺序错误修复

**问题**：以教材 PDF 生成的计划出现阶段乱序，后置主题跑到前置主题之前；只加强
prompt 后仍可能复发。

**根因**：模型看到的 outline 缺少足够明确的 source order，而且输出顺序完全依赖
模型服从；`depends_on` 没有在落库前成为确定性约束。

**方案**：

- Outline 输入显式增加 `source_order`，prompt 声明教材顺序为 authoritative；
- `order_stages_by_outline` 在代码层按 stage 覆盖主题的最晚 source position 排序；
- 未知自定义阶段稳定放在材料阶段之后；日计划重排后重新编号 Day；
- `finalise_stages` 同时 canonicalize topic、预算和 deadline。

**验证**：Planner 单元测试覆盖 source order、依赖顺序、天数和预算；真实问题样本
来自 `backend/storage/.../db18d128-bd89-4c73-b0b0-ef36d9f8796e.pdf`，日志不复制
其私有正文。

**经验**：顺序、权限、预算等 invariant 必须进入代码，不能只写在 prompt。

## AE-007 — Router 低置信度时让用户决定

**问题**：“考考我并详细讲讲”等请求可能是复合动作，也可能是用户在二选一；强行
路由会在错误方向执行到一半。

**根因**：Router 只有单个 intent，没有 confidence、alternatives 和暂停语义。

**方案**：

- Router 返回 intent、confidence、alternatives、reason 和 tasks；
- confidence 低于阈值时不执行 Agent，在 Chat 里返回 2–3 个可点击方向；
- 用户选择通过显式 intent 回传，绕过第二次 Router 模型调用。

**验证**：测试覆盖低置信度暂停、选项顺序和选择后直接执行。

**取舍**：多一次用户交互换取不执行错误的高成本任务。

## AE-008 — Web 搜索代码级授权门

**问题**：Router 模型可能误判并生成 Web task；若把模型输出当授权，会在用户未要求
时发起网络请求。

**根因**：能力选择与用户同意混在同一层，prompt 约束无法作为安全边界。

**方案**：

- Web 必须同时满足部署开关与用户 query 的显式搜索表达，或明确 UI action；
- `filter_authorized_tasks` 在执行前移除未授权 Web 节点及依赖它的 synthesis；
- rate limit、SSRF、抓取上限继续位于工具层。

**验证**：自动化测试保证 Router 单方面返回 Web task 也不能触发网络。

**经验**：模型可以建议 capability，不能授予外部 I/O 权限。

## AE-009 — 一次 query 的 Web + RAG 多 Agent 协作

**问题**：“上网查 Poisson 是谁，再看他在教材里的定理”同时需要互联网和本地资料，
单 intent Router 只能选一个 Agent。

**根因**：Router contract 是单 intent，执行器也只接受一个下游 graph。

**方案**：

- Router 可以拆出多个独立 query；
- Web 与 RAG 并发收集原始证据，不分别生成最终答案；
- Orchestrator 按任务声明顺序合并上下文、统一 citation 编号；
- Smart Answer Agent 只调用一次，明确区分 Web 和 Local claims。

**验证**：集成测试检查两个 Agent 都执行、上下文都进入 Answer prompt、引用编号稳定，
且 Answer 只调用一次。

**遗留问题**：最初实现仍是扁平数组加 `asyncio.gather`，依赖和失败传播隐含在业务
代码中；AE-015 用 Typed DAG 解决。

## AE-010 — Invalid JSON 不再卡死 Agent 流程

**问题**：Lecture Agent 经常返回 fenced JSON、尾逗号、Python dict 或 schema
不匹配，前端显示 “returned invalid JSON” 后工作流停住，checkpoint 状态不清晰。

**根因**：各 Agent 用正则加 `json.loads` 独立解析；没有 bounded recovery、统一
schema validation 或可重试错误状态。

**方案**：

1. 提取第一个平衡 JSON object，正确处理字符串里的花括号；
2. 本地修复 fence/尾逗号/Python literal；
3. 使用领域 parser 做 schema validation；
4. 仍失败时只允许一次模型 repair，并再次验证；
5. Lecture 生成失败返回 retry artifact；评分失败保留 checkpoint 和 learner answer，
   提供 retry_grade，而不是推进 Mastery。

**验证**：Structured Output suite 覆盖 strict、fence、trailing comma、Python
literal、字符串花括号和不可恢复输入；Lecture 集成测试覆盖 retry/retry_grade。

**取舍**：恢复只修序列化，不猜业务字段；无法证明正确时宁可保持状态不变。

## AE-011 — Lecture durable state 与插问恢复

**问题**：长讲课跨多个 turn，用户可能刷新、暂停、跨天恢复，或在检验中插问；仅靠
对话 history 无法可靠区分“作答”和“打断问题”。

**根因**：Lecture 是状态机，不是一次生成；运行状态若只在内存中会丢失。

**方案**：

- `LectureSession` 持久化 outline、current section、pending check 和 section history；
- Fast 分类器区分 answer/question，Smart 模型用于讲解和评分；
- 插问委托 QA 后回到原 checkpoint；暂停、恢复、停止都有显式 transition；
- request_id 保障刷新/HMR 不重复消费初始 query。

**验证**：完整覆盖讲解、作答、暂停、恢复、插问、回到检验和完成。

**剩余边界**：音频/视频是后续独立媒体流水线，不与当前文本 checkpoint 混写。

## AE-012 — 测验、错题、掌握度与 Planner 的闭环

**问题**：Quiz 若只生成题而不把结果回流，Agent 无法依据真实学习证据调整后续行为；
LLM 评分又可能造成事务长时间占锁或重复提交。

**根因**：生成、作答、判分、复习和计划曾是相互独立的功能。

**方案**：

- 客观题程序化判分，简答题使用 Smart 模型；
- 测验提交幂等，慢模型评分不占读取事务，最终状态在行锁内原子提交；
- 错题进入有上限的 SM-2 队列；
- TopicMastery 回流 Quiz 选题和 Planner，薄弱点生成新计划版本而不覆盖旧版本。

**验证**：Phase 3 端到端测试覆盖 test → grade → review → mastery → plan revision。

## AE-013 — 统一 AI Evaluation Platform

**问题**：Router、RAG、JSON 和 Lecture 各自写测试，无法回答“哪个模型/代码版本在哪些
case 上回归、代价多少”。

**根因**：缺少统一 dataset、suite contract、run snapshot 和 baseline comparator。

**方案**：

- `EvalDataset → EvalCase → EvalRun → EvalResult` 版本化持久化；
- Suite registry 与统一 Runner 负责逐 case checkpoint、异常隔离、latency、token、
  cost 和 Git SHA；
- 支持绝对 min score 与相对 baseline max regression gate；
- 首批 Structured Output、Router Contract、RAG Retrieval suites；
- Dashboard、异步 ARQ worker 和 `make eval-fast` CI gate。

**验证**：API、恢复执行、baseline、ownership、12 个初始 contract cases 均有测试。

**剩余边界**：真实模型 judge 应进入 nightly/release，而非每次 PR。

## AE-014 — OpenTelemetry Agent 可观测性与隔离 Replay

**问题**：线上一次失败只能看到最终错误，无法定位具体 Agent、模型、token、耗时或
输入版本；直接在原 Chat 重跑又会污染用户历史。

**根因**：产品 trace 与基础设施 telemetry 没有统一 trace ID，缺少可查询 run/span。

**方案**：

- FastAPI、HTTPX、SQLAlchemy OpenTelemetry instrumentation，可选 OTLP/console；
- `AgentRun/AgentSpan` 持久化语义 stage、模型、token、cost、错误和 waterfall；
- 产品 trace 与 OTel 共用 trace ID；
- Replay 在隔离临时会话重跑原输入，比较 output/latency/token/cost，不写回原 Chat；
- `OTEL_CAPTURE_CONTENT=false` 时保留指标但禁用 Replay。

**验证**：API 隔离、summary、span waterfall、Replay 和真实 OTLP protobuf smoke test。

**取舍**：Replay 不是确定性复现；统计回归应把 failure 提升为 Eval Case。

## AE-015 — 从扁平任务列表升级为 Typed Task DAG

**问题**：AE-009 的 `tasks[] + gather + 手工拼接` 只表达“同时跑”，不能表达依赖、
拓扑、重试、超时或下游失败传播；task 顺序也被误当作执行语义。

**根因**：Router schema 和执行器都没有 task ID、kind、depends_on 或共享状态。

**方案**：

- `AgentTask` 增加稳定 ID、kind、depends_on、timeout 和 max attempts；
- `TaskDAG` 校验重复 ID、悬空依赖、自依赖、cycle、synthesis 类型和节点上限；
- legacy Web + QA 自动归一化为 `web_1/qa_1 → answer_1`；
- `TaskDAGExecutor` 按拓扑层并发，使用 blackboard 传结果，重试耗尽后严格 blocked；
- DAG 节点状态进入 SSE 调用链、前端图、AgentRun 和 Replay。

**验证**：测试覆盖拓扑层、并发 barrier、blackboard、retry、timeout、cycle、授权裁剪、
failure propagation，以及真实 Chat fan-out/fan-in。

**剩余边界**：当前 DAG 快照持久化，但进程重启后的节点级 checkpoint/resume 尚未完成。

## AE-016 — Multi-Agent Benchmark 与消融实验

**问题**：仅证明“多 Agent 能跑”无法说明它是否比 single-agent 更好，也无法区分提升
来自 specialized workers、并发还是最终 synthesis。

**根因**：Evaluation Platform 一次只看一个输出，缺少共享 case/证据/评分器下的协调
策略矩阵。

**方案**：

- 新增 `multi_agent_coordination` suite；
- 固定四个策略：
  - `single_agent`：一个 Smart Agent 读取全部证据；
  - `typed_dag`：Fast Web/QA 并发，Smart Answer fan-in；
  - `sequential_dag`：保留 specialization/synthesis，移除并发；
  - `no_synthesis`：保留并发 workers，移除最终 Answer；
- 同一 case 评估 claim recall、citation coverage、order、coherence、
  latency efficiency 和 cost efficiency；
- deterministic 模式使用固定 stage latency/token 模型，进入每次 CI；
- live 模式调用实际 Fast/Smart 模型，Runner 记录真实 token、cost 和 latency；
- Dashboard 支持一键四策略 matrix、显式 “uses models” live matrix 和横向对比表。

**遇到的实现问题与解决**：

- 只看 wall-clock 会把网络抖动误当策略差异：deterministic 模式使用同一模拟参数，
  live 模式再验证真实系统表现；
- latency/cost 是越低越好，而现有 baseline comparator 默认 score 越高越好：
  suite 输出 `1000 / critical_path_ms`、`sum_stage_ms / critical_path_ms` 和
  `1 / cost` efficiency 指标供 gate，原始 latency/token/cost 仍保存在 Run/Result；
- 不同 variant 若共用最近 baseline 会产生错误比较：Dashboard 按
  `dataset + variant + execution_mode` 选择 baseline。

**验证**：4 个 multi-agent starter cases 进入 `make eval-fast`；矩阵测试确认完整 DAG
质量、single-agent 漏项、sequential critical path 变长、no-synthesis coherence 下降。

**剩余边界**：确定性 benchmark 验证实验管线与相对机制，不替代真实模型结论；live
matrix 应在固定模型版本、温度、预算和至少 30 次重复下报告置信区间。
