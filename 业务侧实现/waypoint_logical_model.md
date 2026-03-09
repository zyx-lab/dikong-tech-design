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
- 更新入口: N/A
