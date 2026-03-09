# mission 实现说明

- updated_at: 2026-03-09T09:30:00+08:00
- entity: mission

## 历史实现（保留）
### 迭代 A：POST /api/v1/missions
- 业务目的：创建任务主记录，建立任务与航线、无人机、飞手的执行关联。
- 设计边界：仅创建任务，不负责执行启动、暂停、取消、完成等状态流转编排。
- 关键业务码：
  - SUCCESS / OK：创建成功
  - INVALID_PARAMS / VALIDATION_ERROR：参数校验失败
  - PERMISSION_DENIED / NOT_AUTHENTICATED 或 FORBIDDEN：未认证或无创建权限

### 迭代 B：GET /api/v1/missions
- 业务目的：提供任务列表只读查询，供外部系统按条件组合筛选。
- 查询维度：`route_id`、`drone_id`、`pilot_id`、`status`。
- 设计边界：仅列表读取，不承担任何状态变更。
- 关键业务码：
  - SUCCESS / OK：查询成功，返回分页 `results`
  - PERMISSION_DENIED / NOT_AUTHENTICATED 或 FORBIDDEN：未认证或无查看权限

### 迭代 C：GET /api/v1/missions/{id}
- 业务目的：补齐“按 mission_id 精确读取任务详情”能力，供执行/取消等上层流程在动作前先读取任务当前快照。
- 设计边界：只读详情接口，不引入任务状态机写操作，保持接口单一职责和可组合性。
- 请求语义：路径参数 `id` 为任务主键；调用方需具备 `mission.view_mission` 权限。
- 响应语义：成功返回单条任务详情（字段与 `MissionReadSerializer` 一致），并携带 `business_code` 与 `business_detail_code`。
- 关键业务码：
  - SUCCESS / OK：详情读取成功
  - PERMISSION_DENIED / NOT_AUTHENTICATED 或 FORBIDDEN：未认证或无查看权限
  - RESOURCE_NOT_FOUND / NOT_FOUND：任务不存在

## 当前轮增补
### 迭代 D：PATCH /api/v1/missions/{id}
- 业务目的：提供 mission 的最小局部更新入口，让外部系统可基于“创建/读取 + 局部更新”组合业务流。
- 设计边界：
  - 仅支持局部更新，不做整对象替换；
  - 仅更新 `name`、`route`、`drone`、`pilot`、`scheduled_at`、`remark`；
  - 不承载任务状态流转，`status` 在该接口不可写。
- 权限要求：调用方需具备 `mission.manage_mission`。
- 响应语义：成功返回更新后的任务快照（`MissionReadSerializer` 字段集），并携带 `business_code` 与 `business_detail_code`。
- 关键业务码：
  - SUCCESS / OK：更新成功
  - INVALID_PARAMS / VALIDATION_ERROR：请求体包含不可写字段或违反业务校验
  - PERMISSION_DENIED / NOT_AUTHENTICATED 或 FORBIDDEN：未认证或无更新权限
  - RESOURCE_NOT_FOUND / NOT_FOUND：任务不存在

## 当前轮关键实现文件
- apps/mission/views.py
- apps/mission/serializers.py
- apps/mission/tests.py

## 可追溯产物
- codex_devflow_scaffold/artifacts/stage0/latest.json
- codex_devflow_scaffold/artifacts/stage2/latest.json
- codex_devflow_scaffold/artifacts/stage4/latest.json
- codex_devflow_scaffold/artifacts/stage5/latest.json

## 本轮增补（2026-03-09）
### 迭代 E：POST /api/v1/missions/{id}/cancel
- 业务目的：补齐任务的基础取消入口，让调度侧能够显式终止未完成任务，形成“创建 -> 查询 -> 编辑 -> 取消”的最小任务生命周期闭环。
- 设计边界：
  - 只处理单条 mission 的状态流转，不承担无人机回收、飞行记录补录、媒体归档或其他跨实体编排。
  - 请求体必须为空；取消动作的输入仅由路径参数 `id` 决定。
  - 仅允许取消 `PENDING`、`RUNNING`、`PAUSED` 任务；`COMPLETED`、`CANCELED`、`FAILED` 再次取消返回状态冲突。
- 权限要求：调用方需具备 `mission.manage_mission`。
- 响应语义：
  - SUCCESS / OK：取消成功，返回最新任务快照，且 `status=CANCELED`
  - INVALID_PARAMS / VALIDATION_ERROR：请求体非空
  - RESOURCE_NOT_FOUND / NOT_FOUND：任务不存在
  - STATE_CONFLICT / STATE_CONFLICT：当前状态不允许取消
  - PERMISSION_DENIED / NOT_AUTHENTICATED 或 FORBIDDEN：未认证或无取消权限
- 审计语义：
  - 成功取消后写入 `MISSION_CANCEL` 审计日志，保留 before/after 快照。
- 项目内回归沉淀：
  - `test_cancel_mission_should_return_success`
  - `test_cancel_mission_with_body_should_return_invalid_params`
  - `test_cancel_mission_state_conflict_should_return_state_conflict`
  - `test_cancel_mission_not_found_should_return_resource_not_found`
  - `test_cancel_mission_without_auth_should_return_permission_denied`
  - `test_cancel_mission_without_permission_should_return_permission_denied`

## 本轮增补（2026-03-09，迭代 7）
### 迭代 F：POST /api/v1/missions/{id}/start
- 业务目的：补齐任务进入执行中的基础入口，让调度侧能够把待执行任务显式启动，形成“创建 -> 查询 -> 编辑 -> 启动 -> 取消/完成”的状态流转基础。
- 设计边界：
  - 只处理单条 mission 的状态流转，不承担飞行记录创建、无人机回收、媒体归档或其他跨实体编排。
  - 请求体必须为空；启动动作的输入仅由路径参数 `id` 决定。
  - 仅允许 `PENDING -> RUNNING`。
  - 已处于 `RUNNING` 的任务重复 start 按幂等成功处理。
  - `PAUSED`、`COMPLETED`、`CANCELED`、`FAILED` 不允许 start，返回状态冲突。
- 权限要求：调用方需具备 `mission.manage_mission`。
- 响应语义：
  - SUCCESS / OK：启动成功，或重复启动执行中任务的幂等成功
  - INVALID_PARAMS / VALIDATION_ERROR：请求体非空
  - RESOURCE_NOT_FOUND / NOT_FOUND：任务不存在
  - STATE_CONFLICT / STATE_CONFLICT：当前状态不允许启动
  - PERMISSION_DENIED / NOT_AUTHENTICATED 或 FORBIDDEN：未认证或无启动权限
- 审计语义：
  - 成功启动后写入 `MISSION_START` 审计日志；幂等重复启动也保留审计轨迹。
- 项目内回归沉淀：
  - `test_start_mission_should_return_success`
  - `test_start_running_mission_should_be_idempotent_success`
  - `test_start_mission_with_body_should_return_invalid_params`
  - `test_start_mission_state_conflict_should_return_state_conflict`
  - `test_start_mission_not_found_should_return_resource_not_found`
  - `test_start_mission_without_auth_should_return_permission_denied`
  - `test_start_mission_without_permission_should_return_permission_denied`

## 本轮增补（2026-03-09，迭代 8）
### 迭代 G：POST /api/v1/missions/{id}/pause
- 业务目的：补齐任务挂起入口，让调度侧能够把执行中的任务显式置为已暂停，形成“创建 -> 启动 -> 暂停 -> 恢复/取消/完成”的状态流转基础。
- 设计边界：
  - 只处理单条 mission 的状态流转，不承担飞行记录创建、无人机回收、媒体归档或其他跨实体编排。
  - 请求体必须为空；暂停动作的输入仅由路径参数 `id` 决定。
  - 仅允许 `RUNNING -> PAUSED`。
  - 已处于 `PAUSED` 的任务重复 pause 按幂等成功处理。
  - `PENDING`、`COMPLETED`、`CANCELED`、`FAILED` 不允许 pause，返回状态冲突。
- 权限要求：调用方需具备 `mission.manage_mission`。
- 响应语义：
  - SUCCESS / OK：暂停成功，或重复暂停已暂停任务的幂等成功
  - INVALID_PARAMS / VALIDATION_ERROR：请求体非空
  - RESOURCE_NOT_FOUND / NOT_FOUND：任务不存在
  - STATE_CONFLICT / STATE_CONFLICT：当前状态不允许暂停
  - PERMISSION_DENIED / NOT_AUTHENTICATED 或 FORBIDDEN：未认证或无暂停权限
- 审计语义：
  - 成功暂停后写入 `MISSION_PAUSE` 审计日志；幂等重复暂停也保留审计轨迹。
- 项目内回归沉淀：
  - `test_pause_mission_should_return_success`
  - `test_pause_paused_mission_should_be_idempotent_success`
  - `test_pause_mission_with_body_should_return_invalid_params`
  - `test_pause_mission_state_conflict_should_return_state_conflict`
  - `test_pause_mission_not_found_should_return_resource_not_found`
  - `test_pause_mission_without_auth_should_return_permission_denied`
  - `test_pause_mission_without_permission_should_return_permission_denied`

## 本轮增补（2026-03-09，迭代 9）
### 迭代 H：POST /api/v1/missions/{id}/resume
- 业务目的：补齐任务恢复入口，让调度侧能够把已暂停任务显式恢复为执行中，形成“启动 -> 暂停 -> 恢复”的最小中断恢复闭环。
- 设计边界：
  - 只处理单条 mission 的状态流转，不承担飞行记录创建、无人机回收、媒体归档或其他跨实体编排。
  - 请求体必须为空；恢复动作的输入仅由路径参数 `id` 决定。
  - 仅允许 `PAUSED -> RUNNING`。
  - 已处于 `RUNNING` 的任务重复 resume 按幂等成功处理。
  - `PENDING`、`COMPLETED`、`CANCELED`、`FAILED` 不允许 resume，返回状态冲突。
- 权限要求：调用方需具备 `mission.manage_mission`。
- 响应语义：
  - SUCCESS / OK：恢复成功，或重复恢复执行中任务的幂等成功
  - INVALID_PARAMS / VALIDATION_ERROR：请求体非空
  - RESOURCE_NOT_FOUND / NOT_FOUND：任务不存在
  - STATE_CONFLICT / STATE_CONFLICT：当前状态不允许恢复
  - PERMISSION_DENIED / NOT_AUTHENTICATED 或 FORBIDDEN：未认证或无恢复权限
- 审计语义：
  - 成功恢复后写入 `MISSION_RESUME` 审计日志；幂等重复恢复也保留审计轨迹。
- 项目内回归沉淀：
  - `test_resume_mission_should_return_success`
  - `test_resume_running_mission_should_be_idempotent_success`
  - `test_resume_mission_with_body_should_return_invalid_params`
  - `test_resume_mission_state_conflict_should_return_state_conflict`
  - `test_resume_mission_not_found_should_return_resource_not_found`
  - `test_resume_mission_without_auth_should_return_permission_denied`
  - `test_resume_mission_without_permission_should_return_permission_denied`

<!-- stage6_doc_sync::mission::impl_desc.md::start -->
## Stage6 本轮同步
- 本轮 focus API: POST /api/v1/missions/{id}/complete
- 本轮实现目标: mission 现在已有创建、查询、局部更新、启动、暂停、恢复和取消，但仍缺少把执行中任务闭环为已完成的基础入口，状态机无法从 RUNNING 落到 COMPLETED。先补齐 complete，才能让任务生命周期具备最小完成闭环。
- 业务事件: EVT-001 完成任务
- 业务约束: N/A
- 测试沉淀: 生成用例数: 5, 已执行用例数: 5, 已沉淀到项目测试: 5, 待沉淀 case: N/A, 失败 case: N/A
- 关键文件: apps/mission/tests.py, apps/mission/views.py, apps/mission/models.py, apps/mission/serializers.py, apps/mission/urls.py
<!-- stage6_doc_sync::mission::impl_desc.md::end -->
