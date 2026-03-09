# mission 数据字典

- updated_at: 2026-03-09T09:30:00+08:00
- entity: mission

## 业务定位
- 历史 API: POST /api/v1/missions
- 历史增补 API: GET /api/v1/missions
- 历史增补 API: GET /api/v1/missions/{id}
- 当前轮增补 API: PATCH /api/v1/missions/{id}
- 本轮增补 API: POST /api/v1/missions/{id}/cancel
- 业务目的：任务实体提供“创建 + 查询 + 局部更新 + 启动 + 暂停 + 恢复 + 取消”的基础能力，供外部系统按需组合调度流程。

## 字段定义（来自模型代码）
- id: type=BigAutoField; constraints=PK; verbose=ID
- name: type=CharField(100); constraints=NOT NULL; verbose=任务名称
- route: type=ForeignKey(route.Route); constraints=NOT NULL, PROTECT; verbose=航线
- route_name: type=CharField(100); constraints=NOT NULL, default=''; verbose=航线名称（冗余）
- drone: type=ForeignKey(drone.Drone); constraints=NOT NULL, PROTECT; verbose=无人机
- drone_name: type=CharField(100); constraints=NOT NULL, default=''; verbose=无人机名称（冗余）
- pilot: type=ForeignKey(access.StaffProfile); constraints=NOT NULL, PROTECT; verbose=飞手
- pilot_name: type=CharField(50); constraints=NOT NULL, default=''; verbose=飞手姓名（冗余）
- scheduled_at: type=DateTimeField; constraints=NULLABLE; verbose=计划执行时间
- remark: type=CharField(500); constraints=NOT NULL, default=''; verbose=任务备注
- status: type=PositiveSmallIntegerField; constraints=NOT NULL, default=MissionStatus.PENDING(0); verbose=任务状态
- created_at: type=DateTimeField(auto_now_add); constraints=NOT NULL; verbose=创建时间
- updated_at: type=DateTimeField(auto_now); constraints=NOT NULL; verbose=更新时间

## 序列化读写边界
- MissionWriteSerializer（POST/PATCH 写入）：
  - 可写字段：`name`, `route`, `drone`, `pilot`, `scheduled_at`, `remark`
  - 不可写字段：`status` 及其他未声明字段（返回 `INVALID_PARAMS`）
  - 关键校验：route 必须 ACTIVE、drone 必须 ENABLED、pilot 必须在职且类型为 `pilot_operator`
- MissionReadSerializer（GET 返回）：
  - 返回字段：`id`, `name`, `route`, `route_name`, `drone`, `drone_name`, `pilot`, `pilot_name`, `scheduled_at`, `remark`, `status`, `created_at`, `updated_at`

## 详情读取响应语义（历史增补）
- 接口：`GET /api/v1/missions/{id}`
- 成功响应：返回单条 mission 对象，并携带 `business_code=SUCCESS`
- 无权限：`business_code=PERMISSION_DENIED`
- 资源不存在：`business_code=RESOURCE_NOT_FOUND`（detail code: `NOT_FOUND`）

## 当前轮 PATCH 响应语义
- 接口：`PATCH /api/v1/missions/{id}`
- 成功响应：返回更新后的 mission 对象，并携带 `business_code=SUCCESS`
- 参数非法：`business_code=INVALID_PARAMS`（detail code: `VALIDATION_ERROR`）
- 无权限：`business_code=PERMISSION_DENIED`
- 资源不存在：`business_code=RESOURCE_NOT_FOUND`（detail code: `NOT_FOUND`）

## 本轮 cancel 响应语义
- 接口：`POST /api/v1/missions/{id}/cancel`
- 成功响应：返回取消后的 mission 对象，并携带 `business_code=SUCCESS`
- 参数非法：请求体非空，返回 `business_code=INVALID_PARAMS`
- 无权限：`business_code=PERMISSION_DENIED`
- 资源不存在：`business_code=RESOURCE_NOT_FOUND`（detail code: `NOT_FOUND`）
- 状态冲突：已完成、已取消或已失败任务再次取消，返回 `business_code=STATE_CONFLICT`

## 本轮 start 响应语义
- 接口：`POST /api/v1/missions/{id}/start`
- 成功响应：返回启动后的 mission 对象，并携带 `business_code=SUCCESS`
- 幂等成功：已处于 `RUNNING` 的任务重复 start 仍返回 `SUCCESS`
- 参数非法：请求体非空，返回 `business_code=INVALID_PARAMS`
- 无权限：`business_code=PERMISSION_DENIED`
- 资源不存在：`business_code=RESOURCE_NOT_FOUND`（detail code: `NOT_FOUND`）
- 状态冲突：`PAUSED`、`COMPLETED`、`CANCELED`、`FAILED` 任务 start 返回 `business_code=STATE_CONFLICT`

## 本轮 pause 响应语义
- 接口：`POST /api/v1/missions/{id}/pause`
- 成功响应：返回暂停后的 mission 对象，并携带 `business_code=SUCCESS`
- 幂等成功：已处于 `PAUSED` 的任务重复 pause 仍返回 `SUCCESS`
- 参数非法：请求体非空，返回 `business_code=INVALID_PARAMS`
- 无权限：`business_code=PERMISSION_DENIED`
- 资源不存在：`business_code=RESOURCE_NOT_FOUND`（detail code: `NOT_FOUND`）
- 状态冲突：`PENDING`、`COMPLETED`、`CANCELED`、`FAILED` 任务 pause 返回 `business_code=STATE_CONFLICT`

## 本轮 resume 响应语义
- 接口：`POST /api/v1/missions/{id}/resume`
- 成功响应：返回恢复后的 mission 对象，并携带 `business_code=SUCCESS`
- 幂等成功：已处于 `RUNNING` 的任务重复 resume 仍返回 `SUCCESS`
- 参数非法：请求体非空，返回 `business_code=INVALID_PARAMS`
- 无权限：`business_code=PERMISSION_DENIED`
- 资源不存在：`business_code=RESOURCE_NOT_FOUND`（detail code: `NOT_FOUND`）
- 状态冲突：`PENDING`、`COMPLETED`、`CANCELED`、`FAILED` 任务 resume 返回 `business_code=STATE_CONFLICT`

## 业务状态码覆盖
- POST /api/v1/missions：
  - 已覆盖：SUCCESS, INVALID_PARAMS, PERMISSION_DENIED
  - 缺口：RESOURCE_NOT_FOUND, STATE_CONFLICT, IDEMPOTENT_DUPLICATE
- GET /api/v1/missions：
  - 已覆盖：SUCCESS, PERMISSION_DENIED
  - 缺口：INVALID_PARAMS, RESOURCE_NOT_FOUND, STATE_CONFLICT, IDEMPOTENT_DUPLICATE
- GET /api/v1/missions/{id}：
  - 已覆盖：SUCCESS, PERMISSION_DENIED, RESOURCE_NOT_FOUND
  - 缺口：INVALID_PARAMS, STATE_CONFLICT, IDEMPOTENT_DUPLICATE
- PATCH /api/v1/missions/{id}：
  - 已覆盖：SUCCESS, INVALID_PARAMS, PERMISSION_DENIED, RESOURCE_NOT_FOUND
  - 缺口：STATE_CONFLICT, IDEMPOTENT_DUPLICATE
- POST /api/v1/missions/{id}/cancel：
  - 已覆盖：SUCCESS, INVALID_PARAMS, PERMISSION_DENIED, RESOURCE_NOT_FOUND, STATE_CONFLICT
  - 缺口：IDEMPOTENT_DUPLICATE
- POST /api/v1/missions/{id}/start：
  - 已覆盖：SUCCESS, INVALID_PARAMS, PERMISSION_DENIED, RESOURCE_NOT_FOUND, STATE_CONFLICT
  - 缺口：IDEMPOTENT_DUPLICATE
- POST /api/v1/missions/{id}/pause：
  - 已覆盖：SUCCESS, INVALID_PARAMS, PERMISSION_DENIED, RESOURCE_NOT_FOUND, STATE_CONFLICT
  - 缺口：IDEMPOTENT_DUPLICATE
- POST /api/v1/missions/{id}/resume：
  - 已覆盖：SUCCESS, INVALID_PARAMS, PERMISSION_DENIED, RESOURCE_NOT_FOUND, STATE_CONFLICT
  - 缺口：IDEMPOTENT_DUPLICATE

## 权限码
- mission.manage_mission：创建、局部更新、启动、暂停、恢复与取消任务（POST/PATCH/start/pause/resume/cancel）
- mission.view_mission：查看任务列表与详情（GET）

## 证据文件
- apps/mission/models.py
- apps/mission/serializers.py
- apps/mission/views.py
- apps/mission/tests.py
- apps/mission/urls.py
- apps/api_v1/business_response.py

<!-- stage6_doc_sync::mission::data_dictionary.md::start -->
## Stage6 本轮同步
- 关联 API: POST /api/v1/missions/{id}/complete
- 提名依据: mission 现在已有创建、查询、局部更新、启动、暂停、恢复和取消，但仍缺少把执行中任务闭环为已完成的基础入口，状态机无法从 RUNNING 落到 COMPLETED。先补齐 complete，才能让任务生命周期具备最小完成闭环。
- 业务事件: EVT-001 完成任务
- 业务码覆盖: 目标=SUCCESS, INVALID_PARAMS, PERMISSION_DENIED, RESOURCE_NOT_FOUND, STATE_CONFLICT, IDEMPOTENT_DUPLICATE; 已覆盖=SUCCESS, PERMISSION_DENIED, RESOURCE_NOT_FOUND, STATE_CONFLICT, INVALID_PARAMS; 缺口=IDEMPOTENT_DUPLICATE
- 测试沉淀: 生成用例数: 5, 已执行用例数: 5, 已沉淀到项目测试: 5, 待沉淀 case: N/A, 失败 case: N/A
- 证据文件: apps/mission/tests.py, apps/mission/views.py, apps/mission/models.py, apps/mission/serializers.py, apps/mission/urls.py
<!-- stage6_doc_sync::mission::data_dictionary.md::end -->
