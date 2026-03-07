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

1. 查看 `codex_devflow_scaffold/decisions/pending.json`
2. 编辑 `codex_devflow_scaffold/decisions/decision.json`
3. 执行 `python codex_skills/codex-tdd-devflow/scripts/workflow_runner.py resume --auto`

标准化快捷方式（推荐）：

```bash
python codex_skills/codex-tdd-devflow/scripts/workflow_runner.py gate
python codex_skills/codex-tdd-devflow/scripts/workflow_runner.py decide --approve --apply --auto
python codex_skills/codex-tdd-devflow/scripts/workflow_runner.py decide --reject --apply
python codex_skills/codex-tdd-devflow/scripts/workflow_runner.py decide --goto-stage 1 --reason "retry stage1" --apply --auto
python codex_skills/codex-tdd-devflow/scripts/workflow_runner.py decide --run-stage8 --apply --auto
```

## P2 能力

- `case-web`：提供测试用例 Web CRUD（列表/创建/编辑/删除）。
- `recalc-analyze`：分析语义/产物/配置变更对下游 Stage 的影响，输出建议重跑起点。
- `stage8-rollback`：从 Stage8 产物自动生成回退分支（支持 dry-run / apply）。
- Stage2 API 扫描支持离线 fallback：在线 schema/root 不可用时，自动调用本地 `manage.py spectacular` 扫描（免启动服务）。

## 约束

- 工作流系统文件由 Runner 维护：`state.json`、`pending.json`、`events.ndjson`。
- Stage 0 语义门禁未通过时，不推进到 Stage 1。
- Stage 4 测试覆盖码来源于 Stage 0 的 `required_case_codes`。
- Stage 6 会校验“触达实体四件套”与权限文档影响；未通过时回退到 Stage 1。
- 离线扫描可通过 `workflow_spec.business_api.local_scan_command` 覆盖默认命令。
