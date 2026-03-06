# TDD + AI 测试脚手架

## 入口

- 命令入口：`scripts/tddai_flow.py`
- 建议解释器：`.venv/bin/python`

## 技能流

- `Seed`：基于业务语义 + 现有 API，生成“应开发 API”候选与业务状态码方案。
- `Skill 0`：API / 测试 case 变更记录员（changelog + commit log）。
- `Skill 1`：API 盘点与差异（新增/变更/删除）。
- `Skill 2`：业务事件生成（新 API 互组合、新旧 API 组合）。
- `Skill 3`：生成调用流（角色视角 + 业务视角，含状态码与参数）。
- `Skill 4`：生成测试 case 并执行回归历史用例。
- `Skill 5`：记录新增测试事件、测试用例与变更摘要。
- `Skill 6`：语义变更/新需求时做 rollback vs continue 决策。

## 权限接口约束（用于生成 case）

权限接口生成 case 时只允许：
- 创建用户
- 给用户赋予业务侧角色

并且在 case 生成阶段必须覆盖：
- `401` 未登录/无 token
- `403` 已登录但无角色权限
- 成功路径（通常 `200`）
- 参数非法失败（`4xx`）

## 运行模式

- `development`：提交状态默认需要开发者拍板（`--approved-by`）。
- `search`：允许探索式开发，可直接 commit 状态，但必须写清 commit log 与记录责任技能。

## Commit 流程变更记录规则

当 commit 流程本身发生变化时（例如门禁规则、模式权限、日志字段、责任技能归属）：
- 必须同步更新：`note.md` 与本文件（`tests_scaffold/README.md`）。
- 必须追加一条记录到：`tests_scaffold/CHANGELOG.md`。
- 记录需包含：变更原因、影响范围、回退点（如有）、记录责任技能。

## 常用命令

```bash
# 1) 初始化
.venv/bin/python scripts/tddai_flow.py init

# 2) 切换模式
.venv/bin/python scripts/tddai_flow.py mode --set search
.venv/bin/python scripts/tddai_flow.py mode --set development

# 3) 跑一轮完整流水线（含回归测试）
.venv/bin/python scripts/tddai_flow.py pipeline --semantic-file note.md --run-tests --note "pipeline run" --recorder-skill skill0

# 4) 仅做决策（Skill 6）
.venv/bin/python scripts/tddai_flow.py decide --semantic-changed --new-feature

# 5) 记录状态提交（不真正 git commit，仅写日志）
.venv/bin/python scripts/tddai_flow.py commit-state --message "search iteration" --recorder-skill skill0

# 6) 真正 git commit（按需）
.venv/bin/python scripts/tddai_flow.py commit-state --message "search iteration" --recorder-skill skill0 --git
```

## 产物目录

- 运行态：`.tdd_ai/state.json`
- 规则：`.tdd_ai/config.json`
- 产物：`.tdd_ai/artifacts/*/latest.json`
- 提交日志：`.tdd_ai/commit_logs/*.md`
- 生成测试：`tests_scaffold/generated/test_generated_specs.py`
- 变更记录：`tests_scaffold/CHANGELOG.md`
