# flight record 数据字典

- generated_at: 2026-03-08T09:24:39.176004Z
- entity: flight_record

## 业务定位
- 关联 API: GET /api/v1/flight-records/{id}
- 业务目的: 总体设计已定义 flight_records 实体，当前业务平面尚无飞行记录读取能力；先补齐按记录ID读取详情的基础只读接口，供外部系统在媒体检索与任务复盘前拉取执行快照。

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

## 业务状态码覆盖
- 已覆盖: SUCCESS, PERMISSION_DENIED, RESOURCE_NOT_FOUND
- 目标集合: SUCCESS, INVALID_PARAMS, PERMISSION_DENIED, RESOURCE_NOT_FOUND, STATE_CONFLICT, IDEMPOTENT_DUPLICATE
- 当前缺口: INVALID_PARAMS, STATE_CONFLICT, IDEMPOTENT_DUPLICATE

## 权限码
- view_flight_record: 可查看飞行记录

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


<!-- stage6_round_context::flight_record::GET /api/v1/flight-records -->
## Stage6 同步上下文（可追溯）
```json
{
  "generated_at": "2026-03-08T10:16:51.849636Z",
  "entity": "flight_record",
  "focus_api_keys": [
    "GET /api/v1/flight-records"
  ],
  "stage0_reason": "在已具备按ID读取飞行记录详情后，补齐飞行记录列表查询这一基础只读接口，供外部系统先筛选记录再按ID拉取详情，形成可组合的复盘检索链路。",
  "source_artifacts": [
    "codex_devflow_scaffold/artifacts/stage0/latest.json",
    "codex_devflow_scaffold/artifacts/stage2/latest.json",
    "codex_devflow_scaffold/artifacts/stage4/latest.json",
    "codex_devflow_scaffold/artifacts/stage5/latest.json"
  ],
  "related_paths": [
    "apps/access/management/commands/seed_role_permissions.py",
    "apps/api_v1/urls.py",
    "config/settings.py",
    "apps/flight_record/",
    "apps/flight_record/models.py",
    "apps/flight_record/serializers.py",
    "apps/flight_record/views.py",
    "apps/flight_record/urls.py",
    "apps/flight_record/tests.py"
  ]
}
```
