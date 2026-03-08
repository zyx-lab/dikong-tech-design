# mission 实现说明

- updated_at: 2026-03-08T08:24:00Z
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

## 当前轮增补
### 迭代 C：GET /api/v1/missions/{id}
- 业务目的：补齐“按 mission_id 精确读取任务详情”能力，供执行/取消等上层流程在动作前先读取任务当前快照。
- 设计边界：只读详情接口，不引入任务状态机写操作，保持接口单一职责和可组合性。
- 请求语义：路径参数 `id` 为任务主键；调用方需具备 `mission.view_mission` 权限。
- 响应语义：成功返回单条任务详情（字段与 `MissionReadSerializer` 一致），并携带 `business_code` 与 `business_detail_code`。
- 关键业务码：
  - SUCCESS / OK：详情读取成功
  - PERMISSION_DENIED / NOT_AUTHENTICATED 或 FORBIDDEN：未认证或无查看权限
  - RESOURCE_NOT_FOUND / NOT_FOUND：任务不存在

## 当前轮关键实现文件
- apps/mission/views.py
- apps/mission/serializers.py
- apps/mission/tests.py
- apps/mission/urls.py
- apps/api_v1/business_response.py

## 可追溯产物
- codex_devflow_scaffold/artifacts/stage0/latest.json
- codex_devflow_scaffold/artifacts/stage2/latest.json
- codex_devflow_scaffold/artifacts/stage4/latest.json
- codex_devflow_scaffold/artifacts/stage5/latest.json
