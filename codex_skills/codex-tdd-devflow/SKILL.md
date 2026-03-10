---
name: codex-tdd-devflow
description: 用状态机推进 API 增量开发、旧项目纳管与陌生团队接入：初始化运行时目录，读取业务语义与当前代码，推进提名、实现、事件、测试、回归、结算、干预与回退。
---

# Codex TDD Devflow

把“一轮 API 开发”变成可恢复、可审计、低噪音的状态机流程。

## 何时使用

- 用户要在项目里跑一轮完整的 API 开发流程，而不是只改几行代码
- 用户要把旧项目纳入可恢复的 API 开发状态机
- 用户要在陌生项目里建立“业务语义 + 当前代码 + 测试结果”三者对齐的门禁流程
- 用户明确提到 `codex-tdd-devflow`

## 先读哪里

- 首次接入、迁移到新项目、或配置不确定时，读 [references/onboarding.md](references/onboarding.md)
- 需要确认阶段职责、门禁、输入输出时，读 [references/stage-model.md](references/stage-model.md)

## 核心原则

- 保留状态机，优先使用 `run-auto` 推进到当前门禁
- `INTRO` 是 skill 协作阶段，不进入 runner 的 0-7 逻辑阶段
- Stage0 到 Stage4 都必须参考当前代码实现、当前 API 暴露和当前测试现状
- 业务语义优先来自 `overview`、`business` 和前序 stage 产物，不靠 path、HTTP method、目录名猜实体
- 输出默认只保留本阶段最小摘要；只有在需要原始载荷时才追加 `--json`
- `project_rules.json` 只补充项目说明，不改变 stage 功能

## INTRO

首次在陌生项目里使用这个 skill，先走 `INTRO`，再进入 bootstrap / Stage0。

`INTRO` 的固定顺序：

1. 运行 `detect-preset` 识别项目更像 `django`、`fastapi` 还是 `generic`
2. 向用户确认是否按该预设初始化
3. 若用户确认，执行 `init --preset xxx`
4. 给用户简洁配置说明，只说明要改哪几个文件
5. 等用户回复“配置完了”
6. 运行 `check-config`，读取其中的 `semantic_review`
7. 当前 Codex session 必须继续阅读 `overview`、`business` 和当前代码，做一轮语义审核
8. 只有 `check-config.status == ready`、`semantic_review.status == ready`，且当前 session 语义审核通过，才进入 `run-auto`，让 runner 自己决定是否 bootstrap
9. 若未 ready，只返回阻断项，不提前进入状态机

要求：

- `INTRO` 不做业务开发
- `INTRO` 不绕过用户确认直接写死预设
- `INTRO` 结束条件是“配置 ready”，不是“已经开始 Stage0”
- runner 的 `semantic_review` 只做语义源就绪检查，不代替当前 session 的业务语义理解

## 产品契约

Runner 负责：

- 初始化运行时目录
- 推进阶段
- 记录状态、门禁、产物、决策
- 在需要人工判断的地方停下

当前 Codex session 负责：

- 读取业务语义
- 对照当前代码理解现状
- 提名 API、实现 API、收敛事件和测试
- 按业务文档自身的固定格式和“更新本文档的指南（大模型用）”维护文档正文
- 给出门禁阶段的建议动作

额外约束：

- 不要把业务文档格式、章节标题、实体名、API 路径、审计动作等写死在 runner 代码里
- runner 只负责状态、门禁、产物记录和旧机器痕迹清理，不负责生成业务文档正文
- 文档更新如何做，必须由当前 session 读取文档现状、理解语义后自行编辑

用户负责：

- 提供或确认业务语义资料
- 审核并调整项目配置
- 在门禁处做最终决策
- 审核 Stage5 的提交建议

## 支持边界

### 直接可用

- Python Web API 项目
- 可以发现当前 API 的项目
- 可以执行项目测试命令的项目
- 有业务语义资料，或者愿意先补最小业务语义资料的项目

### 配置后可用

- 不是 Django/DRF 默认布局的 Python 项目
- 语义目录名、API 扫描方式、测试命令与默认值不同的项目
- 需要用 `project_rules.json` 添加团队约束说明的项目

### 不适合直接使用

- 没有可用 API 发现方式
- 没有可执行测试命令
- 没有业务语义资料，且也不准备补
- 期望 skill 仅靠代码扫描自动理解业务实体与业务意图

## 最小接入步骤

1. 把 skill 放到仓库根目录下的 `codex_skills/codex-tdd-devflow/`
2. 在仓库根目录执行：

```bash
python3 codex_skills/codex-tdd-devflow/scripts/workflow_runner.py init
```

如果你希望直接生成框架预设模板：

```bash
python3 codex_skills/codex-tdd-devflow/scripts/workflow_runner.py detect-preset
python3 codex_skills/codex-tdd-devflow/scripts/workflow_runner.py init --preset django
python3 codex_skills/codex-tdd-devflow/scripts/workflow_runner.py init --preset fastapi
```

3. 检查并修改：

- `<scaffold-dir>/config/static_controls.json`
- `<scaffold-dir>/workflow_spec.json`
- `<scaffold-dir>/config/project_rules.json`

这些文件现在会以模板初始化，包含 `__CODEX_DEVFLOW_REQUIRED_*__` 占位值。

- `--preset generic`: 中性模板
- `--preset django`: 预填 `manage.py/apps/config`、Django 测试命令、drf-spectacular 本地扫描默认值
- `--preset fastapi`: 预填常见 `app/tests` 布局，但 API 导出和测试命令通常仍需项目自行补齐

4. 运行到第一个门禁：

```bash
python3 codex_skills/codex-tdd-devflow/scripts/workflow_runner.py run-auto
```

5. 查看门禁并决策：

```bash
python3 codex_skills/codex-tdd-devflow/scripts/workflow_runner.py gate
python3 codex_skills/codex-tdd-devflow/scripts/workflow_runner.py decide --approve --apply
```

说明：

- 默认运行时目录是 `codex_devflow_scaffold/`
- 如果当前目录不是仓库根目录，追加 `--workspace /path/to/repo`
- 如果想改运行时目录名，追加 `--scaffold-dir your_scaffold_dir`
- `status` / `gate` 默认返回压缩后的 `config_readiness` brief；其中 `ready` 只表示结构 ready，不表示已经完成语义审核
- `check-config` 会额外返回 `semantic_review` 和 `session_semantic_audit`，前者是 runner 的语义源就绪检查，后者提示当前 session 继续做业务语义审核
- 需要完整 `config_readiness` 原始载荷时，使用 `status --json`、`gate --json` 或 `check-config`

## Bootstrap

这个 skill 有隐式 `bootstrap` 机制。

满足以下条件时，首次运行会自动进入首轮纳管模式：

- `api_registry` 为空
- `bootstrap_done` 为 `false`
- 当前项目能扫描出已有 API

此时 runner 会把“当前已观测到的 API”作为首轮候选，而不是要求先手写候选 API。

## 关键运行时资产

- `codex_devflow_scaffold/`: 状态、产物、门禁、决策、日志
- `cases/session_api_candidates.jsonl`: Stage0 候选 API，会话级输入，默认由当前 Codex session 生成
- `cases/session_case_candidates.jsonl`: Stage3/Stage4 用例输入，会话产物，默认由当前 Codex session 生成
- `cases/session_entity_review.json`: Stage5 结算前调整 touched entities 的会话输入
- `业务侧实现/`: 业务文档沉淀目录；每个实体对应四件套
- `semantic/business_semantic_model.json`: 最小语义骨架，不再写死示例业务

详细结构和配置字段见 [references/onboarding.md](references/onboarding.md)。

## 阶段总览

- Stage0 `提名API`: 基于业务语义和当前代码提名本轮 focus API，并显式确认 `entity`
- Stage1 `实现API`: 实现、修正、优化 API，并确认它在系统中可观测
- Stage2 `业务事件`: 从 focus API 收敛最小业务事件
- Stage3 `测试资产`: 生成或收敛最小可执行测试资产
- Stage4 `回归`: 运行 generated tests、项目内测试和全量回归
- Stage5 `结算`: 校验业务文档同步状态、同步 registry 和 commit review
- Stage6 `干预`: 主线失败时给出修复入口
- Stage7 `回退`: 形成回退决策或回退分支计划

完整阶段规则见 [references/stage-model.md](references/stage-model.md)。

## 常用命令

```bash
python3 codex_skills/codex-tdd-devflow/scripts/workflow_runner.py init
python3 codex_skills/codex-tdd-devflow/scripts/workflow_runner.py detect-preset
python3 codex_skills/codex-tdd-devflow/scripts/workflow_runner.py check-config
python3 codex_skills/codex-tdd-devflow/scripts/workflow_runner.py status
python3 codex_skills/codex-tdd-devflow/scripts/workflow_runner.py run-auto
python3 codex_skills/codex-tdd-devflow/scripts/workflow_runner.py gate
python3 codex_skills/codex-tdd-devflow/scripts/workflow_runner.py decide --approve --apply
python3 codex_skills/codex-tdd-devflow/scripts/workflow_runner.py decide --goto-stage 1 --reason "retry implementation" --apply
python3 codex_skills/codex-tdd-devflow/scripts/workflow_runner.py stage3-semantic --keep-all --reason "event review"
python3 codex_skills/codex-tdd-devflow/scripts/workflow_runner.py stage4-semantic --keep-all --reason "case review"
python3 codex_skills/codex-tdd-devflow/scripts/workflow_runner.py stage8-rollback
```

## 推荐提示词

首轮接入陌生项目：

```text
使用 codex-tdd-devflow。先不要假设当前项目是 Django。
先走 INTRO：detect-preset，告诉我识别结果并让我确认；确认后再 init --preset xxx。
然后告诉我该改哪些配置文件。等我说“配置完了”之后，你再 check-config，并结合 overview/business 与当前代码做语义审核；
只有 config ready 且语义审核通过之后，才进入 run-auto 到第一个 gate，只给我简洁阶段摘要。
```

正常推进一轮开发：

```text
使用 codex-tdd-devflow。
读取业务语义和当前代码实现，启动一轮流程，停在当前 gate，只输出本阶段摘要、风险和建议动作。
```

## 工作方式

- 默认先看 `status` 或直接 `run-auto`
- 默认优先自动收口可自动收口的问题
- 默认不输出整包 artifact
- 进入门禁后先看 `gate`
- 优先让当前 Codex session 刷新 session 产物，不把 session 文件当人工维护配置
