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

1. 执行 `python codex_skills/codex-tdd-devflow/scripts/workflow_runner.py gate`（会直接输出可读摘要）
2. 若是 Stage3 门禁，先执行 `python codex_skills/codex-tdd-devflow/scripts/workflow_runner.py stage3-semantic ...` 完成业务事件语义筛选
3. 若是 Stage4 门禁，先执行 `python codex_skills/codex-tdd-devflow/scripts/workflow_runner.py stage4-semantic ...` 完成测试用例语义筛选
4. Stage4 语义筛选后，由当前 Codex session 按 `generated_cases` 生成/更新测试代码到 `generated_test_files`（Runner 不代写）
5. 基于摘要做人审决策（approve/reject/goto_stage/run_stage8）
6. 执行 `python codex_skills/codex-tdd-devflow/scripts/workflow_runner.py decide --approve --apply`（默认不跨门禁自动续跑）

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
- Stage4 会基于 Stage3 已确认业务事件生成 `candidate_cases`，并进入语义筛选门禁；由当前 Codex session 通过 `stage4-semantic` 选择保留用例。
- Stage4 的测试代码必须由当前 Codex session 生成并写入 `codex_devflow_scaffold/tests/generated/test_generated_specs.py`，Runner 只做结构校验和执行。
- Stage4 同步产出中文业务事件说明（`codex_devflow_scaffold/cases/business_events_zh.md`）。

## 约束

- 工作流系统文件由 Runner 维护：`state.json`、`pending.json`、`events.ndjson`。
- Stage 0 语义门禁未通过时，不推进到 Stage 1。
- Stage 4 测试覆盖码来源于 Stage 0 的 `required_case_codes`。
- Stage 6 会校验“触达实体四件套”与权限文档影响；未通过时回退到 Stage 1。
- 离线扫描可通过 `workflow_spec.business_api.local_scan_command` 覆盖默认命令。
- 默认 `approval_mode=manual`：`--auto` 续跑需显式追加 `--allow-auto-continue`。
- Stage 0 候选会跳过 `api_registry` 已存在的 `api_key`，避免重复提名同一接口。
- Stage3 approve 前必须先完成业务事件语义筛选确认（`semantic_review.status=approved`）。
- Stage4 approve 前必须先完成语义筛选确认（`semantic_review.status=approved`）。
- Stage4 approve 时会校验 `generated_test_files` 是否覆盖全部 `expected_tests`；缺失时不允许进入 Stage5。
- Stage 5 会先校验 Stage4 生成测试的“预期数量/命名/执行结果”一致性，再汇总全量回归结果。
- Stage 0 若本轮无可推进候选，会进入 `stage0_no_candidate_blocking` 门禁（仅允许 `reject/goto_stage`）。
- Bootstrap 轮次允许按扫描到的既有 API 批量初始化校验，不受“每轮新增 1 个 API”限制。
- Stage4 生成测试时优先校验业务状态码字段（默认候选：`business_code/biz_code/code/status_code`），HTTP 状态码仅作辅助校验。
