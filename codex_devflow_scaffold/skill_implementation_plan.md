# Codex TDD 通用工作流 Skill 实施规划（V2）

## 1. 文档定位

本文件定义一个可跨业务复用的 Skill 方案，用于实现：

- `workflow-core`（通用执行引擎）
- `workflow-profile-*`（业务配置包）

目标：同一套 Core 不改代码，通过替换 Profile 适配不同业务域（低空、电商、内容平台、SaaS 后台等）。

## 2. 当前状态（2026-03-06）

仓库已具备：

- Django 主工程（`apps/access`、`apps/api_v1`、`apps/drone`）
- `codex_devflow_scaffold/` 目录骨架
- 规划文档（本文件）

尚未落地：

- 通用 Runner 代码（`tools/workflow_runner.py`）
- Skill 包目录（`codex_skills/workflow-core/`、`codex_skills/workflow-profile-*`）
- 可执行的 schema/registry/state 初始化脚本

## 3. 通用化目标与非目标

### 3.1 目标

1. 流程引擎通用：Stage0-Stage8 不绑定任何业务术语。
2. 规则可配置：目录、命名、门禁、文档要求全部外置到 Profile。
3. 可插拔策略：Stage 候选排序、测试生成、回退阈值支持策略切换。
4. 可审计：每轮产物、决策、日志完整可追踪。
5. 可迁移：新业务接入只新增一个 Profile，Core 不改动。

### 3.2 非目标

1. 不追求“一份 Profile 适配所有公司”。
2. 不在 V2 首版内实现 UI 系统。
3. 不把业务细节硬编码回 Core。

## 4. 架构拆分（Core + Profile）

## 4.1 Core（通用层）

职责：

- 运行状态机（Stage0-Stage8）
- 统一输入输出契约校验
- 门禁机制与人工决策中断/恢复
- 日志与追踪 ID 管理
- 产物读写与生命周期管理

约束：

- 不出现具体业务目录名（如 `项目总体概览/`）
- 不出现具体实体命名规范（如四件套文件名）
- 不出现具体权限码（如 `drone.view_drone`）

## 4.2 Profile（业务层）

职责：

- 提供目录映射与读写边界
- 提供实体文档策略与命名模板
- 提供权限/角色模型约束
- 提供 Stage 提示词与策略参数
- 提供业务状态码与测试覆盖策略

约束：

- 只声明规则，不实现流程引擎
- 可扩展但需满足 Core 的 Profile Schema

## 4.3 实例关系

- 一个 Core 可绑定多个 Profile。
- 同一仓库可切换 Profile 做对比演练。
- Profile 可以继承公共基线（后续版本支持）。

## 5. 目录规划（目标态）

```text
codex_skills/
  workflow-core/
    SKILL.md
    scripts/
      runner.py
      validate_profile.py
      init_workspace.py
    references/
      contracts.md
      stage_hooks.md
      decision_protocol.md

  workflow-profile-dikong/
    SKILL.md
    profile.yaml
    prompts/
      stage0.md
      stage1.md
      stage2.md
      stage3.md
      stage4.md
      stage5.md
      stage6.md
      stage7.md
      stage8.md
    schemas/
      semantic_model.schema.json
      case_description.schema.json
      stage4_test_plan.schema.json
      stage5_test_report.schema.json
```

工作目录（运行时）：

```text
workflow_runtime/
  state.json
  decisions/
    pending.json
    decision.json
  artifacts/
    stage0/latest.json
    ...
    stage8/latest.json
  registry/
    api_registry.json
    case_registry.json
  logs/
    events.ndjson
  inputs/
    stage0.json
    ...
```

## 6. Profile Schema（关键）

`profile.yaml` 必须是 Core 唯一配置入口，至少包含：

```yaml
profile:
  name: dikong
  version: 1

paths:
  baseline_dirs: []
  business_dirs: []
  authz_dirs: []
  runtime_dir: workflow_runtime

io_policy:
  readonly_globs: []
  writable_globs: []

entity_docs:
  enabled: true
  templates:
    data_dictionary: "{entity}_data_dictionary.md"
    impl_desc: "{entity}_impl_desc.md"
    logical_model: "{entity}_logical_model.md"
    schema_dbml: "{entity}_schema.dbml"
  required_when_entity_touched: true

stage_policy:
  max_new_api_per_iteration: 1
  require_human_review_stages: [0, 7, 8]
  auto_resume: false

test_policy:
  case_description_file: workflow_runtime/cases/case_descriptions.jsonl
  must_cover_required_business_codes: true
  regress_history_cases: true

permission_policy:
  enabled: true
  update_docs_when_matrix_changed: true

docs_policy:
  required_docs:
    - api_change_summary.md
    - status_code_matrix.md
    - test_example_records.md
```

## 7. 阶段契约（通用版）

## Stage 0 语义与 API 候选

输入：

- Profile 配置
- 当前代码与 API 注册表
- 业务语义文档

输出：

- `api_candidates`
- `required_case_codes`
- `semantic_gap_report`

规则：

- 若语义缺口超阈值，阻断并进入待决策。
- 每轮候选数量受 `max_new_api_per_iteration` 控制。

## Stage 1 实现计划与代码变更

输入：Stage0 产物 + Profile 提示词。
输出：实现清单、变更文件清单、回归影响面。

## Stage 2 API 发现与对账

输入：运行中服务 + registry。
输出：`scanned_apis`、`new_api_keys`、`drift_items`。

## Stage 3 业务事件组合

输入：Stage0/2 产物。
输出：`business_events`（新 API 与存量 API 的组合流）。

## Stage 4 测试计划与代码生成

输入：Stage3 事件 + 测试策略。
输出：调用流、用例描述、测试代码映射。

强约束：

- 覆盖码必须来自 Stage0 的 `required_case_codes`。

## Stage 5 执行与报告

输入：Stage4 测试代码。
输出：通过率、失败分布、回退建议。

## Stage 6 收敛与文档更新

输入：Stage5 报告。
输出：registry 更新、文档更新、变更摘要。

规则：

- 是否强制实体文档/权限文档更新，由 Profile 决定。

## Stage 7 人工干预与重跑决策

输入：冲突、失败、人工修改。
输出：`continue` / `rerun_from_stage` / `rollback_prepare`。

## Stage 8 回退决策

输入：语义变更、失败成本、稳定点。
输出：`rollback` / `continue` + 执行动作。

## 8. 文件责任矩阵（通用抽象）

1. `workflow_runtime/state.json`
- 写：Runner
- 读：Runner/Skill

2. `workflow_runtime/artifacts/stageX/latest.json`
- 写：Runner
- 读：Runner/Skill/Human

3. `workflow_runtime/registry/*.json`
- 写：Runner（Stage2/Stage6）
- 读：Runner/Skill/Human

4. `workflow_runtime/decisions/pending.json`
- 写：Runner
- 读：Skill/Human

5. `workflow_runtime/decisions/decision.json`
- 写：Skill/Human
- 读：Runner

6. Profile 描述文件（`profile.yaml`、`prompts/*`、`schemas/*`）
- 写：Human
- 读：Runner/Skill

冲突处理：

- 系统文件被人工改写时，标记 `system_file_mutation` 并阻断到 Stage7。

## 9. 决策协议（通用）

`decision.json` 统一格式：

```json
{
  "decision_id": "uuid",
  "action": "continue|rerun_from_stage|rollback|abort",
  "next_stage": 0,
  "reason": "text",
  "approved_by": "human|skill",
  "timestamp": "ISO8601"
}
```

规则：

- `rerun_from_stage` 必须附带 `next_stage`。
- `rollback` 必须附带 `target_ref`（commit/tag）。

## 10. 低空项目 Profile 下沉策略

原规划中的低空定制规则，全部迁移到 `workflow-profile-dikong/profile.yaml` 与 `prompts/`：

1. 固定目录三分法（`项目总体概览/`、`业务侧实现/`、`权限管理侧实现/`）
2. 实体四件套命名与强制更新规则
3. 权限矩阵同步规则
4. 特定角色与状态码约束

Core 只保留“读取配置并执行”的机制。

## 10.1 `workflow-profile-dikong` 路径配置（针对当前项目）

以下是本仓库可直接使用的 `paths` 与 IO 边界示例（用于 `workflow-profile-dikong/profile.yaml`）：

```yaml
profile:
  name: dikong
  version: 1

paths:
  baseline_dirs:
    - 项目总体概览
  business_dirs:
    - 业务侧实现
  authz_dirs:
    - 权限管理侧实现
  runtime_dir: workflow_runtime

io_policy:
  readonly_globs:
    - 项目总体概览/**
  writable_globs:
    - 业务侧实现/**
    - 权限管理侧实现/**
    - workflow_runtime/**
```

说明：

1. `项目总体概览/**` 在运行时强制只读，对齐你之前的约束。
2. `业务侧实现/**` 与 `权限管理侧实现/**` 可写，用于实现态文档同步。
3. 运行期状态文件统一放 `workflow_runtime/**`，避免污染业务目录。

## 11. 实施路线

## P0（先可用）

1. 建立 `workflow-core` 的 Runner 最小实现。
2. 定义并校验 `profile.yaml` Schema。
3. 打通 Stage0/2/4/5/7 的主链路。
4. 完成 `workflow-profile-dikong` 首个 Profile。

## P1（可迁移）

1. 再实现一个非低空 Profile（建议电商订单域）。
2. 验证 Core 零代码改动可跑通两套 Profile。
3. 为 Stage 策略提供 hook（排序、门禁阈值、文档策略）。

## P2（工程化）

1. 增加 Profile 兼容性测试。
2. 增加迁移助手（从旧项目规则生成 profile 初稿）。
3. 增加可观测性字段（阶段耗时、失败类型、重跑原因）。

## 12. 验收标准（DoD）

1. Core 中无业务硬编码路径、实体名、权限码。
2. 至少两个业务 Profile 跑通 Stage0-Stage6。
3. 替换 Profile 后无需修改 Core 代码。
4. 所有阶段产物通过对应 schema 校验。
5. 决策链可追踪，支持中断恢复。
6. 文档更新门禁由 Profile 控制并可验证。

## 13. 风险与控制

1. 风险：Profile 设计过于复杂，导致接入成本高。
- 控制：Profile Schema 保持最小集，采用可选扩展字段。

2. 风险：Core 仍残留隐性业务假设。
- 控制：强制双 Profile 回归（低空 + 非低空）。

3. 风险：测试生成策略在不同业务下不稳定。
- 控制：Stage4 引入策略参数与覆盖阈值配置。

## 14. 里程碑交付物

1. `codex_skills/workflow-core/SKILL.md`
2. `codex_skills/workflow-core/scripts/runner.py`
3. `codex_skills/workflow-profile-dikong/profile.yaml`
4. `workflow_runtime/` 初始化脚本
5. 两套 Profile 的演示运行记录与产物样例
