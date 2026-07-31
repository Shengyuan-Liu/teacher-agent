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
| AE-017 | Prompt Registry | 不可变版本、hash、Eval/Replay 快照 |
| AE-018 | Agent 安全与红队 | 四层 trust boundary、持续安全 gate |
| AE-019 | 成本预算、缓存与熔断 | reservation、tenant cache、分布式 breaker |
| AE-020 | Durable Typed DAG | 节点 checkpoint、execution lease、原图恢复 |
| AE-021 | 实验与韧性证据 | semantic Judge、置信区间、load/fault/SLO 报告 |

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

## AE-017 — Prompt Registry 与不可变版本

**问题**：关键 Agent prompt 分散在代码中，运行记录只知道模型而不知道提示词版本；
直接编辑字符串后，线上 failure、Eval baseline 和 Replay 都无法确认行为变化来自模型、
数据还是 prompt，也缺少安全回滚路径。

**根因**：prompt 没有稳定标识、变量契约、内容 hash、生命周期或与 usage/trace 的关联；
Replay 默认使用当前代码，不能选择原运行版本。

**方案**：

- 建立 code-owned builtin + workspace override 的 Prompt Registry，版本状态为
  `draft/active/archived`，数据库约束每个 key 只能有一个 active；
- 版本不可变，保存严格变量集合、SHA-256、变更说明与创建者；激活和回滚清除缓存；
- 首批迁移 Router、Answer synthesis、Planner、Lecture 和 live Benchmark 共 10 个
  高影响 prompt，保留 builtin fallback；
- prompt `key/version/hash/source` 进入 Chat stage、usage call、AgentRun/AgentSpan、
  EvalRun 快照和逐 case 结果；
- Replay 支持 `current` 做回归、`original` 按 version + hash fail-closed 锁定复现；
- Workspace Prompt Dashboard 支持创建 draft、激活历史版本和 reset 到 builtin。

**遇到的实现问题与解决**：

- JSON 示例中的单花括号被模板解析器误认成变量：builtin 模板统一使用转义花括号，
  创建版本时验证变量集合必须与稳定 key 契约完全一致；
- Lecture 测试和扩展会 monkeypatch 旧函数签名：调用端检测可选 `workspace_id` 参数，
  在不破坏旧实现的前提下启用 workspace override；
- 多 worker 进程缓存无法被本地事件同时清除：当前进程立即 invalidation，其他进程用
  短 TTL 收敛，并允许把 TTL 设为 0。

**验证**：单元测试覆盖模板解析、缺失/额外变量、builtin trace；API 测试覆盖
ownership、创建、重复内容冲突、激活、归档回滚、reset；Replay pin 测试确认 active
升级后仍能解析旧 archived version + hash。完整后端、前端和 fast eval gate 同步运行。

**取舍与剩余边界**：builtin 是可靠 fallback，但修改其内容时仍需人工递增代码版本；
当前只迁移最影响行为的 10 个 prompt，QA/Quiz/Search/repair prompts 需继续纳入；
跨进程即时 invalidation 后续可用 Redis pub/sub，生产 prompt 发布还应增加审批和
Eval gate 自动关联。

## AE-018 — Agent 安全策略与红队发布门禁

**问题**：系统 prompt 虽然提示模型不要服从网页指令，Web 也有 consent 和 SSRF
保护，但安全行为分散且主要依赖模型自觉；没有统一方法验证直接/间接 prompt
injection、secret leakage、improper output 和 excessive agency 是否回归。

**根因**：输入、外部 context、输出和 tool authorization 四个 trust boundary 没有共享
policy contract；Evaluation Platform 也缺少 attack 与 benign false-positive 对照集。

**方案**：

- 新增 deterministic `agent_security` policy，返回统一
  `action/findings/policy_version/safe_text`，finding 只存 detector ID 与 evidence hash；
- Chat 在 Router 前执行 input preflight，阻止系统 prompt、`.env` 和凭据提取；
- RAG、Web、Lecture、Quiz、Explanation、Outline 与 multi-source context 按句扫描，
  可疑片段替换为 quarantine marker，保留相邻事实与 citation；
- 流式 QA 保留短输出窗口，credential marker 完整前不发送；credential 做 redact，
  明确 system-prompt dump 做 block；
- Web authorization 收敛到 deployment enabled + explicit user consent 的代码级 policy；
- 安全 input/context/output stages 进入调用链、Message trace 和 AgentRun/AgentSpan；
- 新增 14-case model-free red-team suite 与 Dashboard starter，`security_accuracy=1.0`
  进入 CI。

**遇到的实现问题与解决**：

- 直接阻止包含 “ignore previous instructions” 的文本会误伤安全课程：检测讨论性表达，
  并用 benign paired cases 锁住 false-positive；
- 丢弃整个恶意 chunk 会损失同一来源里的正常事实：按句/行 quarantine，只在模型看到的
  context 中移除攻击段；
- 模型 token 一旦发到浏览器再检查已经太迟：保留最后 128 字符，在 credential pattern
  完整后先脱敏再继续流式发送；
- 把 attack 原文写入审计会二次复制 secret：trace 只保留分类、detector ID 和 SHA-256。

**验证**：14/14 red-team cases 覆盖中英文 extraction、间接注入、active HTML、
credential/prompt leakage、工具 consent 和 benign preservation；Chat 集成测试确认安全
拒绝不调用 Router/LLM，并把 policy 结果持久化。`make eval-fast` 总计 30/30。

**取舍与剩余边界**：确定性策略可解释且低延迟，但不是完整 DLP/WAF；编码混淆、跨 chunk
拼接、多模态隐写和 adaptive multi-turn attack 仍需 nightly live red team。输出短缓冲会
略微增加首段可见延迟；未来增加有副作用工具时还需 per-tool 最小权限、参数验证和确认级别。

## AE-019 — 请求级成本预算、缓存与依赖熔断

**问题**：一次复合 query 会并发运行 Router、Web/RAG worker、reranker 和 Answer。
原系统只在 provider 返回后统计用量，因此多个 DAG 节点可能同时看到“尚未花费”的预算；
相同 Router/Search 请求会重复付费；provider 故障期间每个请求都会等待同一超时。

**根因**：Usage 是事后 accounting，不是执行前 admission control；已有 prompt/sparse
cache 是模块内进程字典，没有统一 tenant key、跨 worker 命中或审计；重试只在 DAG
节点层，没有按 provider/model 聚合的故障状态。

**方案**：

- 每个 turn 创建共享 `ResourceLedger`，模型 I/O 前按估计 token/cost reservation，
  返回后用 provider usage reconciliation；并发协程共享 outstanding reservations；
- 预计达到 soft ratio 时 Smart→Fast，超过 call/token/已知 cost 任一 hard limit 时
  在 provider I/O 前 fail-closed；未知价格仍执行 call/token 限制并显式标记成本未完整执行；
- Router cache key 纳入 history/question、prompt version/hash、model trace；Web cache
  纳入 provider/query/top-k/filter；两者使用 workspace hash + payload hash，不存原文 key；
- Redis cache miss 使用进程内 keyed lock single-flight；Redis 故障 fail-open 并进入
  cooldown，避免每次调用重复等待连接超时；
- LLM、Tavily、Jina/Cohere/Voyage reranker 共用 breaker；Redis Lua 原子执行
  closed/open/half-open，half-open 跨 worker 只允许一个 probe；Redis outage 使用本地状态机；
- policy 与最终 summary 进入 Chat 调用链；完整治理 payload 进入 Usage、Message、
  AgentRun/Span 与 OTel root attributes；前端 Usage 展示预算、cache、breaker 和降级次数；
- 新增 `resource_governance` suite，作为 Dashboard starter 和 `make eval-fast` CI gate。

**遇到的实现问题与解决**：

- 只比较已返回 token 会在 fan-out 时 oversubscribe：reservation 在 await 前同步写入共享
  ledger，第二个同层协程立即看到第一个 outstanding call；
- 若把治理放在带 `lru_cache` 的 model factory 内，只会检查模型首次创建：改为透明
  ChatModel proxy，在每次 `ainvoke/astream` 的真实 I/O 边界执行预算和 breaker；
- cache key 若直接拼 query 会在 Redis 运维面泄露内容：canonical JSON 只用于本地
  SHA-256，Redis 与 trace 仅保留 payload hash 前缀；
- Redis 自身故障不能让保护层拖慢所有请求：cache bypass，breaker 降为进程内状态，
  并设置 Redis 重试 cooldown。

**验证**：新增 7 个治理单元测试，覆盖 soft downgrade、并发 hard stop、实际 usage
核算、未知价格、tenant key、single-flight 和完整 circuit recovery；5 个 model-free
fault-injection cases 全通过，`make eval-fast` 从 30 扩展为 35 cases。完整设计和配置见
`docs/17-resource-governance.md`。

**取舍与剩余边界**：单个 response 超过 reservation 后无法追回已发生的费用，只能停止
后续调用；streaming 尚未按 output token 中途取消；预算是单 turn 部署配置，不是持久化
用户套餐；Redis outage 时 breaker 跨 worker 暂时不一致；Embedding 计量已纳入预算，
但还没有独立 circuit proxy。

## AE-020 — Durable Typed Task DAG 与进程恢复

**问题**：Typed DAG 虽然在最终 AgentRun 保存快照，但 Web/RAG worker 已完成后若 API
进程退出，用户重试会重新 Router、重新检索并再次付费；两个相同 request 重试也可能并发
执行同一节点。

**根因**：blackboard 只存在于进程内，`Message.client_request_id` 只能识别重复请求，
不能区分“已完成”与“中断待恢复”；执行器也没有 persistence/lease boundary。

**方案**：

- 新增 `task_executions` 和 `task_node_checkpoints`，分别保存原始 DAG、execution 状态、
  worker lease 与节点 status/attempt/result/error；
- 稳定的客户端 `request_id` 作为 execution key；未提供时使用已持久化 user message ID；
- provider I/O 前保存 running/attempt，成功后 materialize JSONB result；
- 同一 request 重试先读取原始 DAG，避免 Router 漂移；已完成节点发出 `restored` event，
  未完成节点继续运行；
- execution lease 用数据库行锁取得和续期，活跃 owner 存在时第二个 worker 返回
  `duplicate/in_progress`，中断或 lease 过期后允许接管；
- 调用链与 done payload 增加 `checkpoint/execution_id/resumed`。

**遇到的实现问题与解决**：

- 只恢复结果、不恢复原图会让 nondeterministic Router 产生 hash mismatch：重试在 Router
  前加载数据库中的 DAG definition；
- async generator 被客户端断开时普通 return 不会执行收尾：executor 用 `finally`
  将 execution 标记为 interrupted 并释放 lease；
- 已完成 synthesis 恰好在 assistant message 提交前中断：恢复流把 Answer checkpoint
  当作完成事件重新持久化 Message，不再次调用模型。

**验证**：单元测试模拟首层 worker 完成后关闭 generator，新 executor 只执行 Answer；
PostgreSQL 集成测试验证 materialized result、竞争 lease 拒绝和 interrupted takeover；
迁移已在本地 PostgreSQL 从 `a91d5e7c42bf` 升到 `b72f6e2c9d10`。

**取舍与剩余边界**：这是结果幂等、at-least-once 语义，不宣称 provider I/O exactly-once；
进程若在 provider 返回和 checkpoint commit 之间退出仍可能重发。有副作用工具必须把
execution/node key 传给下游。当前依赖全是 required，可选依赖和部分结果降级尚未实现。

## AE-021 — 可复现 Benchmark、语义 Judge 与韧性证据

**问题**：系统已有 deterministic 消融和治理单测，但求职展示缺少可复查的分布报告、
真实模型结果、并发压力和故障恢复证据。首轮 live benchmark 还把模型同义改写错误判为
claim 缺失，使 Typed DAG 得到不可信的 0% pass rate。

**根因**：原报告只在 Dashboard 临时展示均值，没有固定 Git/model/prompt/seed 和
case-level artifact；live 与 deterministic 共用完整句子 substring matcher；测试没有
P50/P95、bootstrap CI 或并发 fault profile。

**方案**：

- 新增 benchmark CLI，固定四策略、4-case dataset、Git SHA、Fast/Smart/Judge 模型，
  保存每个 answer、token、成本、关键路径和 2,000-sample bootstrap 95% CI；
- deterministic 保留严格 contract matcher；live 使用版本化 `benchmark.judge`
  按 semantic entailment、引用、顺序和连贯性评分，结构化结果经过 bounded recovery；
- 发布 480-sample deterministic report 和 48-sample live pilot，不删除不利结果；
- 新增 200-turn、50-concurrency resilience profile，直接压实际 DAG/retry、预算
  reservation、cache single-flight 和 circuit half-open 状态机；
- 新增通用 HTTP load probe；本地 readiness 500/500 成功，availability 100%，
  P95 235.247ms，满足预设 99% / 500ms SLO；
- CI 增加 3-repeat deterministic report 和 40-turn/20-concurrency resilience smoke。

**验证**：

- live pilot 48 个策略样本与 48 个 Judge，无 JSON repair；结果显示 single-agent 质量
  最高，Typed DAG 仅相对 sequential 略高质量/较短关键路径，对 no-synthesis 没有质量
  优势却显著增加延迟与成本；
- resilience 600 个 DAG turns 覆盖 healthy、transient timeout 和 permanent timeout；
  预算 12/50 admission、50-request cache stampede 单次计算、50 次 open-circuit block
  和唯一 half-open probe 全部通过；
- 报告及 case-level JSON 位于 `docs/reports/`，生成器、统计函数与故障 gate 有自动化测试。

**取舍与剩余边界**：3-repeat live 是 pilot，不能宣称稳定百分比收益；Judge 与 Answer
都使用 Terra，仍有 self-preference 风险，正式实验应换独立模型并扩展到 30+ repeats。
HTTP readiness 不代表付费 Chat 端到端 SLO；部署后还需要 authenticated canary、soak
test 和外部区域负载。
