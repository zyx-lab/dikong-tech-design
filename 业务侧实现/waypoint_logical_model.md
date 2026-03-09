# waypoint 逻辑模型

- generated_at: 2026-03-09T02:39:31.679337Z

## 实体主表
- table: waypoints
- 主键: id (BigAutoField)

## 状态机
- N/A

## 关系与约束
- route -> "route.Route"

## 生命周期入口
- 创建入口: POST /api/v1/waypoints
- 查询入口: GET /api/v1/waypoints
- 更新入口: N/A

## 读取语义边界（GET /api/v1/waypoints）
- 读取模型: 按 route 下的 sequence 有序返回航点列表。
- 过滤能力: 支持 `route_id`、`sequence` 精确过滤。
- 权限边界: 仅具备 `view_waypoint` 权限的主体可读取；无权限时返回 `PERMISSION_DENIED`。
