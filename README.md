# 低空平台权限系统（V3）

本仓库当前采用双平面 API 架构：

1. Internal IAM Plane（内部管理）
- 前缀：`/internal/auth/*`
- 文档：`/internal/docs/`
- 用途：账号、身份类型、能力组、权限范围、审计

2. Business API Plane（业务开放）
- 前缀：`/api/v1/*`
- 文档：`/api/v1/docs/`
- 用途：业务资源接口（任务、无人机、飞行记录等）

## 快速启动

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_role_permissions --mode replace
python manage.py createsuperuser
python manage.py create_business_super_account --username biz_root --password 'YourStrongPassword'
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
- `GET /internal/auth/registration-applications`
- `GET /internal/auth/registration-applications/{id}`
- `POST /internal/auth/registration-applications/{id}/approve`
- `POST /internal/auth/registration-applications/{id}/reject`

2. Business API
- `GET /api/v1/`
- `GET /api/v1/health`
- `POST /api/v1/registration-applications`
- `GET /api/v1/registration-applications/{application_no}/status`
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

- 设计基线：[权限管理/权限设计.md](权限管理/权限设计.md)
- 权限矩阵：[权限管理/角色权限矩阵设计.md](权限管理/角色权限矩阵设计.md)
- 业务扩展：[权限管理/业务接口扩展指南.md](权限管理/业务接口扩展指南.md)
- 注册审核：[权限管理/注册申请审核系统设计.md](权限管理/注册申请审核系统设计.md)
- 无人机计划：[权限管理/无人机管理V1设计计划.md](权限管理/无人机管理V1设计计划.md)
- 维护学习：[权限管理/维护与学习参考.md](权限管理/维护与学习参考.md)
- Admin 与表关系：[权限管理/Admin菜单与数据库表关系说明.md](权限管理/Admin菜单与数据库表关系说明.md)

## 代码定位

- IAM 模块：`apps/access/*`
- 业务 API 骨架：`apps/api_v1/*`
- 路由编排：`config/urls.py`
