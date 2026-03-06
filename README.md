# 低空平台权限系统（V3）

本仓库当前采用双平面 API 架构：

1. Internal IAM Plane（内部管理）
- 前缀：`/internal/auth/*`
- 文档：`/internal/docs/`
- 用途：账号、身份类型、能力组、权限范围、审计

2. Business API Plane（业务开放）
- 前缀：`/api/v1/*`
- 文档：`/api/v1/docs/`
- 用途：业务资源接口（当前已开放无人机台账与分配关系）

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

## 当前 API 入口

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

2. Business API
- `GET /api/v1/`
- `GET /api/v1/health`
- `GET/POST /api/v1/drones`
- `GET/PUT/PATCH /api/v1/drones/{id}`
- `POST /api/v1/drones/{id}/enable`
- `POST /api/v1/drones/{id}/disable`
- `POST /api/v1/drones/{id}/maintenance`
- `POST /api/v1/drones/{id}/retire`
- `GET/POST /api/v1/drone-assignments`
- `GET /api/v1/drone-assignments/{id}`
- `POST /api/v1/drone-assignments/{id}/cancel`

## 文档导航

- 总体概念图：[overall_er_diagram.md](项目总体概览/概念设计/overall_er_diagram.md)
- 总体逻辑模型：[overall_logical_model.md](项目总体概览/逻辑设计/overall_logical_model.md)
- 总体数据字典：[overall_data_dictionary.md](项目总体概览/逻辑设计/overall_data_dictionary.md)
- 总体 DBML：[overall_schema.dbml](项目总体概览/逻辑设计/overall_schema.dbml)
- 业务接口扩展指南：[业务接口扩展指南.md](项目总体概览/业务接口扩展指南.md)
- 无人机实现设计：[drone_impl_desc.md](业务侧实现/drone_impl_desc.md)
- 无人机逻辑模型：[drone_logical_model.md](业务侧实现/drone_logical_model.md)
- 无人机数据字典：[drone_data_dictionary.md](业务侧实现/drone_data_dictionary.md)
- 无人机 DBML：[drone_schema.dbml](业务侧实现/drone_schema.dbml)
- 权限设计基线：[权限设计.md](权限管理侧实现/权限设计.md)
- 角色权限矩阵：[角色权限矩阵设计.md](权限管理侧实现/角色权限矩阵设计.md)
- 维护学习参考：[维护与学习参考.md](权限管理侧实现/维护与学习参考.md)
- Admin 与表关系：[Admin菜单与数据库表关系说明.md](权限管理侧实现/Admin菜单与数据库表关系说明.md)
- 权限逻辑模型：[authz_logical_model.md](权限管理侧实现/authz_logical_model.md)
- 权限 DBML：[authz_schema.dbml](权限管理侧实现/authz_schema.dbml)

## 代码定位

- IAM 模块：`apps/access/*`
- 业务 API 骨架：`apps/api_v1/*`
- 路由编排：`config/urls.py`

## TODO（未完成功能规划 / 基于当前设计）

### 1. 业务前端（当前未做）

- [ ] 实现业务登录页与会话管理（对接 `/api/v1/*`）。
- [ ] 实现无人机台账页面（列表、详情、新增、编辑、状态动作）。
- [ ] 实现无人机分配页面（创建分配、取消分配、分配列表筛选）。
- [ ] 实现“按权限显示菜单与按钮”（`business_admin` / `dispatcher` / `pilot_operator`）。

### 2. 航线域（Route/Waypoint，当前未做）

- [ ] 新增 `routes`、`waypoints` 业务模型与迁移。
- [ ] 开放 `/api/v1/routes*`、`/api/v1/waypoints*` 接口。
- [ ] 新增权限码并接入矩阵（建议：`route.view_route`、`route.manage_route`）。
- [ ] 在 `route_planner` 角色落地对应能力组与 scope。

### 3. 任务域（Mission，当前未做）

- [ ] 新增 `missions` 模型与任务状态机（待执行/执行中/完成/取消等）。
- [ ] 开放 `/api/v1/missions*` 接口（创建、派发、状态流转、查询）。
- [ ] 落地任务与无人机/飞手的业务约束（如退役无人机不可派发）。
- [ ] 新增任务域权限矩阵并补测试。

### 4. 飞行记录与媒体域（当前未做）

- [ ] 新增 `flight_records`、`media_files` 模型与迁移。
- [ ] 开放 `/api/v1/flight-records*`、`/api/v1/media-files*` 接口。
- [ ] 定义飞手上传、调度查看、审计查看的权限边界。
- [ ] 补“删除策略”落地（逻辑删除、审计追踪、清理策略）。

### 5. IAM 能力增强（当前部分已做）

- [ ] 增加角色矩阵导出接口（便于审阅与存档）。
- [ ] 增加权限变更差异日志展示（变更前后矩阵对比）。
- [ ] 增加一键校验命令：检查 `staff_type -> group -> permission(scope)` 是否完整。

### 6. 部署与边界治理（当前未做）

- [ ] 按环境隔离 Internal IAM 与 Business API 的访问入口（网关/白名单）。
- [ ] 区分生产鉴权策略（如业务侧切换为 Token/JWT，后台保留 Session）。
- [ ] 增加 CI 任务：`check + migration check + tests` 强制通过后再发布。

### 7. 文档持续维护规则

- [ ] 每新增业务域，同步新增并更新 `业务侧实现/<domain>_logical_model.md`、`业务侧实现/<domain>_data_dictionary.md`、`业务侧实现/<domain>_schema.dbml`。
- [ ] 每次矩阵调整，同步更新 `权限管理侧实现/角色权限矩阵设计.md`。
- [ ] 涉及跨域模型变更，同步更新 `项目总体概览/逻辑设计/overall_*` 文档。
- [ ] Future 规划文档必须标注 `Future`，防止与“当前实现”混淆。
