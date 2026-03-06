# Codex TDD 工作流实现规划书

## 1. 文档定位

本文件是开发执行规范，用于实现 `codex-tdd-devflow` Skill 与 `workflow_runner`。

目标：
- 固化 0-8 阶段状态机。
- 固化阶段输入输出契约。
- 固化门禁、回退、日志、文档产出规则。
- 保证每轮迭代可追踪、可回放、可审计。

## 2. 总体原则

- 仅使用 Codex CLI 作为模型执行入口。
- 默认自动推进流程，仅在门禁阶段向用户请求决策。
- Stage 0 到 Stage 8 全部采用结构化产物（JSON + schema）。
- Stage 4 状态码覆盖必须来自 Stage 0 的 `required_case_codes`，禁止写死固定码值集合。
- 业务语义不完整时，Stage 0 必须阻断，不允许产出 API 候选。
- 每轮迭代仅允许新增 1 个 API。

## 2.1 Skill 行为定义（本轮约束）

1. 参考目录固定为三类：
- `项目总体概览/`
- `业务侧实现/`
- `权限管理侧实现/`

2. 读写边界固定：
- `项目总体概览/`：只读，Skill/Runner 不允许自动改写。
- `业务侧实现/`：可写，允许随迭代更新并对齐代码实现。
- `权限管理侧实现/`：可写，允许随迭代更新并对齐权限实现。

3. 业务实体文档规则：
- 每个业务实体都对应 `业务侧实现/` 下四个文件：
1. `<entity>_data_dictionary.md`
2. `<entity>_impl_desc.md`
3. `<entity>_logical_model.md`
4. `<entity>_schema.dbml`
- 开发过程中，Skill 可按进度新增或修改这四个文件，使文档持续与代码实现一致。

4. 权限建模规则：
- 不同用户对不同实体的操作权限建模统一维护在 `权限管理侧实现/`。
- 开发过程中按迭代进度升级该目录中的权限文档与模型。

## 3. 目录与材料

## 3.1 必要目录

1. 全局基线目录（只读）
- 默认：`项目总体概览/`
- 作用：提供全局语义与总体结构基线，不参与迭代自动改写。

2. 业务实现目录（可写）
- 默认：`业务侧实现/`
- 作用：随迭代更新，持续与代码实现保持一致。

3. 权限实现目录（可写）
- 默认：`权限管理侧实现/`
- 作用：随迭代更新，持续与权限实现保持一致。

4. 工作流目录（可写）
- 默认：`codex_devflow_scaffold/`

5. 执行器与技能目录
- Runner：`tools/workflow_runner.py`
- Skill：`codex_skills/codex-tdd-devflow/`

## 3.2 核心文件

- `codex_devflow_scaffold/workflow_spec.json`
- `codex_devflow_scaffold/state.json`
- `codex_devflow_scaffold/semantic/business_semantic_model.json`
- `codex_devflow_scaffold/inputs/stageX.json`
- `codex_devflow_scaffold/artifacts/stageX/latest.json`
- `codex_devflow_scaffold/schemas/stageX_output.schema.json`
- `codex_devflow_scaffold/prompts/stageX.md`
- `codex_devflow_scaffold/registry/api_registry.json`
- `codex_devflow_scaffold/registry/case_registry.json`
- `codex_devflow_scaffold/cases/case_descriptions.jsonl`
- `codex_devflow_scaffold/decisions/pending.json`
- `codex_devflow_scaffold/decisions/decision.json`
- `codex_devflow_scaffold/logs/events.ndjson`
- `codex_devflow_scaffold/docs/*.md`

测试脚手架约束：
- 用例描述与测试代码分离。
- 中文用例描述存放在 `codex_devflow_scaffold/cases/case_descriptions.jsonl`。
- Stage 4 负责把用例描述映射为可执行测试代码。
- `case_registry.json` 维护 `case_id -> test_file/test_name/event_id/api_refs` 映射。

## 3.3 文件责任矩阵（谁写谁读）

## A. 责任角色定义

1. `Human`：开发者人工维护
2. `Skill`：会话编排层（仅写决策输入与触发动作）
3. `Runner`：流程执行器（状态、产物、日志）
4. `StageExecutor`：阶段产物生成器（当前由 Runner 内置或后续拆分）

## B. 文件责任表

1. `codex_devflow_scaffold/workflow_spec.json`
- 写：`Human`
- 读：`Skill/Runner/StageExecutor`
- 规则：运行中禁止自动改写

2. `codex_devflow_scaffold/state.json`
- 写：`Runner`
- 读：`Skill/Runner/StageExecutor`
- 规则：`Skill` 与 `Human` 不直接写

3. `codex_devflow_scaffold/semantic/business_semantic_model.json`
- 写：`Human/StageExecutor`
- 读：`Runner/StageExecutor`
- 规则：Stage 0 前必须存在且通过 schema 校验

4. `codex_devflow_scaffold/inputs/stageX.json`
- 写：`Human/Skill`
- 读：`Runner`
- 规则：仅作为阶段输入，不作为权威历史记录

5. `codex_devflow_scaffold/artifacts/stageX/latest.json`
- 写：`Runner/StageExecutor`
- 读：`Skill/Runner/StageExecutor`
- 规则：`latest.json` 只由流程覆盖写；人工修改需记录并触发 Stage 7

6. `codex_devflow_scaffold/schemas/*.json`
- 写：`Human`
- 读：`Runner/StageExecutor`
- 规则：运行中不自动改写

7. `codex_devflow_scaffold/prompts/stageX.md`
- 写：`Human`
- 读：`StageExecutor`
- 规则：修改后需触发下游重跑检查

8. `codex_devflow_scaffold/registry/api_registry.json`
- 写：`Stage 2/Stage 6（通过 Runner）`
- 读：`Stage 0/2/3/4/6`
- 规则：不得由 Skill 直接写业务内容

9. `codex_devflow_scaffold/registry/case_registry.json`
- 写：`Stage 4/Stage 6（通过 Runner）`
- 读：`Stage 4/5/6/7/8`
- 规则：映射关系必须与测试代码一致

10. `codex_devflow_scaffold/cases/case_descriptions.jsonl`
- 写：`Stage 4/Human`
- 读：`Stage 4/5/6`
- 规则：结构必须符合 `case_description.schema.json`

11. `codex_devflow_scaffold/decisions/pending.json`
- 写：`Runner`
- 读：`Skill/Human`
- 规则：只读，不手工改

12. `codex_devflow_scaffold/decisions/decision.json`
- 写：`Skill/Human`
- 读：`Runner`
- 规则：必须匹配 `pending.decision_id`

13. `codex_devflow_scaffold/logs/events.ndjson`
- 写：`Runner`
- 读：`Human/Skill`
- 规则：只追加，不覆盖

14. `codex_devflow_scaffold/docs/*.md`
- 写：`Stage 6/StageExecutor`（可由 Human 修订）
- 读：`Human/Skill/Runner`
- 规则：缺必选文档不允许收敛

15. `项目总体概览/*`
- 写：`Human`
- 读：`Skill/Runner/StageExecutor`
- 规则：对 Skill/Runner 只读；流程中禁止自动改写。

16. `业务侧实现/<entity>_data_dictionary.md`
- 写：`Stage 6/StageExecutor`（可由 Human 修订）
- 读：`Human/Skill/Runner/StageExecutor`
- 规则：涉及该实体字段/约束变化时必须同步更新。

17. `业务侧实现/<entity>_impl_desc.md`
- 写：`Stage 6/StageExecutor`（可由 Human 修订）
- 读：`Human/Skill/Runner/StageExecutor`
- 规则：涉及该实体 API 行为变化时必须同步更新。

18. `业务侧实现/<entity>_logical_model.md`
- 写：`Stage 6/StageExecutor`（可由 Human 修订）
- 读：`Human/Skill/Runner/StageExecutor`
- 规则：涉及该实体关系/状态机变化时必须同步更新。

19. `业务侧实现/<entity>_schema.dbml`
- 写：`Stage 6/StageExecutor`（可由 Human 修订）
- 读：`Human/Skill/Runner/StageExecutor`
- 规则：涉及该实体表结构变化时必须同步更新。

20. `权限管理侧实现/*.md` 与 `权限管理侧实现/*.dbml`
- 写：`Stage 6/StageExecutor`（可由 Human 修订）
- 读：`Human/Skill/Runner/StageExecutor`
- 规则：仅在权限矩阵/鉴权链路/角色边界变化时更新。

## C. 冲突处理规则

1. 若同一文件被非责任角色修改，Runner 在下一阶段标记 `ownership_conflict` 并进入 Stage 7。
2. `state.json/pending.json/events.ndjson` 视为系统文件，人工改动直接判定流程不可信，需人工确认后重跑。
3. 人工修改 `artifacts` 或 `prompts/schemas` 后，必须按变更传播规则重跑相关下游阶段。
4. `项目总体概览/*` 被流程自动改写时，直接判定流程违规并阻断到 Stage 7。

## 3.4 业务实体文档四件套约束

适用范围：`业务侧实现/` 下所有业务实体（如 `drone`、`route`、`mission`）。

命名规范：每个实体固定四个文件，实体名用 snake_case。
1. `<entity>_data_dictionary.md`
2. `<entity>_impl_desc.md`
3. `<entity>_logical_model.md`
4. `<entity>_schema.dbml`

强约束：
1. Stage 0 若新增实体，必须先声明四件套目标文件名，缺一项即阻断。
2. Stage 6 收敛时，凡本轮变更触达某实体，必须对该实体四件套完成一致性更新。
3. 若本轮仅改代码未改文档，Stage 6 失败并回退 Stage 1 补齐。
4. 若实体未发生变化，允许不改四件套，但必须在 `api_change_summary.md` 明确写“<entity> 四件套无变更”。

权限侧补充约束：
1. 涉及角色、权限码、scope、矩阵变化时，必须同步更新 `权限管理侧实现/` 中对应文档。
2. 若本轮未触达权限模型，允许不改权限文档，但必须在 `api_change_summary.md` 标注“权限侧无变更”。

## 3.5 最小 Schema 清单（开发前置）

为避免“规则只停留在文档”，下列 schema 作为第一批必须落地的机器校验规则。

1. `codex_devflow_scaffold/schemas/semantic_model.schema.json`
- 校验对象：`codex_devflow_scaffold/semantic/business_semantic_model.json`
- 用途：Stage 0 前语义完整性门禁
- 最小必填：`business_goal/roles/resources/actions/state_machines/constraints/permission_boundary`

2. `codex_devflow_scaffold/schemas/case_description.schema.json`
- 校验对象：`codex_devflow_scaffold/cases/case_descriptions.jsonl`（逐行校验）
- 用途：保证中文用例描述结构一致
- 最小必填：`case_id/event_id/api_refs/expected_business_code/status`

3. `codex_devflow_scaffold/schemas/case_registry_item.schema.json`
- 校验对象：`codex_devflow_scaffold/registry/case_registry.json` 的 `items[*]`
- 用途：保证 case 到测试代码映射可反查
- 最小必填：`case_id/test_file/test_name/event_id/api_refs/enabled`

4. `codex_devflow_scaffold/schemas/api_discovery_result.schema.json`
- 校验对象：Stage 2 产物
- 用途：保证“每轮 API 扫描与对账”结果完整
- 最小必填：`scanned_apis/existing_api_keys/new_api_keys/drift_items`

5. `codex_devflow_scaffold/schemas/stage4_test_plan.schema.json`
- 校验对象：Stage 4 产物
- 用途：保证测试计划与事件覆盖结果可落地
- 最小必填：`generated_cases/generated_test_files/coverage_by_event`

6. `codex_devflow_scaffold/schemas/stage5_test_report.schema.json`
- 校验对象：Stage 5 产物
- 用途：为 Stage 7/8 提供统一失败分布输入
- 最小必填：`executed_cases/failed_cases/regression_summary`

7. `codex_devflow_scaffold/schemas/pending_decision.schema.json`
- 校验对象：`codex_devflow_scaffold/decisions/pending.json`
- 用途：保证门禁暂停信息完整，避免决策上下文丢失
- 最小必填：`decision_id/stage/reason/allowed_actions/next_stage_on_approve/fallback_stage/created_at`

8. `codex_devflow_scaffold/schemas/decision_input.schema.json`
- 校验对象：`codex_devflow_scaffold/decisions/decision.json`
- 用途：保证用户决策输入合法，避免流程误跳转
- 最小必填：`decision_id/action`
- 条件必填：当 `action=goto_stage` 时，`next_stage` 必填
- 约束：`action` 必须属于当前 `pending.allowed_actions`

版本策略：
- 每个 schema 增加 `schema_version` 字段。
- 初期采用“最小骨架必填 + 可选扩展字段”策略，避免过早锁死结构。

## 3.6 测试脚手架详细规范

## A. 用例描述文件（`codex_devflow_scaffold/cases/case_descriptions.jsonl`）

- 存储格式：JSON Lines（每行一个 case 对象）。
- 编码：UTF-8。
- 每条记录字段：
1. `case_id`：全局唯一，建议格式 `CASE-<resource>-<3digit>`。
2. `title`：中文标题，描述业务意图。
3. `event_id`：来源 Stage 3 事件 ID。
4. `api_refs`：`METHOD PATH` 列表，按调用顺序排列。
5. `preconditions`：前置条件列表（账号角色、数据准备、状态要求）。
6. `steps`：步骤说明列表（可选，便于阅读）。
7. `expected_business_code`：期望业务状态码（来自 Stage 0 `biz_status_codes`）。
8. `expected_http_status`：期望 HTTP 状态码（用于测试断言）。
9. `tags`：标签列表（如 `smoke/regression/permission/negative`）。
10. `status`：`draft`/`approved`/`deprecated`。
11. `owner`：责任人（可选）。
12. `updated_at`：最后更新时间（ISO8601）。

- 约束：
1. `case_id` 不允许重复。
2. `api_refs` 至少 1 项。
3. `expected_business_code` 必须在本轮 Stage 0 状态码全集内。
4. `status=deprecated` 的 case 不参与 Stage 5 新增测试执行，但仍参与历史追踪。

## B. 用例映射注册表（`codex_devflow_scaffold/registry/case_registry.json`）

- 用途：建立“业务描述 -> 可执行测试”的反向索引。
- 每条映射字段：
1. `case_id`
2. `event_id`
3. `api_refs`
4. `test_file`
5. `test_name`
6. `test_type`（`api`/`integration`）
7. `enabled`（是否参与本轮执行）
8. `updated_at`

- 约束：
1. `case_id + test_name` 唯一。
2. `enabled=false` 时，Stage 5 不执行该条，但保留记录。
3. registry 与测试代码不一致时，Stage 5 直接失败并回退 Stage 1。

## C. 测试代码生成约定（Stage 4）

- 目标目录：沿用现有 Django 测试目录（如 `apps/<module>/tests.py` 或 `apps/<module>/tests/test_<resource>.py`）。
- 命名规则：
1. 测试函数名：`test_<resource>__<scenario>__<case_id_lower>`。
2. 测试函数必须带 `case_id` 常量或注释，便于追踪。
3. 每轮新增测试文件数量尽量最小，优先增量修改现有测试文件。

- 生成策略：
1. 仅围绕“本轮新增 API + 关联旧 API”生成用例。
2. 先生成用例描述，再生成测试代码，最后更新 `case_registry`。
3. 代码生成后，必须同步生成 `test_example_records.md` 摘要。

## D. 执行策略（Stage 5）

- 执行集合：
1. 本轮新增/更新测试（来自 Stage 4）。
2. 历史回归测试（`enabled=true` 且 `status!=deprecated`）。

- 失败处理：
1. 任一失败回退 Stage 1。
2. 连续失败超过阈值转 Stage 7。
3. 输出失败分布：`case_id -> 错误类型 -> 失败次数`，供 Stage 8 决策使用。

## E. 质量门禁

- Stage 4 完成门禁：
1. `case_descriptions.jsonl` 有新增或更新记录。
2. `case_registry.json` 与测试代码映射一致。
3. `test_example_records.md` 与本轮 case 变更一致。

- Stage 6 收敛门禁：
1. 用例描述、映射、测试代码三者可互相反查。
2. `api_change_summary.md` 中声明本轮测试覆盖范围与未覆盖风险。
3. 若权限矩阵无变更，明确写“权限矩阵无变更”。

## 4. 业务语义门禁

Stage 0 执行前必须生成或更新：
- `codex_devflow_scaffold/semantic/business_semantic_model.json`

最小必填字段：
1. `business_goal`
2. `roles`
3. `resources`
4. `actions`
5. `state_machines`
6. `constraints`
7. `permission_boundary`

门禁规则：
- 缺任一字段，Stage 0 输出 `semantic_gap_report` 并阻断。
- 阻断后只允许：
1. 补齐语义并重跑 Stage 0。
2. 人工豁免并记录审计日志。

## 5. 阶段契约

## Stage 0 API 与状态码种子

输入：业务语义模型 + API 注册表。
输出：
- 新增 API 候选（仅 1 个）。
- `biz_status_codes`。
- `required_case_codes`。
- 风险评估。
- commit message。
- `semantic_gap_report`（如有缺口）。

门禁：人工 review 后才能进入 Stage 1。

## Stage 1 代码实现

输入：Stage 0 产物 + 业务语义。
输出：实现任务、变更文件、实现说明。
规则：仅实现本轮新增 API，不做无关重构。

## Stage 2 API 扫描与差异

输入：Stage 0 候选 + API 注册表 + 运行中服务 URL。
输出：扫描 API、旧 API 集合、新 API 集合。

服务发现：
1. 优先读取 OpenAPI schema。
2. schema 不可用时，回退读取业务 API root `endpoints`。
3. 仍失败时记录原因并进入 Stage 7。

对账规则：
- 每轮必须重新抓取在线 API。
- 每轮必须执行“在线 API vs `api_registry`”对账。
- 存在不一致（新增/缺失/方法变化/路径变化）时，记录 API 漂移并先处理，再进入 Stage 3/4。

Bootstrap 规则（仅首次）：
- 条件：`api_registry` 为空且未标记 `bootstrap_done`。
- 行为：可跳过一致性校验，先将在线 API 全量写入注册表。
- 这些 API 全部标记为“新增 API”，后续必须完成 review、实现核验、测试补齐。
- 完成后写入 `bootstrap_done=true`，后续恢复每轮强制对账。

## Stage 3 业务事件组合

输入：Stage 2 差异 + 业务语义。
输出：
- 新 API 之间组合事件。
- 新 API 与旧 API 组合事件。
- 开发者编辑说明。

## Stage 4 调用流与测试生成

输入：Stage 0/1/2/3 + case 注册表。
输出：
- 角色视角调用流。
- 业务视角调用流。
- 测试用例与测试文件清单。

强约束：
- 覆盖码必须来自 Stage 0 `required_case_codes`。
- 测试示例按业务事件组合动态生成，不使用固定模板数量门槛。
- 若涉及权限接口，优先覆盖未登录、无角色、成功、参数非法场景。

## Stage 5 测试执行

输入：Stage 4 用例与历史回归集。
行为：执行测试命令并产出报告。

跳转：
- 成功 -> Stage 6。
- 失败 -> Stage 1。
- 连续失败超阈值 -> Stage 7。

## Stage 6 收敛与记录

输入：Stage 5 报告 + Stage 0/4 产物。
输出：
- 更新 API/Case 注册表。
- 更新 changelog。
- 生成收敛 commit 信息。
- 同步更新受影响实体的四件套文档（`业务侧实现/<entity>_*`）。
- 如涉及权限变化，同步更新 `权限管理侧实现/*`。

门禁：开发模式下需人工拍板后提交。

## Stage 7 人工干预门禁

输入：业务语义变更、阶段产物变更、失败上下文。
输出：重跑建议（回某 stage）或转 Stage 8。

## Stage 8 回退决策

输入：语义差异、稳定点、失败分布、影响面。
输出：
- `rollback` 或 `continue`。
- 决策依据。
- 执行动作（回退点或最小继续变更集）。

规则：核心语义冲突或系统性失效时默认倾向 `rollback`。

## 6. 决策协议

门禁触发后由 Runner 生成 `pending.json`。
Skill 仅请求最小动作：
- `approve`
- `reject`
- `goto_stage`
- `run_stage8`（仅 Stage 7）

用户决策写入 `decision.json` 后继续流程。

决策校验规则：
1. `pending.json` 必须通过 `pending_decision.schema.json`。
2. `decision.json` 必须通过 `decision_input.schema.json`。
3. `decision.decision_id` 必须与当前 `pending.decision_id` 一致。
4. `decision.action` 必须在 `pending.allowed_actions` 内。
5. `action=goto_stage` 时必须提供 `next_stage`。

## 7. 提交规范

- commit 标题格式：`stage-{n}(...): ...`
- Stage 0 每次新增 API 必须单独 commit。
- 回退动作必须记录 commit，不允许静默回退。
- 若 commit 规则变化，必须同步更新：
1. `codex_devflow_scaffold/README.md`
2. `codex_devflow_scaffold/CHANGELOG.md`

## 8. 每轮文档产出规范

必选文档（每轮必须更新，目录 `codex_devflow_scaffold/docs/`）：
1. `api_change_summary.md`
- 本轮 API 变化清单与原因。
- 明确“本轮仅新增 1 个 API”。

2. `api_catalog.md`
- API 角色、目标、请求、响应、业务状态码、失败场景。

3. `data_model_delta.md`
- 本轮数据表/字段/约束变化及业务含义。

4. `traceability_matrix.md`
- 业务需求 -> API -> 数据表 -> 权限 -> 测试 映射。

5. `test_example_records.md`
- 本轮新增/更新测试示例。
- 每条示例必须标注 `event_id` 与关联 API。
- 示例设计以“新 API + 旧 API 组合业务路径”为核心。

实现对齐文档（按实体变化强制）：
6. `业务侧实现/<entity>_data_dictionary.md`
7. `业务侧实现/<entity>_impl_desc.md`
8. `业务侧实现/<entity>_logical_model.md`
9. `业务侧实现/<entity>_schema.dbml`
- 若本轮触达该实体，以上 4 个文件必须完成一致性更新。
- 若未触达该实体，需在 `api_change_summary.md` 声明“无变更”。

按需文档（仅权限矩阵变更时生成）：
10. `permission_impact.md`
- API 与权限矩阵、scope、角色影响关系。
- 若未生成，需在 `api_change_summary.md` 标注“权限矩阵无变更”。

## 9. 实施优先级

## P0

1. 固化流程规格与 schema。
2. 跑通 `run-auto/resume/run-stage` 状态机。
3. 落实 Stage 0 语义门禁。
4. 落实 Stage 2 每轮 API 对账与 Bootstrap。
5. 落实 Stage 4 动态状态码约束。
6. 落实每轮文档产出门禁。
7. 落实 `pending.json/decision.json` schema 校验与条件字段校验。

## P1

1. Skill 触发词与交互标准化。
2. 决策模板生成优化。
3. 事件日志观测字段增强（耗时、失败类型、触发来源）。

## P2

1. Web 用例管理（case CRUD）。
2. Stage 8 自动分支与回退自动化。
3. 语义变更到下游重算图自动分析。

## 10. 验收标准（DoD）

1. Stage 0 可自动推进到门禁并暂停。
2. 任意门禁可通过 `decision.json` 恢复并继续。
3. Stage 4 可在不同 `required_case_codes` 下通过校验。
4. Stage 5 失败严格回 Stage 1，超阈值转 Stage 7。
5. Stage 6 可更新 registry/changelog，并遵守最终拍板门禁。
6. 关键动作全部写入 `events.ndjson`。
7. 缺少业务语义目录时，Stage 0/1/3/4/7/8 必然阻断。
8. 每轮都完成在线 API 与 `api_registry` 对账。
9. Bootstrap 导入的 API 必须作为新增项完成后续 review 与测试补齐。
10. Stage 0 语义缺口时不会产出伪 API。
11. 每轮必选文档 5 份齐全且可追踪。
12. `permission_impact.md` 仅在权限矩阵变更时强制生成。
13. `pending.json/decision.json` 均通过 schema 校验；非法 action 或缺失 `next_stage` 会被阻断。
14. `项目总体概览/` 在流程执行中保持只读，不发生自动改写。
15. 本轮触达实体均满足“四件套文档已同步”门禁。
