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
