# flight record 数据字典

- generated_at: 2026-03-08T09:24:39.176004Z
- entity: flight_record

## 业务定位
- 历史 API: POST /api/v1/flight-records
- 历史增补 API: GET /api/v1/flight-records
- 历史增补 API: GET /api/v1/flight-records/{id}
- 当前轮增补 API: PATCH /api/v1/flight-records/{id}
- 业务目的: 当前 flight_record 已具备创建、列表与详情能力，但缺少基础编辑入口；补齐 PATCH 后，调用方可修正飞行结果元数据，形成最小可维护闭环。

## 字段定义（来自模型代码）
- id: type=BigAutoField; constraints=pk; verbose=ID
- flight_no: type=CharField; constraints=unique, max_length=50; verbose=架次编号
- mission: type=ForeignKey; constraints=nullable, blank; verbose=mission.Mission
- mission_name: type=CharField; constraints=blank, max_length=100, default=""; verbose=任务名称（冗余）
- route_name: type=CharField; constraints=blank, max_length=100, default=""; verbose=航线名称（冗余）
- airport_name: type=CharField; constraints=blank, max_length=100, default=""; verbose=执行机场名称
- drone: type=ForeignKey; constraints=nullable, blank; verbose=drone.Drone
- drone_name: type=CharField; constraints=blank, max_length=100, default=""; verbose=无人机名称（冗余）
- pilot: type=ForeignKey; constraints=nullable, blank; verbose=access.StaffProfile
- pilot_name: type=CharField; constraints=blank, max_length=50, default=""; verbose=飞手姓名（冗余）
- start_time: type=DateTimeField; constraints=nullable, blank; verbose=开始时间
- end_time: type=DateTimeField; constraints=nullable, blank; verbose=结束时间
- flight_duration: type=PositiveIntegerField; constraints=nullable, blank; verbose=飞行时长（秒）
- photo_count: type=PositiveIntegerField; constraints=default=0; verbose=拍摄照片数量
- video_count: type=PositiveIntegerField; constraints=default=0; verbose=录制视频数量
- status: type=PositiveSmallIntegerField; constraints=default=FlightRecordStatus.IN_PROGRESS; verbose=状态
- created_at: type=DateTimeField; constraints=N/A; verbose=创建时间
- updated_at: type=DateTimeField; constraints=N/A; verbose=更新时间

## 序列化读写边界
- FlightRecordReadSerializer: id, flight_no, mission, mission_name, route_name, airport_name, drone, drone_name, pilot, pilot_name, start_time, end_time, flight_duration, photo_count, video_count, status, created_at, updated_at
- FlightRecordWriteSerializer:
  - 允许字段: `flight_no`, `mission`, `drone`, `pilot`, `start_time`, `end_time`, `flight_duration`, `photo_count`, `video_count`, `status`, `airport_name`
  - 不可写字段: `mission_name`, `route_name`, `drone_name`, `pilot_name`, `created_at`, `updated_at`
  - 约束: `end_time` 不能早于 `start_time`；若同时给出 `mission` 与 `drone/pilot`，绑定关系必须一致

## 业务状态码覆盖
- 已覆盖: SUCCESS, INVALID_PARAMS, PERMISSION_DENIED, RESOURCE_NOT_FOUND
- 目标集合: SUCCESS, INVALID_PARAMS, PERMISSION_DENIED, RESOURCE_NOT_FOUND, STATE_CONFLICT, IDEMPOTENT_DUPLICATE
- 当前缺口: STATE_CONFLICT, IDEMPOTENT_DUPLICATE

## 权限码
- view_flight_record: 可查看飞行记录
- manage_flight_record: 可新增与编辑飞行记录

## 证据文件
- apps/access/management/commands/seed_role_permissions.py
- apps/api_v1/urls.py
- config/settings.py
- apps/flight_record/
- apps/flight_record/models.py
- apps/flight_record/serializers.py
- apps/flight_record/views.py
- apps/flight_record/urls.py
- apps/flight_record/tests.py

## 本轮增补（PATCH /api/v1/flight-records/{id}）
- 关联 API: PATCH /api/v1/flight-records/{id}
- 写入语义: 局部更新飞行记录主数据，成功返回 `SUCCESS`；空 body 或参数非法返回 `INVALID_PARAMS`；无权限返回 `PERMISSION_DENIED`；资源不存在返回 `RESOURCE_NOT_FOUND`。
- 约束说明: PATCH 只更新单条 flight_record 自身可写字段，不承担媒体文件编排、级联删除或跨实体状态流转。

<!-- stage6_doc_sync::flight_record::data_dictionary.md::start -->
## Stage6 本轮同步
- 关联 API: POST /api/v1/flight-records/{id}/abort
- 提名依据: flight_record 在补齐 complete 后，仍缺少把飞行中记录显式落到 ABORTED 的基础动作入口。补齐 abort 后，flight_record 的显式状态动作才同时覆盖正常结束和异常结束两条主路径。
- 业务事件: EVT-001 异常终止飞行记录
- 业务码覆盖: 目标=SUCCESS, INVALID_PARAMS, PERMISSION_DENIED, RESOURCE_NOT_FOUND, STATE_CONFLICT, IDEMPOTENT_DUPLICATE; 已覆盖=SUCCESS, PERMISSION_DENIED, RESOURCE_NOT_FOUND, STATE_CONFLICT, INVALID_PARAMS; 缺口=IDEMPOTENT_DUPLICATE
- 测试沉淀: 生成用例数: 5, 已执行用例数: 5, 已沉淀到项目测试: 5, 待沉淀 case: N/A, 失败 case: N/A
- 证据文件: apps/flight_record/tests.py, apps/flight_record/views.py, apps/flight_record/models.py, apps/flight_record/serializers.py, apps/flight_record/urls.py
<!-- stage6_doc_sync::flight_record::data_dictionary.md::end -->
