# flight record 逻辑模型

- generated_at: 2026-03-08T09:24:39.176004Z

## 实体主表
- table: flight_records
- 主键: id (BigAutoField)

## 状态机
- N/A

## 关系与约束
- mission -> "mission.Mission"
- drone -> "drone.Drone"
- pilot -> "access.StaffProfile"
- 唯一约束: flight_no

## 生命周期入口
- 创建入口（历史增补）: POST /api/v1/flight-records
- 查询入口（历史增补）: GET /api/v1/flight-records
- 详情入口（历史增补）: GET /api/v1/flight-records/{id}
- 更新入口（当前轮增补）: PATCH /api/v1/flight-records/{id}

## 本轮增补（PATCH /api/v1/flight-records/{id}）
- 更新入口: PATCH /api/v1/flight-records/{id}
- 业务规则:
  - 仅允许局部更新单条 flight_record 主记录元数据；
  - PATCH 请求体必须至少包含一个可写字段；
  - 允许更新 `mission`、`drone`、`pilot`、`airport_name`、`start_time`、`end_time`、`flight_duration`、`photo_count`、`video_count`、`status` 等字段；
  - 不承担媒体文件编排、级联删除或跨实体状态流转。
- 业务码: `SUCCESS` / `INVALID_PARAMS` / `PERMISSION_DENIED` / `RESOURCE_NOT_FOUND`。

<!-- stage6_doc_sync::flight_record::logical_model.md::start -->
## Stage6 本轮同步
- 业务目标: N/A
- 业务动作: N/A
- 状态机: N/A
- 业务约束: N/A
- 事件闭环: EVT-001->POST /api/v1/flight-records/{id}/complete
- 权限边界: 代码权限码: view_flight_record (可查看飞行记录), manage_flight_record (可新增与编辑飞行记录)
<!-- stage6_doc_sync::flight_record::logical_model.md::end -->
