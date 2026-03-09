# route 逻辑模型

- updated_at: 2026-03-08T07:52:00Z

## 实体主表
- table: routes
- 主键: id (BigAutoField)
- 排序规则: `ordering = ['-id']`

## 状态机
- RouteStatus:
  - DISABLED(0): 禁用
  - ACTIVE(1): 正常
- RouteType:
  - PENDING_EXTENSION(0): 待扩展（语义待后续业务明确）

## 关系与约束
- 当前模型无显式 ForeignKey（`drone_type_id` 为外部 ID 引用位）
- 唯一约束: N/A

## 生命周期入口
- 创建入口（历史）：POST /api/v1/routes
- 查询入口（历史增补）：GET /api/v1/routes
- 详情入口（历史增补）：GET /api/v1/routes/{id}
- 更新入口（当前轮增补）：PATCH /api/v1/routes/{id}
- 删除入口（历史增补）：DELETE /api/v1/routes/{id}

## 当前轮业务语义
- `PATCH /api/v1/routes/{id}` 仅允许局部更新 route 主记录元数据，不承担航点重排、任务解绑、状态流转或删除恢复。
- 成功路径：返回 route 最新快照，业务码 `SUCCESS`。
- 失败路径：
  - 无权限：`PERMISSION_DENIED`
  - 目标 route 不存在：`RESOURCE_NOT_FOUND`
  - 请求体为空或包含不可写字段：`INVALID_PARAMS`

## 更新语义边界（PATCH /api/v1/routes/{id}）
- PATCH 请求体必须至少包含一个可写字段。
- 仅允许修改 `name`、`route_type`、`drone_type_id`、`total_distance`、`estimated_duration`、`waypoint_count`。
- `status`、`creator_name` 等生命周期/审计字段不可通过 PATCH 改写。
- 更新接口只处理 route 自身台账元数据，不承担航点编排或任务侧联动。

## 删除语义边界（DELETE /api/v1/routes/{id}）
- DELETE 请求体必须为空；若携带 body，返回 `INVALID_PARAMS`。
- 若 route 已被 mission 引用，则保留主记录并将 `status` 置为 `DISABLED(0)`。
- 若 route 未被 mission 引用，则物理删除 route 主记录，并同步删除其下属 waypoints。
- 删除接口只处理 route 自身生命周期，不承担任务解绑或跨实体编排。

<!-- stage6_doc_sync::route::logical_model.md::start -->
## Stage6 本轮同步
- 业务目标: N/A
- 业务动作: N/A
- 状态机: N/A
- 业务约束: N/A
- 事件闭环: EVT-001->POST /api/v1/routes/{id}/enable
- 权限边界: 代码权限码: view_route (可查看航线), manage_route (可新增与编辑航线)
<!-- stage6_doc_sync::route::logical_model.md::end -->
