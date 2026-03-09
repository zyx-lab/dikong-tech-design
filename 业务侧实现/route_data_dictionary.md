# route 数据字典

- updated_at: 2026-03-08T07:52:00Z
- entity: route

## 业务定位
- 历史 API: POST /api/v1/routes
- 历史增补 API: GET /api/v1/routes
- 历史增补 API: GET /api/v1/routes/{id}
- 历史增补 API: DELETE /api/v1/routes/{id}
- 当前轮增补 API: PATCH /api/v1/routes/{id}
- 业务目的：航线台账先创建、后查询；外部系统基于基础接口自行组合业务流。

## 字段定义（来自模型代码）
- id: type=BigAutoField; constraints=pk; verbose=ID
- name: type=CharField(100); constraints=NOT NULL; verbose=航线名称
- route_type: type=PositiveSmallIntegerField; constraints=NOT NULL, default=RouteType.PENDING_EXTENSION(0); verbose=航线类型扩展位
- drone_type_id: type=BigIntegerField; constraints=NULLABLE; verbose=适用无人机类型 ID
- total_distance: type=DecimalField(12,2); constraints=NULLABLE; verbose=航线总长度(米)
- estimated_duration: type=PositiveIntegerField; constraints=NULLABLE; verbose=预计飞行时长(秒)
- waypoint_count: type=PositiveIntegerField; constraints=NULLABLE; verbose=航点数量
- creator_name: type=CharField(50); constraints=NOT NULL, default=''; verbose=创建人姓名
- status: type=PositiveSmallIntegerField; constraints=NOT NULL, default=RouteStatus.ACTIVE(1); verbose=状态
- created_at: type=DateTimeField(auto_now_add); constraints=NOT NULL; verbose=创建时间
- updated_at: type=DateTimeField(auto_now); constraints=NOT NULL; verbose=更新时间

## 序列化读写边界
- RouteWriteSerializer（POST/PATCH 写入）：
  - 允许字段：`name`, `route_type`, `drone_type_id`, `total_distance`, `estimated_duration`, `waypoint_count`
  - 拒绝未知字段：返回 `INVALID_PARAMS`
- RouteReadSerializer（GET/POST/PATCH 返回）：
  - 返回字段：`id`, `name`, `route_type`, `drone_type_id`, `total_distance`, `estimated_duration`, `waypoint_count`, `creator_name`, `status`, `created_at`, `updated_at`

## 详情读取响应语义（当前轮）
- 接口：`GET /api/v1/routes/{id}`
- 成功响应：返回单条 route 对象（字段同 `RouteReadSerializer`）并携带 `business_code=SUCCESS`
- 无权限：返回 `business_code=PERMISSION_DENIED`
- 资源不存在：返回 `business_code=RESOURCE_NOT_FOUND`（detail code：`ROUTE_NOT_FOUND`）

## 更新响应语义（当前轮）
- 接口：`PATCH /api/v1/routes/{id}`
- 成功响应：返回最新 route 对象（字段同 `RouteReadSerializer`）并携带 `business_code=SUCCESS`
- 参数错误：PATCH 请求体为空或包含不可写字段时返回 `business_code=INVALID_PARAMS`
- 无权限：返回 `business_code=PERMISSION_DENIED`
- 资源不存在：返回 `business_code=RESOURCE_NOT_FOUND`

## 删除响应语义（历史增补）
- 接口：`DELETE /api/v1/routes/{id}`
- 成功响应：
  - 未被 mission 引用：物理删除 route，返回 `deleted=true`, `delete_mode=hard`
  - 已被 mission 引用：保留 route 主记录并将 `status=DISABLED(0)`，返回 `deleted=true`, `delete_mode=disabled`
- 参数错误：DELETE 请求携带 body 时返回 `business_code=INVALID_PARAMS`
- 无权限：返回 `business_code=PERMISSION_DENIED`
- 资源不存在：返回 `business_code=RESOURCE_NOT_FOUND`

## 业务状态码覆盖
- POST /api/v1/routes:
  - 已覆盖：SUCCESS, INVALID_PARAMS, PERMISSION_DENIED
  - 缺口：RESOURCE_NOT_FOUND, STATE_CONFLICT, IDEMPOTENT_DUPLICATE
- GET /api/v1/routes:
  - 已覆盖：SUCCESS, PERMISSION_DENIED
  - 缺口：INVALID_PARAMS, RESOURCE_NOT_FOUND, STATE_CONFLICT, IDEMPOTENT_DUPLICATE
- GET /api/v1/routes/{id}:
  - 已覆盖：SUCCESS, PERMISSION_DENIED, RESOURCE_NOT_FOUND
  - 缺口：INVALID_PARAMS, STATE_CONFLICT, IDEMPOTENT_DUPLICATE
- PATCH /api/v1/routes/{id}:
  - 已覆盖：SUCCESS, INVALID_PARAMS, PERMISSION_DENIED, RESOURCE_NOT_FOUND
  - 缺口：STATE_CONFLICT, IDEMPOTENT_DUPLICATE
- DELETE /api/v1/routes/{id}:
  - 已覆盖：SUCCESS, INVALID_PARAMS, PERMISSION_DENIED, RESOURCE_NOT_FOUND
  - 缺口：STATE_CONFLICT, IDEMPOTENT_DUPLICATE

## 权限码
- route.manage_route：创建航线（POST）
- route.manage_route：更新航线（PATCH）
- route.manage_route：删除航线（DELETE）
- route.view_route：查看航线列表/详情（GET）

## 证据文件
- apps/route/models.py
- apps/route/serializers.py
- apps/route/views.py
- apps/route/tests.py
- apps/route/urls.py
- apps/api_v1/urls.py
- apps/api_v1/business_response.py

<!-- stage6_doc_sync::route::data_dictionary.md::start -->
## Stage6 本轮同步
- 关联 API: PATCH /api/v1/routes/{id}
- 提名依据: 当前 route 已具备创建、列表、详情与删除能力，但缺少基础编辑入口，调用方无法修正航线名称、预计时长、适配机型等台账元数据。补齐 PATCH 后，route 才具备完整的最小可维护生命周期。
- 业务事件: EVT-001 更新航线
- 业务码覆盖: 目标=SUCCESS, INVALID_PARAMS, PERMISSION_DENIED, RESOURCE_NOT_FOUND, STATE_CONFLICT, IDEMPOTENT_DUPLICATE; 已覆盖=SUCCESS, PERMISSION_DENIED, RESOURCE_NOT_FOUND, INVALID_PARAMS; 缺口=STATE_CONFLICT, IDEMPOTENT_DUPLICATE
- 测试沉淀: 生成用例数: N/A, 已执行用例数: N/A, 已沉淀到项目测试: N/A, 待沉淀 case: N/A, 失败 case: N/A
- 证据文件: apps/route/tests.py, apps/route/views.py, apps/route/models.py, apps/route/serializers.py, apps/route/urls.py
<!-- stage6_doc_sync::route::data_dictionary.md::end -->
