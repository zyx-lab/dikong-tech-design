# waypoint 数据字典

- generated_at: 2026-03-09T02:39:31.679337Z
- entity: waypoint

## 业务定位
- 关联 API: POST /api/v1/waypoints
- 业务目的: 项目总体概览已定义 waypoints 为 routes 的核心子实体（1:N），当前代码尚无航点实体与基础写入接口。先补齐 POST 后，外部系统可组合“创建航线 -> 写入航点 -> 创建任务”业务流，不引入编排型接口。

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
