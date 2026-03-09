# waypoint 实现说明

- generated_at: 2026-03-09T02:39:31.679337Z

## 本轮实现目标
- 项目总体概览已定义 waypoints 为 routes 的核心子实体（1:N）。在已具备写入/查询/局部修正能力的基础上，本轮补齐删除能力，形成“创建 -> 查询 -> 修正 -> 删除”的最小闭环。

## API 行为范围
- 已有基础写入: POST /api/v1/waypoints
- 已有列表读取: GET /api/v1/waypoints
- 已有单条读取: GET /api/v1/waypoints/{id}
- 已有局部更新: PATCH /api/v1/waypoints/{id}
- 本轮聚焦删除: DELETE /api/v1/waypoints/{id}
- 行为边界: 仅提供单条删除，不承担批量删除、跨航线迁移、归档编排等动作。

## 关键业务码
- 已覆盖业务码: SUCCESS, INVALID_PARAMS, PERMISSION_DENIED, RESOURCE_NOT_FOUND, IDEMPOTENT_DUPLICATE
- 未覆盖目标码: STATE_CONFLICT

## DELETE /api/v1/waypoints/{id} 语义说明
- 业务价值: 允许调度系统清理无效或误建航点，避免脏数据影响后续任务规划。
- 路径参数: `id`（航点主键）。
- 请求体: N/A（按路径参数删除）。
- 约束规则: 删除后同步回写 route.waypoint_count，保证航线统计字段一致。
- 成功返回: `business_code=SUCCESS`，`business_detail_code=OK`，响应包含 `id` 与 `deleted=true`。
- 资源不存在: `business_code=RESOURCE_NOT_FOUND`，`business_detail_code=NOT_FOUND`。
- 权限失败: `business_code=PERMISSION_DENIED`，并返回细分权限错误码。

## 可追溯来源
- codex_devflow_scaffold/artifacts/stage0/latest.json
- codex_devflow_scaffold/artifacts/stage2/latest.json
- codex_devflow_scaffold/artifacts/stage4/latest.json
- codex_devflow_scaffold/artifacts/stage5/latest.json

## 关键实现文件
- apps/access/management/commands/seed_role_permissions.py
- apps/api_v1/urls.py
- config/settings.py
- apps/waypoint/
- apps/waypoint/models.py
- apps/waypoint/serializers.py
- apps/waypoint/views.py
- apps/waypoint/urls.py
- apps/waypoint/tests.py
