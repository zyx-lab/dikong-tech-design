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

- 权限设计基线：[权限设计.md](权限管理设计/权限设计.md)
- 角色权限矩阵：[角色权限矩阵设计.md](权限管理设计/角色权限矩阵设计.md)
- 维护学习参考：[维护与学习参考.md](权限管理设计/维护与学习参考.md)
- Admin 与表关系：[Admin菜单与数据库表关系说明.md](权限管理设计/Admin菜单与数据库表关系说明.md)
- 业务接口扩展指南：[业务接口扩展指南.md](业务侧概念设计/业务接口扩展指南.md)
- 无人机管理设计（当前实现）：[无人机管理V1设计计划.md](业务侧概念设计/无人机管理V1设计计划.md)
- 业务逻辑模型（当前实现）：[business_logical_model.md](逻辑设计/business_logical_model.md)
- 业务数据字典（当前实现）：[business_data_dictionary.md](逻辑设计/business_data_dictionary.md)
- 业务 DBML（当前实现）：[schema.dbml](逻辑设计/schema.dbml)
- 权限逻辑模型：[authz_logical_model.md](逻辑设计/authz_logical_model.md)
- 权限 DBML：[authz_schema.dbml](逻辑设计/authz_schema.dbml)

## 代码定位

- IAM 模块：`apps/access/*`
- 业务 API 骨架：`apps/api_v1/*`
- 路由编排：`config/urls.py`
