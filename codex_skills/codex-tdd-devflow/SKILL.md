---
name: codex-tdd-devflow
description: 执行 codex_devflow_scaffold 的 Stage0-Stage8 流程。用于初始化工作流目录、运行阶段、处理门禁决策（approve/reject/goto_stage/run_stage8）、写入结构化产物与事件日志。
---

# Codex TDD Devflow

## 使用场景

- 你需要在本仓库执行规划文件 `codex_devflow_scaffold/skill_implementation_plan.md` 定义的流程。
- 你需要一个可恢复的状态机（`run-stage` / `run-auto` / `resume`）。
- 你需要把状态、阶段产物、门禁、决策与事件日志落盘。

## 触发词（标准化）

以下表达应触发本 Skill：

- `运行 stage0-8 工作流`
- `执行 tdd devflow`
- `继续 workflow runner`
- `处理 pending 决策`
- `run-auto / resume / run-stage`

## 命令入口

统一使用（自包含入口）：`codex_skills/codex-tdd-devflow/scripts/workflow_runner.py`

```bash
python codex_skills/codex-tdd-devflow/scripts/workflow_runner.py init
python codex_skills/codex-tdd-devflow/scripts/workflow_runner.py run-auto
python codex_skills/codex-tdd-devflow/scripts/workflow_runner.py status
python codex_skills/codex-tdd-devflow/scripts/workflow_runner.py gate
python codex_skills/codex-tdd-devflow/scripts/workflow_runner.py stage3-semantic --keep-all --reason "session event review"
python codex_skills/codex-tdd-devflow/scripts/workflow_runner.py stage4-semantic --keep-all --reason "session semantic review"
python codex_skills/codex-tdd-devflow/scripts/workflow_runner.py resume --auto
python codex_skills/codex-tdd-devflow/scripts/workflow_runner.py decision-template
python codex_skills/codex-tdd-devflow/scripts/workflow_runner.py decide --approve --apply --auto
python codex_skills/codex-tdd-devflow/scripts/workflow_runner.py recalc-analyze
python codex_skills/codex-tdd-devflow/scripts/workflow_runner.py semantic-snapshot
python codex_skills/codex-tdd-devflow/scripts/workflow_runner.py stage8-rollback
python codex_skills/codex-tdd-devflow/scripts/workflow_runner.py case-web --host 127.0.0.1 --port 8787
```

## 决策流程

当 `run-auto` 返回 `waiting_decision` 时：

强约束（必须遵守）：
- 进入 `waiting_decision` 后，只允许先执行 `gate` 并展示摘要，然后停止等待用户明确指令。
- 未收到用户明确指令前，禁止执行 `decide`、`run-auto`、`resume --auto`。例外：Stage3/Stage4 语义自动收敛可由当前 session 在门禁前自动执行（仅更新语义产物，不做决策动作）。
- 向用户请求决策前，必须先给出“可决策摘要”：`current_stage`、`reason`、`decision_id`、`allowed_actions`、`approve/reject/goto_stage` 的后续影响、关键产物要点与风险（如 `semantic_review` / `test_generation` / 回归结果）。
- 向用户请求决策前，必须补充“决策信息清单（按阶段）”：
  - Stage0：候选 API 列表、每个候选的选择理由（语义来源/去重依据）、风险等级、`approve` 后将进入的下一阶段。
  - Stage3：本轮 session 选择保留的测试事件（`selected_event_ids`）、被剔除事件、事件对应业务状态码与语义理由。
  - Stage4：本轮 session 选择保留的测试用例（`selected_case_ids`）、生成的测试函数名、测试代码就绪状态（`test_generation` 是否 ready、missing_tests 是否为空）。
  - Stage5/6：代码回归结果（通过/失败、失败分布、关键 stdout/stderr 摘要）、对下一步决策的影响。
  - 非适用字段必须明确标注 `N/A`（例如 Stage0 的“测试执行结果”尚未产生时必须写 `N/A`），禁止省略字段导致用户误判。

标准步骤：
1. 执行 `python codex_skills/codex-tdd-devflow/scripts/workflow_runner.py gate`（会直接输出可读摘要）
2. 等待用户明确回复（`approve/reject/goto_stage/run_stage8` 或要求先做 Stage3/4 语义筛选）
3. Stage0 非 bootstrap 轮次：每次重新进入 Stage0 时，必须先由当前 Codex session 基于“最新业务文档 + 本会话用户指令”重写 `codex_devflow_scaffold/cases/session_api_candidates.jsonl`（不可复用旧提名），再由 Runner 读取并提名待开发 API
3.1 Stage0 提名前必须做去重校验：候选 `api_key` 只要命中 `api_registry` 或“当前代码实时扫描已观测 API”，都视为无效候选（防止重复提名上一轮已实现接口）
4. 每次提名 API 后，当前 Codex session 必须先在项目代码中核查该接口是否已正确实现（路由/视图/序列化/权限/业务码）；若未实现或实现不符合语义，必须先完成实现再继续后续阶段
4.1 一旦确认并开始实现该提名 API，必须在实现位置（如 ViewSet action / 视图函数）补充详细中文注释，明确说明该 API 的业务作用、适用边界、输入输出与关键业务状态码语义
5. 每次实现/修改 API 后，必须先在项目内部补充对应业务测试（如 `apps/*/tests.py`），再继续 Stage3/Stage4；禁止只依赖 generated 测试文件
6. Stage3 门禁若发现 `semantic_review.status=pending`，当前 Codex session 必须先自动完成 Stage3 语义筛选（业务意义审核 + stage3-semantic），再输出 gate 摘要等待用户 approve；禁止把这一步甩给用户手工执行
7. Stage4 默认 `case_design_mode=session`：先由当前 Codex session 编写 `codex_devflow_scaffold/cases/session_case_candidates.jsonl`（业务用例来源由 session 思考，不由 Runner 笛卡尔积生成），并确保每条 case 的 `api_refs` 可追溯到业务事件 API（`business_events.api_refs`）或 Stage0 本轮 focus API，再执行 `python codex_skills/codex-tdd-devflow/scripts/workflow_runner.py stage4-semantic ...`
8. Stage4 语义筛选后，由当前 Codex session 继续生成/更新测试代码到 `generated_test_files`（Runner 不代写）
9. 执行 `python codex_skills/codex-tdd-devflow/scripts/workflow_runner.py decide --approve --apply`（默认不跨门禁自动续跑）
10. 进入 Stage6 时，当前 Codex session 需先执行“自动同步 + 复检”流程：若发现触达实体四件套不一致，先在 Stage6 内自动同步（`业务侧实现/{entity}_data_dictionary.md`、`业务侧实现/{entity}_impl_desc.md`、`业务侧实现/{entity}_logical_model.md`、`业务侧实现/{entity}_schema.dbml`）并复检；仅复检仍失败才门禁阻断
11. Stage6 的触达实体识别采用“session 语义理解优先（stage0/1/2 语义上下文 + API 语义）”；默认不要求用户手改 `session_entity_review.json`，由当前 session 自动给出实体集合供审核；若用户提出修正意见，由当前 session 代为更新后重跑 Stage6

`session_case_candidates.jsonl` 最小字段（每行一个 JSON 对象）：

```json
{"case_id":"CASE-SESSION-I001-001","event_id":"EVT-001","api_refs":["POST /api/v1/drones"],"expected_business_code":"SUCCESS","title":"创建无人机成功"}
```

`session_api_candidates.jsonl` 最小字段（每行一个 JSON 对象）：

```json
{"api_key":"POST /api/v1/drones/{id}/assign-mission","reason":"为任务下发补全无人机任务绑定能力","risk":"medium","entity":"drone"}
```

`session_entity_review.json`（Stage6 实体审核记录；默认不需用户手改）：

```json
{"schema_version":1,"mode":"append","entities":[],"reason":"默认使用 Stage6 自动推断实体集合；如需修正由 session 代更"}
```

说明：Stage6 门禁 `approve` 后会把 `current_stage` 置为 0（进入下一轮）。此时如果再执行 `run-auto`，会继续跑到“下一轮 Stage0 门禁”。

标准化快捷方式（推荐）：

```bash
python codex_skills/codex-tdd-devflow/scripts/workflow_runner.py gate
python codex_skills/codex-tdd-devflow/scripts/workflow_runner.py decide --approve --apply
python codex_skills/codex-tdd-devflow/scripts/workflow_runner.py decide --reject --apply
python codex_skills/codex-tdd-devflow/scripts/workflow_runner.py decide --goto-stage 1 --reason "retry stage1" --apply
python codex_skills/codex-tdd-devflow/scripts/workflow_runner.py decide --run-stage8 --apply
python codex_skills/codex-tdd-devflow/scripts/workflow_runner.py stage3-semantic --keep-event-id EVT-001 --keep-event-id EVT-002 --reason "保留本轮业务高风险事件"
python codex_skills/codex-tdd-devflow/scripts/workflow_runner.py stage4-semantic --keep-case-id CASE-AUTO-I001-001 --keep-case-id CASE-AUTO-I001-002 --reason "保留高价值场景"
python codex_skills/codex-tdd-devflow/scripts/workflow_runner.py decide --approve --apply --auto --allow-auto-continue
```

## P2 能力

- `case-web`：提供测试用例 Web CRUD（列表/创建/编辑/删除）。
- `recalc-analyze`：分析语义/产物/配置变更对下游 Stage 的影响，输出建议重跑起点。
- `stage8-rollback`：从 Stage8 产物自动生成回退分支（支持 dry-run / apply）。
- Stage2 API 扫描支持离线 fallback：在线 schema/root 不可用时，自动调用本地 `manage.py spectacular` 扫描（免启动服务）。
- Stage3 会先生成 `business_event_candidates`，并进入语义门禁；由当前 Codex session 通过 `stage3-semantic` 选择保留业务事件后再推进 Stage4。
- Stage4 默认不再由 Runner 自动笛卡尔积生成业务用例；`candidate_cases` 来自当前 Codex session 编写的 `cases/session_case_candidates.jsonl`。
- Stage4 的测试用例与测试代码都由当前 Codex session 负责：先产出 case，再产出 test；禁止占位/模板空壳测试。
- Runner 会校验 Stage4 测试是否包含语义信号：真实 HTTP 请求、目标 API 路径引用、`business_code` 与 `business_detail_code` 断言。
- Stage4 同步产出中文业务事件说明（`codex_devflow_scaffold/cases/business_events_zh.md`）。

## 约束

- 工作流系统文件由 Runner 维护：`state.json`、`pending.json`、`events.ndjson`。
- Stage 0 语义门禁未通过时，不推进到 Stage 1。
- Stage0 规则修正：仅 bootstrap 轮次使用“已扫描观测到且未注册”的 API 候选；非 bootstrap 轮次改为由当前 session 提名 `session_api_candidates.jsonl`。
- Stage0 重入规则：每次决策回到 Stage0 后，Runner 会要求 `session_api_candidates.jsonl` 的修改时间晚于本次“进入 Stage0”的时间点；否则进入 `stage0_session_replan_required` 门禁（仅允许 `reject/goto_stage`）。
- Stage0 去重补充：Runner 会拒绝提名“已在 `api_registry` 注册”或“当前代码扫描已观测到”的 API（即使 registry 因时序暂未更新，也按已存在处理）。
- 总体设计对齐前置检查：每轮提名 API 或新增/修改数据表前，当前 session 必须先回顾 `项目总体概览/`（至少 `业务接口扩展指南.md`、`逻辑设计/overall_data_dictionary.md`、`逻辑设计/overall_logical_model.md`），确认实体边界、表结构与命名语义不偏离总体设计。
- 总体设计偏差处理：若发现“本轮拟实现实体/表”与 `项目总体概览/` 定义明显冲突（字段语义、主外键关系、实体归属），必须先在当前轮给出对齐修正再推进；禁止带着已识别偏差进入 Stage4 之后阶段。
- 字段语义确定性约束：对“业务语义尚未确认”的字段（例如命名含糊、取值语义未定、状态机未定），允许保留一个中性“扩展位/占位字段”实现；但禁止提前固化详细枚举语义（如一次性写多个业务类型常量）。
- 待定项落档规则：语义未定字段必须在对应实体文档记录“待定项（TBD）”，说明“当前占位实现 + 需要确认的问题”；禁止把猜测语义写成正式业务约束。
- 每次 Stage0 提名 API 后，当前 Codex session 必须在项目中核查“接口是否已正确实现”；若未实现或实现与提名语义不一致，必须先完成代码实现再推进后续阶段。
- 每次提名 API 进入实现阶段时，必须在接口实现处添加详细中文注释，至少覆盖：业务目的、边界条件、请求/响应语义、关键 `business_code`/`business_detail_code` 含义。
- API 实现完成后，必须在项目内部测试文件补充/更新对应测试（例如 `apps/*/tests.py`）；`generated_test_files` 仅用于流程门禁，不可替代项目内回归测试。
- `generated_test_files` 生命周期规则：`codex_devflow_scaffold/tests/generated/test_generated_specs.py` 属于“当轮门禁临时产物”，允许每轮覆盖；禁止将其作为长期回归基线。
- Stage5 回归沉淀校验规则：Stage5 必须校验“Stage4 `selected_case_ids` 已在项目内测试（`apps/*/tests.py`）有对应沉淀（本轮新增或既有可追溯用例）”；若仅有 generated 测试而无项目内沉淀，必须阻断并先补齐沉淀再继续。
- API 设计原则：默认只设计“简单、单一职责、可组合”的基础接口；业务编排应由外部调用方组合基础接口完成（例如“关闭旧分配 + 新建分配”），不应优先新增编排型接口。
- 编排型接口例外：仅在必须保证跨步骤强原子一致性、且无法通过外部组合可靠实现时才允许提名；Stage0 `reason` 必须写明“为什么基础接口组合不足”与一致性风险。
- 实体边界约束：每个业务实体（如 `drone`/`drone_assignment`/`route`）默认必须落在独立 app（`apps/<entity>/`）中实现其 model/serializer/view/urls；禁止把新实体继续堆进历史 app 目录，除非用户明确批准“共享 bounded context”例外。
- 历史实体治理约束：若扫描发现“实体 API 路径与代码归属 app 不一致”（例如 `drone-assignments` 仍在 `apps/drone`），当前 session 必须在本轮修复为独立 app，再推进后续 Stage。
- API 面收敛约束：本轮提名 API 若为单接口增量，代码新增/修改后对外暴露的“新增 API 面”必须是提名集合子集；禁止因 ViewSet mixin 默认行为额外暴露未提名的 list/retrieve/update/delete 接口。
- API 面校验规则：Stage2/Stage3 必须对比“本轮 focus API + 基线已存在 API”与当前扫描结果；若出现本轮引入的未提名新增接口，必须在当前阶段自动收敛（改路由/改 mixin）后再允许进入 Stage4。
- Stage2 漂移自愈规则：当 Stage2 扫描发现 `missing_online`（注册表有但当前代码扫描不可观测）时，当前 session/Runner 必须先按“代码扫描结果为准”自动收敛 `api_registry`（清理陈旧项并重算漂移），把产物修复到 Stage3 可接受状态；仅当自动修复后仍存在 unresolved `missing_online` 时才允许门禁阻断。
- Stage2 候选补实现规则：当 Stage2 发现 `missing_candidate_apis`（本轮候选 API 仍不可观测）时，当前 Codex session 必须在 Stage2 内直接补实现该候选 API（含业务码、中文注释、项目内测试）并重跑 Stage2；仅在自动补实现尝试后仍不可观测时，才允许向用户展示 Stage2 阻断门禁。
- Stage 4 测试覆盖码来源于 Stage 0 的 `required_case_codes`。
- Stage 6 会校验“触达实体四件套”与权限文档影响；若发现不一致，必须先由当前 session 基于上下文自动同步并复检，复检仍失败才阻断在 Stage6（不自动回退 Stage1）。
- 责任归属：业务侧四件套文档同步由当前 Codex session 负责；Runner 负责触发检查与记录自动同步结果。
- Git 迭代提交规则：每新增 1 个 API 且完成 1 轮迭代后，提交检查点固定在 Stage6。
- Stage6 自动同步规则：若 Stage6 检查到文档/结算一致性缺口，当前 Codex session 必须先基于上下文自动完成同步修复（实体四件套、必要结算文档）并在 Stage6 内复检；仅当自动修复后仍失败，才向用户展示 Stage6 阻断门禁。
- Stage6 文档编写规则：实体四件套正文必须由当前 Codex session 基于“本轮业务语义 + 代码实现 + 可追溯产物”撰写；禁止机械模板粘贴或仅写占位提示。
- Stage6 文档增补规则：实体四件套应采用“增补本轮内容”而非覆盖旧轮内容；需保留历史轮次语义，并新增“本轮 API/业务码/可追溯来源”段落。
- Stage6 非破坏同步规则：Runner 自动同步仅允许“非破坏式增补”（append metadata / context），禁止整文件覆盖已有业务正文。
- Stage6 去硬编码规则：禁止在 Runner 自动同步逻辑中硬编码业务结论、字段语义、事件/用例描述；业务正文必须由当前 session 基于上下文语义生成。
- Stage6 证据可追溯规则：实体文档禁止写不可追溯的测试证据（如孤立测试函数名列表）；若需引用证据，必须指向可定位产物路径（如 stage artifact 文件）。
- Stage6 首次实体建档规则：若某实体为首次出现（`业务侧实现/` 下不存在该实体四件套），当前 session 必须在本轮创建完整四件套（`{entity}_data_dictionary.md`、`{entity}_impl_desc.md`、`{entity}_logical_model.md`、`{entity}_schema.dbml`）后才可通过 Stage6。
- Stage6 语义新鲜度规则：文档同步检查不能只看“文件存在或曾被修改”；必须校验“本轮触达实体文档在本轮时间窗内有更新”且“文档包含本轮 focus API 的语义引用”。任一不满足都视为未同步，必须在 Stage6 自动修复并复检。
- Stage6 语义正文校验规则：`Stage6 同步上下文（可追溯）` / `trace_context` 仅算元数据，不计入业务正文语义；门禁必须在“剔除 trace 块后的正文”中检查本轮 focus API 引用，否则一律阻断。
- Stage6 同后缀参考规则：编写 `{entity}_data_dictionary.md|{entity}_impl_desc.md|{entity}_logical_model.md|{entity}_schema.dbml` 时，必须先参考 `业务侧实现/` 下同后缀既有文档的结构与粒度，保持风格一致。
- Stage6 实体审核规则：Stage6 门禁摘要必须展示“语义推断实体、用户覆写实体、最终实体”；默认以 session 语义推断为主，用户审核覆写仅用于纠偏。
- Stage6 commit 审核规则：Stage6 必须先生成“待提交 commit 审核信息”（commit message、拟 `git add` 文件清单、排除文件清单）并展示给用户；未经用户明确审核批准，禁止执行 `git add` / `git commit`。
- Stage6 commit 执行点：仅在用户明确批准 Stage6 的 commit 审核信息后，当前 session 才可执行本轮 `git commit`（按白名单挑选文件）。
- Git 提交白名单：仅允许提交“实体四件套文档变更（`业务侧实现/{entity}_*.md|*.dbml`）+ 代码实现变更（如 `apps/*`、必要 `config/*` 路由接线）+ 项目内测试变更（如 `apps/*/tests.py`）+ 必要迁移文件（`apps/*/migrations/*.py`）”。
- Git 提交黑名单：禁止提交 Skill 运行产物（含 `codex_devflow_scaffold/**` 的 `artifacts/inputs/logs/registry/docs/cases/tests/generated/state/decisions` 等）与其它临时产物；除非用户明确要求，不提交 `codex_skills/**`。
- 执行提交时必须显式按文件挑选 `git add <path>`，避免把非白名单文件混入提交。
- 离线扫描可通过 `workflow_spec.business_api.local_scan_command` 覆盖默认命令。
- 默认 `approval_mode=manual`：`--auto` 续跑需显式追加 `--allow-auto-continue`。
- Stage 0 候选会跳过 `api_registry` 已存在的 `api_key`，避免重复提名同一接口。
- Stage3 approve 前必须先完成业务事件语义筛选确认（`semantic_review.status=approved`）。
- Stage3 自动收敛规则：一旦进入 Stage3 waiting_decision 且 `semantic_review.status=pending`，当前 session/Runner 必须先自动执行语义筛选并回写产物，再向用户展示门禁摘要。
- Stage4 自动收敛规则：一旦进入 Stage4 waiting_decision 且 `semantic_review.status=pending`，当前 session/Runner 必须先自动执行语义筛选（必要时先同步 `session_case_candidates.jsonl`）并回写产物，再向用户展示门禁摘要。
- Stage3 语义筛选必须体现“业务意义审核”结果：每个保留/剔除事件都要有业务语义理由；若最终全量保留，必须明确说明“为何每个事件都仍有业务价值”，禁止无理由机械通过。
- Stage3 Stage4-ready 约束：无论 Stage2 状态如何，Stage3 必须把事件产物整理到 Stage4 可接受状态（API 可观测、事件非空、event.api_refs 对齐 focus_api_keys、expected_business_codes 非空）；未满足时必须阻断在 Stage3，不得进入 Stage4。
- Stage4 approve 前必须先完成语义筛选确认（`semantic_review.status=approved`）。
- Stage4 approve 时会校验 `generated_test_files` 是否覆盖全部 `expected_tests`，且每个测试满足语义信号校验；不满足时不允许进入 Stage5。
- Stage4 `case_design_mode=session` 下，若 `session_case_candidates.jsonl` 无有效用例，`stage4-semantic` 会阻断并要求先补齐 case。
- Stage4 可追溯性校验：`generated_cases[*].api_refs` 必须可追溯到对应业务事件（`business_events[*].api_refs`）或 Stage0 本轮 focus API；若出现跨轮残留 API（如上一轮 history 混入本轮 active），`stage4-semantic` / approve 都必须阻断。
- Stage6 注册兜底：Runner 会用“当前代码实时扫描 ∩ Stage0 本轮候选”补齐 `api_registry`，修复 Stage2 快照时序导致的漏登记问题，避免下一轮重复提名。
- 每个 Stage 均产出 `stage_contract`（阶段契约检查）。若 `stage_contract.status=blocked` 且 `blocking=true`，Runner 必须在当前 Stage 门禁阻断，要求先修复到该 Stage 的可接受状态后再推进。
- 核心原则：`Stage n` 必须把产物处理到 `Stage n+1` 可接受状态（`ready_for_next_stage=true`）后才能推进；若未满足，必须在当前 Stage 阻断并回到推荐修复阶段，不得“带病流转”。
- 会话自动收敛原则：若当前门禁阻断原因属于“可由当前 Codex session 自动修复的同步问题”（如 Stage4 case/api_refs 与本轮 focus API 错配、generated tests 与 case 不一致），必须先由 session 自动完成修复（改 `session_case_candidates.jsonl`、改 `generated_test_files`、必要时执行 `stage4-semantic --sync-session-cases` 并重跑当前 Stage）后，再向用户请求 approve。
- Stage4 自动修正规则：当出现 `stage4_not_ready_for_stage5_traceability_failed` 时，当前 session 不应直接把 `reject/goto_stage` 甩给用户；应先自动完成“本轮 focus API 对齐 + 语义重审 + 当前 Stage 重跑”，仅在自动修复仍失败时才向用户展示阻断并说明失败原因。
- 任何 `waiting_decision` 门禁点，若用户未明确授权，不得自动执行后续决策命令（含 `decide` / `run-auto` / `resume --auto`）。
- 任何门禁提问都必须给出有助于决策的明确信息，不得只输出“请 approve/reject”而不附上下文与影响说明。
- 门禁摘要必须明确回答三个问题：为什么是这个候选（或事件/用例）、当前回归状态如何（不适用则写 `N/A`）、用户本次决策会带来什么后果。
- 门禁摘要字段采用“按阶段适用”规则：只要是该阶段必须字段就必须给值；不适用字段必须显式写 `N/A`。不是每个阶段都涉及测试用例或测试执行结果。
- Stage 5 会先校验 Stage4 生成测试的“预期数量/命名/执行结果”一致性，再汇总全量回归结果。
- Stage5 职责边界：仅对“代码与测试执行结果”给出回归结论，不负责实体四件套文档同步判定。
- Stage6 职责边界：负责结算一致性（registry/doc 与代码对齐、实体四件套同步）与最终提交关口（commit 审核 + 提交）；若文档未同步，先由当前 session 自动修复并在 Stage6 复检，修复失败后才门禁阻断。
- Stage 0 若本轮无可推进候选，会进入 `stage0_no_candidate_blocking` 门禁（仅允许 `reject/goto_stage`）。
- Stage 0 若命中“候选未重规划”阻断，会进入 `stage0_session_replan_required` 门禁（仅允许 `reject/goto_stage`）。
- Bootstrap 轮次允许按扫描到的既有 API 批量初始化校验，不受“每轮新增 1 个 API”限制。
- Stage4 生成测试时优先校验业务状态码字段（默认候选：`business_code/business_detail_code`），HTTP 状态码仅作辅助校验；不得以“仅校验产物 JSON/元数据”的方式替代真实接口测试。

## 门禁摘要最小清单（按阶段）

- Stage0（候选 API 审核）：
  - 必含：候选 API、选择理由（含去重/来源）、风险等级、approve/reject/goto_stage 影响。
  - 若候选属于编排型接口，必须补充“不可由基础接口组合替代”的理由；否则应改为基础接口候选。
  - 可选/不适用：测试事件/测试用例/回归结果（通常 `N/A`）。
- Stage3（业务事件语义审核）：
  - 必含：`selected_event_ids`、剔除事件、每个保留事件的业务状态码与语义理由、决策影响。
  - 必含：业务意义审核结论（是否覆盖主流程/关键失败路径/权限与状态约束；若未覆盖需说明缺口）。
  - 可选/不适用：测试代码执行结果（通常 `N/A`）。
- Stage4（测试用例语义 + 代码就绪审核）：
  - 必含：`selected_case_ids`、`expected_tests`、已生成测试函数名、`test_generation.status`、`missing_tests`、`traceability_check`（是否满足事件→用例→API 可追溯）、决策影响。
  - 可选/不适用：全量回归结果（若尚未执行则 `N/A`）。
- Stage5（回归执行结果审核）：
  - 必含：生成测试执行结果、项目内测试执行结果（`apps/*/tests.py`）、全量回归结果、失败分布、关键错误摘要、`selected_case_ids -> 项目内测试` 沉淀映射、决策影响。
- Stage6（结算/最终门禁）：
  - 必含：registry/doc 同步结果、实体审核信息（语义推断/用户覆写/最终实体）、回归状态摘要（来自 Stage5）、commit 审核信息（拟提交 message/文件清单/排除清单）、本次决策对下一轮的影响（approve 会回 Stage0）。
- Stage7/Stage8（发布或回退门禁，若启用）：
  - 必含：发布/回退动作摘要、受影响范围、可逆性说明、决策影响。
  - 不涉及测试时，测试字段必须写 `N/A`。

## 阶段契约（Stage Contract）

- Stage0：
  - 目标状态（面向 Stage1）：候选 API 是增量且可推进（无语义缺口阻断、无重规划阻断、候选非空）。
  - 失败处理：留在 Stage0，先修复语义缺口/重提候选。
- Stage1：
  - 目标状态（面向 Stage2）：实现任务可执行（`implementation_tasks` 非空且覆盖接口实现关键项），并已区分“已确认字段”和“待定字段（占位实现 + 文档待定项）”。
  - 失败处理：回到 Stage0 重做任务拆解输入。
- Stage2：
  - 目标状态（面向 Stage3）：扫描结果可用于判断候选 API 观测状态，且 `missing_online` 漂移已在 Stage2 自动收敛或被证明已解决（无 unresolved 漂移项）。
  - 失败处理：若是 unresolved `missing_online`，优先留在 Stage2 继续自动修复 registry/扫描一致性；若是 Stage0 候选 API 仍不可观测，优先在 Stage2 直接补实现并重跑，仍失败再门禁阻断。
- Stage3：
  - 目标状态（面向 Stage4）：把输入处理到 Stage4 可接受状态（Stage4-ready）：
    - 候选 API 可观测（`unimplemented_candidate_api_count=0`）
    - `business_events` 非空
    - 每个事件 `api_refs` 可追溯到 `focus_api_keys`
    - 每个事件 `expected_business_codes` 非空
    - 代码实体归属符合边界（本轮触达实体已在独立 app，未出现“新实体写入错误 app”）
    - 本轮新增 API 面与提名一致（无未提名扩散接口）
    - 未出现“语义未定字段被过度固化”的违规项（允许占位字段，不允许提前固化详细枚举语义）
  - 失败处理：若 API 不可观测回 Stage1 补实现；若事件语义不满足 Stage4-ready，留在 Stage3 修正事件后再推进。
- Stage4：
  - 目标状态（面向 Stage5）：事件->用例->API 可追溯，且测试代码就绪（`test_generation` 可用于推进）。
  - 失败处理：留在 Stage4/回 Stage3 修正 case 与测试，不得带错配进入 Stage5。
- Stage5：
  - 目标状态（面向 Stage6）：生成用例与全量回归结果清晰可判定，且 Stage4 选中用例已沉淀到项目内测试（`apps/*/tests.py`）并可追溯。
  - 失败处理：按失败分布回 Stage1 或 Stage7，不可跳过回归结论。
- Stage6：
  - 目标状态（面向下一轮 Stage0）：registry/doc 与代码一致，实体文档同步通过（包含本轮增补、focus API 语义对齐、非破坏式保留历史正文），且已生成可供用户审核的 commit 清单。
  - 失败处理：优先留在 Stage6 自动同步并复检；仅在自动修复仍失败时再人工决策是否回退修复阶段。
- Stage7：
  - 目标状态（面向 Stage1/Stage8）：具备明确可执行干预建议（重跑阶段或回退）。
  - 失败处理：补齐建议后再决策。
- Stage8：
  - 目标状态（面向下一轮 Stage0）：回退/继续决策明确且可执行。
  - 失败处理：留在 Stage8 修正决策输入。
