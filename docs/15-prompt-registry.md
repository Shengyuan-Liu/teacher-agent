# Prompt Registry 与版本化

Prompt Registry 把影响 Agent 行为的提示词从“散落的可变字符串”升级为可识别、可验证、
可追溯和可回滚的工程资产。代码仍保存内置基线，因此 workspace 没有 override 时，
Agent 有明确 fallback；数据库故障则显式失败，避免在不知情的情况下改变线上行为。

## 版本模型

- `PromptDefinition`：workspace 内唯一的稳定 `key`，例如 `router.classify`。
- `PromptVersion`：不可变的 template、变量契约、SHA-256、说明和创建者。
- workspace 版本从 `draft` 原子切换为 `active`；旧 active 进入 `archived`。
- 一个 definition 最多一个 active 版本；reset 会恢复 code-owned builtin。
- 已发布版本不提供 update/delete API，回滚通过重新激活 archived 版本完成。

当前首批覆盖 11 个高影响 prompt：Router、Web/RAG Answer synthesis、Planner
draft/revise、Lecture outline/section/input classification/grading，以及 live
Multi-Agent Benchmark worker/synthesis/semantic Judge。QA、Quiz、Search query generation 等其余
prompt 将按相同接口逐步迁移。

## 运行时契约

`render_prompt()` 会：

1. 按 replay pin → workspace active → builtin 的优先级解析；
2. 要求调用方提供的变量集合与版本声明完全一致；
3. 渲染模板并记录 `key/version/content_hash/source/step`；
4. 把 prompt 元数据附加到 usage model call、Chat 调用链、EvalResult 和 AgentRun。

workspace active 版本有短 TTL 缓存，激活和 reset 会清理当前进程缓存；其他 worker
最多在 `PROMPT_CACHE_TTL_SECONDS` 后收敛。若需要跨进程瞬时一致性，可将其设为 `0`
或后续接入 Redis invalidation。

## Eval 与 Replay

- EvalRun 创建时快照所有 active prompt；每个 case 保存实际使用 manifest。
- AgentRun 同时保存启动快照和实际使用版本。
- Replay `prompt_mode=current` 使用当前版本，适合观察 prompt 变更的回归影响。
- Replay `prompt_mode=original` 按原运行的 version + hash 锁定；版本缺失时 fail closed，
  不会悄悄换成其他 prompt。
- Replay comparison 会显示 prompt manifest 是否变化。

## 管理入口

Workspace 的 **Prompts** 页支持：

- 查看所有稳定 key、变量契约、active 来源和内容哈希；
- 从当前版本创建 immutable draft；
- 激活 draft 或 archived 版本；
- reset 到 builtin。

REST API 位于 `/api/v1/workspaces/{workspace_id}/prompts`，并复用 workspace ownership
检查。模板内容可能包含产品策略或用户定制规则，不应写入公开日志。

## 发布建议

先创建 draft，在 Evaluation Platform 用相同 workspace 跑 baseline 对比；通过 gate
后再激活。对高风险 Router 或 Planner 变更，应保留实验说明、EvalRun ID 和回滚版本，
并在 live matrix 中检查质量、延迟与成本，而不是只凭单条对话判断。
