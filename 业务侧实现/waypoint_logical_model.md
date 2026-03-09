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
- 单条查询入口: GET /api/v1/waypoints/{id}
- 更新入口: PATCH /api/v1/waypoints/{id}
- 删除入口: DELETE /api/v1/waypoints/{id}

## 读取语义边界（GET /api/v1/waypoints/{id}）
- 读取模型: 基于主键返回单条航点记录。
- 输入参数: 路径参数 `id`。
- 异常语义: 不存在对象时返回 `RESOURCE_NOT_FOUND`。
- 权限边界: 仅具备 `view_waypoint` 权限的主体可读取；无权限时返回 `PERMISSION_DENIED`。

## 更新语义边界（PATCH /api/v1/waypoints/{id}）
- 更新模型: 仅允许局部更新 `sequence`、`latitude`、`longitude`、`altitude`。
- 输入参数: 路径参数 `id` + 至少 1 个可更新字段。
- 约束语义: route 绑定不可通过 PATCH 修改；同一路线下 sequence 必须唯一。
- 异常语义:
  - 字段非法/空请求体 -> `INVALID_PARAMS`
  - 目标不存在 -> `RESOURCE_NOT_FOUND`
  - 无认证或无权限 -> `PERMISSION_DENIED`

## 删除语义边界（DELETE /api/v1/waypoints/{id}）
- 删除模型: 按 `id` 删除单条 waypoint 记录。
- 输入参数: 路径参数 `id`。
- 约束语义: 删除成功后必须回写 route.waypoint_count，避免航线冗余计数字段漂移。
- 异常语义:
  - 目标不存在 -> `RESOURCE_NOT_FOUND`
  - 无认证或无权限 -> `PERMISSION_DENIED`
