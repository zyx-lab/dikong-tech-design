# Stage Model

## 目录

- 阶段编号
- 全局约束
- Stage0 提名API
- Stage1 实现API
- Stage2 业务事件
- Stage3 测试资产
- Stage4 回归
- Stage5 结算
- Stage6 干预
- Stage7 回退
- 门禁与输出

## 阶段编号

`INTRO` 是 skill 层的接入前阶段，不属于 runner 状态机。

它负责：

- 识别框架预设
- 初始化配置骨架
- 等用户完成配置
- 校验配置 ready
- 用 runner 的 `check-config` 审核 `overview/business` 语义源是否结构上可进入 bootstrap
- 由当前 Codex session 继续阅读 `overview/business` 和当前代码，完成真正的语义审核

只有 `INTRO` 完成后，runner 才进入逻辑阶段 `0-7`。

对外按逻辑阶段 `0-7` 使用。

- `run-stage`
- `project_rules.json -> stages`
- `gate` 与 `status`
- 用户决策里的 `goto-stage`

都使用逻辑阶段编号。

Runner 内部还存在一个额外子阶段，所以 `artifacts/stage*` 里会出现 `stage0..stage8`。这属于实现细节，正常使用时按逻辑阶段理解即可。

## 全局约束

Stage0 到 Stage4 共同遵守：

- 必须参考当前代码实现
- 必须参考当前 API 暴露结果
- 必须参考当前测试现状
- 业务语义优先来自 `overview`、`business` 和前序 stage 产物
- 禁止仅靠 path、HTTP method、代码目录反推业务实体或业务意图

## Stage0 提名API

目标：

- 选择本轮 focus API
- 为候选 API 显式确认 `entity`
- 为候选 API 显式确认本轮链路：`new_api` 或 `repair_api`

主要输入：

- `semantic.directories.overview`
- `semantic.directories.business`
- `cases/session_api_candidates.jsonl`
- 当前代码实现与当前 API 观测结果

主要输出：

- `api_candidates`
- `touched_entities`
- `required_case_codes`
- `iteration_delivery_mode`

补充说明：

- `new_api` 用于提名当前 registry 中还不存在的新增接口
- `repair_api` 用于提名“已经注册或已在代码中暴露、但需要按当前业务语义修正”的既有接口
- `repair_api` 不再因为 `already_registered` 被直接丢弃；runner 会把本轮状态标记为修复链路

阻断信号：

- 语义目录缺失
- 候选 API 为空
- 需要重规划

## Stage1 实现API

目标：

- 实现、修正、优化 API
- 确认 API 在系统中可观测

补充说明：

- 若 Stage0 进入 `repair_api` 链路，Stage1 的重点是修复既有路由的契约、权限、测试与文档一致性，而不是新增路由

主要动作：

- 改代码
- 补项目内测试
- 扫描 API
- 处理 registry 漂移

主要输出：

- 实现结果
- 最新 API 扫描结果

## Stage2 业务事件

目标：

- 从 focus API 收敛值得测试的业务事件

主要动作：

- 从 `cases/session_event_candidates.jsonl` 同步 `business_events`
- 对齐 `api_refs`
- 透传 Stage0 已确认的 `entity`
- 对齐 `expected_business_codes`

约束：

- 业务事件必须由当前 Codex session 结合当前实现生成
- 业务事件必须结合当前实现确认真实边界
- 优先沿用 Stage0 已显式声明的业务码
- runner 只做 traceability / gate 校验，不生成业务事件

## Stage3 测试资产

目标：

- 产出最小可执行测试资产

主要动作：

- 从 `cases/session_case_candidates.jsonl` 收敛测试用例
- 对齐 `case -> event -> api`
- 检查 generated tests 是否就绪

约束：

- case 设计必须对照当前代码和当前测试现状
- runner 不再自动补齐最小 case
- 没有有效 session case 时，Stage3 直接阻塞在 gate

主要输出：

- `candidate_cases`
- `generated_cases`
- `generated_test_files`

## Stage4 回归

目标：

- 基于真实测试结果形成回归结论

主要动作：

- 跑 generated tests
- 跑项目内测试
- 跑全量回归
- 校验 Stage3 用例是否已沉淀到项目内测试

主要输出：

- 回归结果
- 失败分布
- case 沉淀状态

## Stage5 结算

目标：

- 校验业务文档同步状态
- 同步 registry
- 生成 commit review

主要动作：

- 校验实体四件套是否与本轮代码变更保持一致
- 清理旧的机器同步痕迹，避免污染人工正文
- 生成提交审核信息

文档策略：

- 以 stage 产物里显式确认的 `entity` 和 `api_refs` 为主
- runner 不生成四件套正文，也不根据当前实现硬编码刷新文档内容
- 当前 session 必须读取现有文档与文档内更新指南，按语义自行维护正文
- `docs/` 下的摘要文档仍可重建为本轮简洁视图

说明：

- 旧的“前置提交审核”已经并入这个阶段

## Stage6 干预

目标：

- 主线失败时给出下一步修复入口

主要输出：

- 建议回到哪一阶段
- 是否进入回退阶段
- 干预建议摘要

## Stage7 回退

目标：

- 对异常轮次做回退决策收口

主要输出：

- `rollback` 或 `continue`
- 回退分支计划

对应命令：

- `stage8-rollback`
- 兼容别名 `stage7-rollback`

## 门禁与输出

默认流转：

```bash
python3 codex_skills/codex-tdd-devflow/scripts/workflow_runner.py run-auto
```

进入门禁后：

```bash
python3 codex_skills/codex-tdd-devflow/scripts/workflow_runner.py gate
python3 codex_skills/codex-tdd-devflow/scripts/workflow_runner.py decide --approve --apply
```

输出原则：

- 默认只看阶段摘要
- `status` / `gate` 里的 `config_readiness` 默认只看 brief；完整载荷用 `--json` 或 `check-config`
- 只有在需要完整原始载荷时才追加 `--json`
- 不把整包 artifact 倒给用户
- 每个阶段只输出与本阶段直接相关的信息
