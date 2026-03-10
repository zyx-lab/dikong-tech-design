# Onboarding

## 目录

- 产品目标
- 接入前检查
- 安装与初始化
- 三个配置文件
- Bootstrap 行为
- 运行时目录
- 会话输入文件
- 业务文档沉淀
- 项目规则

## 产品目标

这个 skill 面向“完全陌生团队”时，核心不是零配置，而是明确接入契约：

- 业务语义从哪里来
- 当前代码要如何参与各阶段判断
- API 如何被发现
- 测试如何执行
- 阶段门禁如何停下

如果这些前提被明确配置，这个 skill 可以从“只适配旧 Django”提升为“可迁移到陌生项目”。

## 接入前检查

接入前至少确认以下四件事：

1. 仓库里有稳定的 API 发现方式
2. 仓库里有可执行测试命令
3. 仓库里有业务语义目录，或准备补最小语义资料
4. 团队接受“门禁 + 决策文件 + 状态目录”的协作方式

如果这四件事里缺两件以上，不建议直接上主流程。

## 安装与初始化

推荐目录：

```text
your_project/
  codex_skills/
    codex-tdd-devflow/
```

初始化命令：

```bash
python3 codex_skills/codex-tdd-devflow/scripts/workflow_runner.py init
```

也可以直接选择模板预设：

```bash
python3 codex_skills/codex-tdd-devflow/scripts/workflow_runner.py detect-preset
python3 codex_skills/codex-tdd-devflow/scripts/workflow_runner.py init --preset generic
python3 codex_skills/codex-tdd-devflow/scripts/workflow_runner.py init --preset django
python3 codex_skills/codex-tdd-devflow/scripts/workflow_runner.py init --preset fastapi
```

可选参数：

- `--workspace /path/to/repo`: 当前目录不是仓库根目录时使用
- `--scaffold-dir your_scaffold_dir`: 想改默认运行时目录名时使用
- `--force`: 仅在你确认要覆盖已有 scaffold 文件时使用

初始化后，runner 会创建默认运行时目录 `codex_devflow_scaffold/`。

初始化出的配置文件是“中性模板”，不是 Django 示例。

如果你用了 `--preset django` 或 `--preset fastapi`，runner 会改为生成对应框架的起步模板。

特点：

- 必填项会写成 `__CODEX_DEVFLOW_REQUIRED_*__`
- `status` 与 `gate` 默认会输出压缩后的 `config_readiness` brief
- `check-config` 会返回配置校验 + `semantic_review` + `session_semantic_audit`
- `run-auto`、`run-stage`、`resume --auto` 会在主流程入口校验这些占位值是否已替换
- JSON 不支持真正注释，所以模板里的中文说明会放在 `_template` / `_comment` / `_examples` 字段里

补充说明：

- `config_readiness.status=ready` 只表示运行时配置和语义源结构检查通过，不表示可以跳过当前 session 的语义审核
- 默认 brief 会保留 `summary`、`next_step_hint`、阻断/警告计数和少量 preview
- 完整 `config_readiness` 原始载荷可通过 `status --json`、`gate --json` 或 `check-config` 查看
- `semantic_review` 是 runner 做的“语义源就绪检查”，只看目录、文件可读性、四件套覆盖情况
- `session_semantic_audit` 提醒当前 Codex session 继续阅读 `overview`、`business` 和当前代码，确认这些资料真的足以支撑 Stage0 提名和 entity 判断
- 所以 `check-config ready` 不等于“已经理解业务”；它只表示结构层面可以进入 INTRO 的最后人工语义确认

## 三个配置文件

### `config/static_controls.json`

这个文件描述“项目长什么样”。

重点字段分组：

- `semantic.directories`
- `project_layout.entry_files`
- `project_layout.code_directories`
- `project_layout.entity_code_roots`
- `project_layout.project_test_roots`
- `project_layout.permission_code_directories`
- `commit_controls.git_add_whitelist_patterns`
- `commit_controls.git_add_blacklist_prefixes`

常见改法：

- 把 `项目总体概览` / `业务侧实现` 改成你的语义目录名
- 把默认的 `apps/`、`config/` 改成你的代码目录
- 把默认测试目录改成你的项目测试目录

补充说明：

- `permission` 语义目录现在是可选项
- 显式空数组表示当前项目没有这类目录，不会再偷偷回退到 Django 默认值

### `workflow_spec.json`

这个文件描述“流程怎么跑”。

重点字段分组：

- `business_api.schema_url`
- `business_api.root_url`
- `business_api.local_scan_enabled`
- `business_api.local_scan_urlconf`
- `test_command`
- `generated_test_command`
- `project_internal_test_command`
- `gate_policy`

常见改法：

- 改 API schema 地址
- 改本地扫描方式
- 改 generated tests / 项目内测试 / 全量回归命令
- 改门禁策略

补充说明：

- 至少配置一种 API 发现方式：`schema_url`、`root_url`、或 `local_scan_command`
- `local_scan_urlconf` 只在继续走 Django 本地扫描时才需要

### `config/project_rules.json`

这个文件描述“团队还想补什么说明”。

它只做补充说明，不改变 stage 行为。

支持两种写法：

```json
{
  "schema_version": 1,
  "rules": [
    "将 skill 流程中产生的测试 case 沉淀到项目内",
    {
      "text": "Stage3 生成测试优先复用项目已有 fixture",
      "stages": [3]
    }
  ]
}
```

说明：

- 写字符串时，表示全局补充说明
- 写对象时，`stages` 使用对外逻辑阶段编号 `0-7`
- 修改这个文件后，runner 会按需最小化回刷受影响的 `inputs/stage*.json`

## Bootstrap 行为

这个 skill 的 bootstrap 是隐式的，不需要额外命令。

触发条件：

- `registry/api_registry.json` 为空或没有有效已注册 API
- `bootstrap_done` 为 `false`
- API 发现逻辑能看到已有接口

触发后：

- Stage0 会把当前已观测 API 视为首轮候选
- 不要求用户先手工写 `session_api_candidates.jsonl`
- Stage5 结算后会写回 registry，并把 `bootstrap_done` 置为 `true`

前提：

- 先通过 `check-config`
- 当前 Codex session 已完成一轮语义审核，确认 `overview/business` 与当前代码之间没有明显脱节到无法进入 Stage0

## 运行时目录

默认目录：

```text
codex_devflow_scaffold/
```

关键内容：

- `state.json`: 当前阶段、状态机位置、重规划标记
- `pending.json`: 当前门禁等待的决策
- `workflow_spec.json`
- `config/static_controls.json`
- `config/project_rules.json`
- `semantic/business_semantic_model.json`
- `artifacts/stage*/latest.json`
- `inputs/stage*.json`
- `cases/`
- `docs/`
- `registry/`
- `tests/generated/`
- `logs/events.ndjson`

这套目录是运行时资产，不是业务代码目录。

其中 `semantic/business_semantic_model.json` 现在只提供最小语义骨架，不再默认塞入示例业务。

## 会话输入文件

### `cases/session_api_candidates.jsonl`

用途：

- Stage0 候选 API 输入
- 默认由当前 Codex session 基于业务语义和当前代码生成

约束：

- 不是长期手工维护的项目配置
- 可以人工 override，但那是例外流程
- `entity` 应由业务语义理解得出，而不是路径规则推断

### `cases/session_case_candidates.jsonl`

用途：

- Stage3 / Stage4 case 输入
- 默认由当前 Codex session 生成和刷新
- 属于会话产物，不是人工维护配置

### `cases/session_entity_review.json`

用途：

- Stage5 结算前修正 touched entities
- 用于人工确认实体归属

## 业务文档沉淀

业务文档默认沉淀到：

```text
业务侧实现/
```

目录名可通过 `static_controls.json -> semantic.directories.business` 修改。

每个实体对应四件套：

- `{entity}_data_dictionary.md`
- `{entity}_impl_desc.md`
- `{entity}_logical_model.md`
- `{entity}_schema.dbml`

收口原则：

- runner 不会生成四件套正文
- 如果四件套已存在，runner 只做状态校验和旧机器块清理
- 文档正文由当前 session 读取现有文档和其中的更新指南后自行维护
- 重复执行 Stage5 不应持续制造无意义 diff

## 项目规则

`project_rules.json` 适合沉淀团队说明，例如：

- 测试 case 是否必须沉淀到项目内
- Stage3 是否优先复用 fixture
- Stage5 文档是否要附带某类业务约束说明

不适合写进 `project_rules.json` 的内容：

- 改 stage 顺序
- 改门禁推进逻辑
- 改实体识别算法

这些属于 workflow 行为，应改实现或 `workflow_spec.json`，不是补充规则。
