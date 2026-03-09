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
