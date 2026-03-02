# DRF 后台实现说明（V3 / 双平面）

## 1. 实现范围
当前代码分为两个 API 平面：
1. Internal IAM Plane（内部管理平面）
- 路由前缀：`/internal/auth/*`
- 文档入口：`/internal/docs/`
- 功能：用户、角色组、范围策略、身份类型、审计查询

2. Business API Plane（业务平面）
- 路由前缀：`/api/v1/*`
- 文档入口：`/api/v1/docs/`
- 当前提供基础入口和健康检查，后续业务资源在此平面扩展

## 2. 代码结构
- `apps/access/*`：IAM 模型、鉴权服务、内部管理 API、审计
- `apps/api_v1/*`：业务平面入口与健康检查
- `config/urls.py`：双平面路由与文档路由编排
- `apps/access/management/commands/seed_role_permissions.py`：权限矩阵初始化

## 3. Internal IAM API 清单
前缀：`/internal/auth`

- `GET /`（IAM 根入口）
- `GET /session-status`
- `GET /me/permissions`
- `GET /users`
- `POST /users`
- `GET /users/{id}`
- `PUT/PATCH /users/{id}`
- `GET /permissions?app_label=xxx`
- `GET /groups`
- `POST /groups`
- `GET /groups/{id}`
- `PUT/PATCH /groups/{id}`
- `POST /groups/{id}/permissions`
- `POST /groups/{id}/scopes`
- `GET /staff-types`
- `POST /staff-types`
- `GET /staff-types/{id}`
- `PUT/PATCH /staff-types/{id}`
- `POST /staff-types/{id}/groups`
- `GET /scopes/matrix`
- `GET /audit-logs`

## 4. Business API 清单（当前）
前缀：`/api/v1`

- `GET /`（业务 API 根入口）
- `GET /health`

## 5. 鉴权策略
1. DRF 默认要求登录认证（Session/Basic）。
2. IAM 管理接口使用 `RequireDjangoPermission`：
- 先做身份校验（账号、staff、staff_type 等）
- 再做权限码命中校验（`StaffType -> Group -> Permission`）
3. scope 规则统一：`ALL > ASSIGNED > OWN`。

## 6. 审计策略
以下写操作写入 `auth_audit_logs`：
- 用户创建/修改
- Group 创建/修改
- Group 权限分配
- Group 范围分配
- StaffType 创建/修改
- StaffType-Group 映射变更

## 7. 运行命令
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_role_permissions --mode replace
python manage.py runserver
```

## 8. 发布建议
1. `internal/auth` 与 `internal/docs` 仅内网/VPN/白名单开放。
2. 公网只暴露 `api/v1`。
3. 不在业务平面暴露 IAM 管理动作。
