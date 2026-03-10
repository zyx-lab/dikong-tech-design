# 低空平台权限系统（V3）

## 当前状态（对齐日期：2026-03-10）

本仓库当前是一个 Django + DRF 的双平面 API 项目，代码已实现：

1. Internal IAM Plane（内部管理）
- 前缀：`/internal/auth/*`
- 文档：`/internal/docs/`
- 能力：账号管理、能力组与权限范围管理、身份类型映射、审计日志查询

2. Business API Plane（业务开放）
- 前缀：`/api/v1/*`
- 文档：`/api/v1/docs/`
- 已实现业务域：
  - 无人机台账（drone）：CRUD + 状态流转（启用/停用/维护/退役）
  - 无人机分配（drone_assignment）：CRUD + 状态流转（取消/恢复）
  - 航线（route）：CRUD + 状态流转（启用/禁用）
  - 航点（waypoint）：CRUD
  - 任务（mission）：CRUD + 状态流转（启动/暂停/恢复/完成/失败/取消）
  - 飞行记录（flight_record）：CRUD + 状态流转（完成/异常终止）
  - 媒体文件（media_file）：CRUD + 逻辑删除

## 快速启动

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_role_permissions --mode replace
python manage.py createsuperuser
python manage.py create_business_admin_account --username biz_root --password 'YourStrongPassword'
python manage.py runserver 0.0.0.0:8001
```

## 代码与能力映射

- 路由入口：`config/urls.py`
- Internal IAM：`apps/access/*`
- Business API 根入口：`apps/api_v1/*`
- 业务域：
  - `apps/drone/*` - 无人机台账
  - `apps/drone_assignment/*` - 无人机分配
  - `apps/route/*` - 航线
  - `apps/waypoint/*` - 航点
  - `apps/mission/*` - 任务
  - `apps/flight_record/*` - 飞行记录
  - `apps/media_file/*` - 媒体文件

### 已实现接口清单（与当前代码一致）

1. Internal IAM
- `GET /internal/auth/`
- `GET /internal/auth/session-status`
- `GET /internal/auth/me/permissions`
- `GET/POST /internal/auth/users`
- `GET/PUT/PATCH /internal/auth/users/{id}`
- `GET /internal/auth/permissions?app_label=xxx`
- `GET/POST /internal/auth/groups`
- `GET/PUT/PATCH /internal/auth/groups/{id}`
- `POST /internal/auth/groups/{id}/permissions`
- `POST /internal/auth/groups/{id}/scopes`
- `GET/POST /internal/auth/staff-types`
- `GET/PUT/PATCH /internal/auth/staff-types/{id}`
- `POST /internal/auth/staff-types/{id}/groups`
- `GET /internal/auth/scopes/matrix`
- `GET /internal/auth/audit-logs`

2. Business API - 无人机（drone）
- `GET/POST /api/v1/drones`
- `GET/PUT/PATCH /api/v1/drones/{id}`
- `DELETE /api/v1/drones/{id}`
- `POST /api/v1/drones/{id}/enable`
- `POST /api/v1/drones/{id}/disable`
- `POST /api/v1/drones/{id}/maintenance`
- `POST /api/v1/drones/{id}/retire`
- `GET /api/v1/drones/{id}/assignments/history`
- `GET /api/v1/drones/{id}/assignments/active`
- `GET /api/v1/drones/{id}/assignments/latest`

3. Business API - 无人机分配（drone_assignment）
- `GET/POST /api/v1/drone-assignments`
- `GET /api/v1/drone-assignments/{id}`
- `POST /api/v1/drone-assignments/{id}/cancel`
- `POST /api/v1/drone-assignments/{id}/reactivate`

4. Business API - 航线（route）
- `GET/POST /api/v1/routes`
- `GET/PUT/PATCH /api/v1/routes/{id}`
- `DELETE /api/v1/routes/{id}`
- `POST /api/v1/routes/{id}/enable`
- `POST /api/v1/routes/{id}/disable`

5. Business API - 航点（waypoint）
- `GET/POST /api/v1/waypoints`
- `GET/PATCH /api/v1/waypoints/{id}`
- `DELETE /api/v1/waypoints/{id}`

6. Business API - 任务（mission）
- `GET/POST /api/v1/missions`
- `GET/PATCH /api/v1/missions/{id}`
- `POST /api/v1/missions/{id}/start`
- `POST /api/v1/missions/{id}/pause`
- `POST /api/v1/missions/{id}/resume`
- `POST /api/v1/missions/{id}/complete`
- `POST /api/v1/missions/{id}/fail`
- `POST /api/v1/missions/{id}/cancel`

7. Business API - 飞行记录（flight_record）
- `GET/POST /api/v1/flight-records`
- `GET/PATCH /api/v1/flight-records/{id}`
- `POST /api/v1/flight-records/{id}/complete`
- `POST /api/v1/flight-records/{id}/abort`

8. Business API - 媒体文件（media_file）
- `GET/POST /api/v1/media-files`
- `GET/PATCH /api/v1/media-files/{id}`
- `DELETE /api/v1/media-files/{id}`

## 鉴权与授权边界

### 认证方式

| 方式 | 说明 |
|------|------|
| SessionAuthentication | 浏览器登录后 Django Session 保持登录状态 |
| BasicAuthentication | 用户名:密码 Base64 编码，用于跨系统调用 |

### 默认权限

除 4 个明确开放的接口外，其他所有接口都需要登录（IsAuthenticated）。

### 明确开放（无需登录）

| 接口 | 用途 |
|------|------|
| `GET /internal/auth/` | 检查 IAM 服务状态 |
| `GET /internal/auth/session-status` | 查看当前登录状态 |
| `GET /api/v1/` | 检查 API 服务状态 |
| `GET /api/v1/health` | 健康检查 |

### 两套权限体系

#### Internal IAM（内部管理）

使用 `RequireInternalPermission`，路径前缀 `/internal/auth/*`

**规则**：
1. 先检查是否是 superuser → 直接放行
2. 再检查是否有 staff + staff_type 身份 → 没有则拒绝
3. 最后检查权限码 → 通过 staff_type → group → permission 链判断

#### Business API（业务接口）

使用 `ScopedActionPermission`，路径前缀 `/api/v1/*`

**规则**：
1. 先检查权限码 → 每个 API action 对应一个权限码（如 `drone.view_drone`、`drone.manage_drone`）
2. 再检查数据范围（Scope） → 决定能看哪些数据

### Scope（数据可见范围）

| Scope | 含义 | 适用角色 |
|-------|------|----------|
| ALL | 全部数据 | 管理员 |
| OWN | 自己创建的 | 普通操作员 |
| ASSIGNED | 分配给自己的 | 组长/主管 |

**例子**：
- 无人机列表 API 配置 Scope=ASSIGNED → 用户只能看到分配给自己的无人机
- 任务列表 API 配置 Scope=OWN → 用户只能看到自己创建的任务

### superuser（超级管理员）

- **不通过 staff_type 授权**：不需要关联 staff、staff_type、group、permission
- **拥有全部权限**：可以操作所有数据，不受 Scope 限制
- **用途**：系统初始管理员、运维人员

## 文档导航

### 总体设计
- 总体概念图：[overall_er_diagram.md](项目总体概览/概念设计/overall_er_diagram.md)
- 总体逻辑模型：[overall_logical_model.md](项目总体概览/逻辑设计/overall_logical_model.md)
- 总体数据字典：[overall_data_dictionary.md](项目总体概览/逻辑设计/overall_data_dictionary.md)
- 总体 DBML：[overall_schema.dbml](项目总体概览/逻辑设计/overall_schema.dbml)
- 业务接口扩展指南：[业务接口扩展指南.md](项目总体概览/业务接口扩展指南.md)

### 业务侧实现（业务侧实现/）

| 业务域 | 数据字典 | 实现描述 | 逻辑模型 | 图表 |
|--------|----------|----------|----------|------|
| 无人机 | [drone_data_dictionary.md](业务侧实现/drone_data_dictionary.md) | [drone_impl_desc.md](业务侧实现/drone_impl_desc.md) | [drone_logical_model.md](业务侧实现/drone_logical_model.md) | [drone_schema.dbml](业务侧实现/drone_schema.dbml) |
| 无人机分配 | [drone_assignment_data_dictionary.md](业务侧实现/drone_assignment_data_dictionary.md) | [drone_assignment_impl_desc.md](业务侧实现/drone_assignment_impl_desc.md) | [drone_assignment_logical_model.md](业务侧实现/drone_assignment_logical_model.md) | [drone_assignment_schema.dbml](业务侧实现/drone_assignment_schema.dbml) |
| 航线 | [route_data_dictionary.md](业务侧实现/route_data_dictionary.md) | [route_impl_desc.md](业务侧实现/route_impl_desc.md) | [route_logical_model.md](业务侧实现/route_logical_model.md) | [route_schema.dbml](业务侧实现/route_schema.dbml) |
| 航点 | [waypoint_data_dictionary.md](业务侧实现/waypoint_data_dictionary.md) | [waypoint_impl_desc.md](业务侧实现/waypoint_impl_desc.md) | [waypoint_logical_model.md](业务侧实现/waypoint_logical_model.md) | [waypoint_schema.dbml](业务侧实现/waypoint_schema.dbml) |
| 任务 | [mission_data_dictionary.md](业务侧实现/mission_data_dictionary.md) | [mission_impl_desc.md](业务侧实现/mission_impl_desc.md) | [mission_logical_model.md](业务侧实现/mission_logical_model.md) | [mission_schema.dbml](业务侧实现/mission_schema.dbml) |
| 飞行记录 | [flight_record_data_dictionary.md](业务侧实现/flight_record_data_dictionary.md) | [flight_record_impl_desc.md](业务侧实现/flight_record_impl_desc.md) | [flight_record_logical_model.md](业务侧实现/flight_record_logical_model.md) | [flight_record_schema.dbml](业务侧实现/flight_record_schema.dbml) |
| 媒体文件 | [media_file_data_dictionary.md](业务侧实现/media_file_data_dictionary.md) | [media_file_impl_desc.md](业务侧实现/media_file_impl_desc.md) | [media_file_logical_model.md](业务侧实现/media_file_logical_model.md) | [media_file_schema.dbml](业务侧实现/media_file_schema.dbml) |

### 权限管理侧实现（权限管理侧实现/）
- 权限设计基线：[权限设计.md](权限管理侧实现/权限设计.md)
- 角色权限矩阵：[角色权限矩阵设计.md](权限管理侧实现/角色权限矩阵设计.md)
- 维护学习参考：[维护与学习参考.md](权限管理侧实现/维护与学习参考.md)
- Admin 与表关系：[Admin菜单与数据库表关系说明.md](权限管理侧实现/Admin菜单与数据库表关系说明.md)
- 权限逻辑模型：[authz_logical_model.md](权限管理侧实现/authz_logical_model.md)
- 权限 DBML：[authz_schema.dbml](权限管理侧实现/authz_schema.dbml)

## 近期路线（与代码现状对齐）

1. 业务前端（未开始）
- [ ] 登录与会话管理
- [ ] 无人机台账页面
- [ ] 无人机分配页面
- [ ] 航线/航点管理页面
- [ ] 任务管理页面
- [ ] 飞行记录与媒体文件页面
- [ ] 菜单/按钮级权限展示

2. 业务域扩展（已完成）
- [x] 无人机台账（drone）
- [x] 无人机分配（drone_assignment）
- [x] 航线域（routes/waypoints）
- [x] 任务域（missions）
- [x] 飞行记录与媒体域（flight_records/media_files）

3. IAM 增强（已完成）
- [x] 授权链完整性校验命令：`python manage.py check_auth_chain`
