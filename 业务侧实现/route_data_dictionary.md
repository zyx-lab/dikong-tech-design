# route 数据字典

- updated_at: 2026-03-08T07:52:00Z
- entity: route

## 业务定位
- 历史 API: POST /api/v1/routes
- 历史增补 API: GET /api/v1/routes
- 当前轮增补 API: GET /api/v1/routes/{id}
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
- RouteWriteSerializer（POST 写入）：
  - 允许字段：`name`, `route_type`, `drone_type_id`, `total_distance`, `estimated_duration`, `waypoint_count`
  - 拒绝未知字段：返回 `INVALID_PARAMS`
- RouteReadSerializer（GET/POST 返回）：
  - 返回字段：`id`, `name`, `route_type`, `drone_type_id`, `total_distance`, `estimated_duration`, `waypoint_count`, `creator_name`, `status`, `created_at`, `updated_at`

## 详情读取响应语义（当前轮）
- 接口：`GET /api/v1/routes/{id}`
- 成功响应：返回单条 route 对象（字段同 `RouteReadSerializer`）并携带 `business_code=SUCCESS`
- 无权限：返回 `business_code=PERMISSION_DENIED`
- 资源不存在：返回 `business_code=RESOURCE_NOT_FOUND`（detail code：`ROUTE_NOT_FOUND`）

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

## 权限码
- route.manage_route：创建航线（POST）
- route.view_route：查看航线列表/详情（GET）

## 证据文件
- apps/route/models.py
- apps/route/serializers.py
- apps/route/views.py
- apps/route/tests.py
- apps/route/urls.py
- apps/api_v1/urls.py
- apps/api_v1/business_response.py


<!-- stage6_round_context::route::GET /api/v1/routes/{id} -->
## Stage6 同步上下文（可追溯）
```json
{
  "generated_at": "2026-03-08T07:38:38.976856Z",
  "entity": "route",
  "focus_api_keys": [
    "GET /api/v1/routes/{id}"
  ],
  "stage0_reason": "在已具备 POST/GET 列表基础上，补齐航线详情读取能力，供任务编排前按 route_id 精确拉取航线主数据；接口保持只读、单一职责、可组合。",
  "source_artifacts": [
    "codex_devflow_scaffold/artifacts/stage0/latest.json",
    "codex_devflow_scaffold/artifacts/stage2/latest.json",
    "codex_devflow_scaffold/artifacts/stage4/latest.json",
    "codex_devflow_scaffold/artifacts/stage5/latest.json"
  ],
  "related_paths": [
    "apps/access/management/commands/seed_role_permissions.py",
    "apps/api_v1/urls.py",
    "apps/api_v1/views.py",
    "apps/drone/urls.py",
    "config/settings.py",
    "apps/route/",
    "apps/route/models.py",
    "apps/route/serializers.py",
    "apps/route/views.py",
    "apps/route/urls.py",
    "apps/route/tests.py"
  ]
}
```
