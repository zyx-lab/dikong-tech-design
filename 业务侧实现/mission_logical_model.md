# mission 逻辑模型

- updated_at: 2026-03-09T09:30:00+08:00

## 实体主表
- table: missions
- 主键: id (BigAutoField)
- 排序规则: `ordering = ['-id']`

## 状态机
- MissionStatus:
  - PENDING(0): 待执行
  - RUNNING(1): 执行中
  - PAUSED(2): 已暂停
  - COMPLETED(3): 已完成
  - CANCELED(4): 已取消
  - FAILED(5): 执行失败

## 关系与约束
- route -> route.Route（FK, PROTECT）
- drone -> drone.Drone（FK, PROTECT）
- pilot -> access.StaffProfile（FK, PROTECT）
- 创建任务时需满足：
  - route.status = ACTIVE
  - drone.status = ENABLED
  - pilot.employment_status = ACTIVE 且 staff_type.code = `pilot_operator`

## 生命周期入口
- 创建入口（历史）：POST /api/v1/missions
- 查询入口（历史增补）：GET /api/v1/missions
- 详情入口（历史增补）：GET /api/v1/missions/{id}
- 更新入口（当前轮增补）：PATCH /api/v1/missions/{id}
- 取消入口（本轮增补）：POST /api/v1/missions/{id}/cancel
- 删除入口：N/A

## 当前轮业务语义
- `PATCH /api/v1/missions/{id}` 仅做 mission 局部字段更新，不做任务状态流转。
- 可更新字段：`name`, `route`, `drone`, `pilot`, `scheduled_at`, `remark`。
- 不可更新字段：`status` 以及未声明字段（返回 `INVALID_PARAMS`）。
- 成功路径：`SUCCESS / OK`，返回更新后的任务快照。
- 失败路径：
  - 无权限：`PERMISSION_DENIED`
  - 参数非法：`INVALID_PARAMS`
  - 任务不存在：`RESOURCE_NOT_FOUND`

## 本轮增补（2026-03-09）
- 本轮聚焦 API: `POST /api/v1/missions/{id}/cancel`
- 状态流转语义：
  - 允许 `PENDING -> CANCELED`
  - 允许 `RUNNING -> CANCELED`
  - 允许 `PAUSED -> CANCELED`
  - `COMPLETED`、`CANCELED`、`FAILED` 不允许再取消，返回 `STATE_CONFLICT`
- 输入边界：
  - 请求体必须为空，不接受额外编排参数。
- 作用边界：
  - 仅变更 `missions.status`，不自动处理无人机分配回收、飞行记录补录或其他跨实体联动。
- 审计语义：
  - 成功取消后写入 `MISSION_CANCEL` 审计日志。

<!-- stage6_doc_sync::mission::logical_model.md::start -->
## Stage6 本轮同步
- 业务目标: N/A
- 业务动作: N/A
- 状态机: N/A
- 业务约束: N/A
- 事件闭环: EVT-001->POST /api/v1/missions/{id}/cancel
- 权限边界: 代码权限码: view_mission (可查看任务), manage_mission (可新增与编辑任务)
<!-- stage6_doc_sync::mission::logical_model.md::end -->
