# waypoint 数据字典

- generated_at: 2026-03-09T02:39:31.679337Z
- entity: waypoint

## 业务定位
- 关联 API: POST /api/v1/waypoints, GET /api/v1/waypoints
- 业务目的: waypoint 作为 route 的子实体，提供最小可组合能力：先通过 POST 写入航点，再通过 GET 做列表读取与结果校验，外部再按需编排任务流程。

## 字段定义（来自模型代码）
- id: type=BigAutoField; constraints=pk; verbose=ID
- route: type=ForeignKey; constraints=N/A; verbose=route.Route
- sequence: type=PositiveIntegerField; constraints=N/A; verbose=航点序号
- latitude: type=DecimalField; constraints=N/A; verbose=纬度
- longitude: type=DecimalField; constraints=N/A; verbose=经度
- altitude: type=DecimalField; constraints=N/A; verbose=飞行高度（米）
- created_at: type=DateTimeField; constraints=N/A; verbose=创建时间

## 序列化读写边界
- WaypointReadSerializer: id, route, sequence, latitude, longitude, altitude, created_at
- WaypointWriteSerializer: route, sequence, latitude, longitude, altitude

## 业务状态码覆盖
- 已覆盖: SUCCESS, INVALID_PARAMS, PERMISSION_DENIED
- 目标集合: SUCCESS, INVALID_PARAMS, PERMISSION_DENIED, RESOURCE_NOT_FOUND, STATE_CONFLICT, IDEMPOTENT_DUPLICATE
- 当前缺口: RESOURCE_NOT_FOUND, STATE_CONFLICT, IDEMPOTENT_DUPLICATE

## 本轮语义增补（GET /api/v1/waypoints）
- 接口定位: 读取航点列表的只读入口，不承担编辑/删除/重排等编排行为。
- 输入维度: 支持 `route_id`、`sequence` 过滤。
- 成功语义: HTTP 200，`business_code=SUCCESS`，`business_detail_code=OK`。
- 权限失败语义: HTTP 401/403，`business_code=PERMISSION_DENIED`，`business_detail_code` 为权限细分码。

## 权限码
- view_waypoint: 可查看航点
- manage_waypoint: 可管理航点

## 证据文件
- apps/access/management/commands/seed_role_permissions.py
- apps/api_v1/urls.py
- config/settings.py
- apps/waypoint/
- apps/waypoint/models.py
- apps/waypoint/serializers.py
- apps/waypoint/views.py
- apps/waypoint/urls.py
- apps/waypoint/tests.py
