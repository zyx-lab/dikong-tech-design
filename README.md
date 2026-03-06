# 低空平台权限系统（V3）

## 当前状态（对齐日期：2026-03-06）

本仓库当前是一个 Django + DRF 的双平面 API 项目，代码已实现：

1. Internal IAM Plane（内部管理）
- 前缀：`/internal/auth/*`
- 文档：`/internal/docs/`
- 能力：账号管理、能力组与权限范围管理、身份类型映射、审计日志查询

2. Business API Plane（业务开放）
- 前缀：`/api/v1/*`
- 文档：`/api/v1/docs/`
- 能力：无人机台账、无人机分配、无人机状态流转

3. Skill 规划（流程脚手架）
- 规划文件：`codex_devflow_scaffold/skill_implementation_plan.md`
- 当前实现状态：**仅规划文档已落库**，Runner/Skill 代码尚未落地（见下方“Skill 规划对齐”）

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
- 无人机业务域：`apps/drone/*`

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

## 鉴权与授权边界

- 认证方式：`SessionAuthentication` + `BasicAuthentication`
- 默认权限：`IsAuthenticated`
- 明确开放（AllowAny）：
  - `GET /internal/auth/`
  - `GET /internal/auth/session-status`
  - `GET /api/v1/`
  - `GET /api/v1/health`
- Internal IAM 权限类：`RequireInternalPermission`
- Business API 权限类：`ScopedActionPermission`
- Scope 类型：`ALL` / `OWN` / `ASSIGNED`
- superuser 作为 root 账号：不走 staff_type 授权链，拥有全量权限

## Skill 规划对齐（实现态 vs 规划态）

`codex_devflow_scaffold/skill_implementation_plan.md` 已定义 Stage0-Stage8 规范，但当前仓库实际状态如下：

1. 已存在
- `codex_devflow_scaffold/skill_implementation_plan.md`
- `codex_devflow_scaffold/` 下阶段目录骨架（`artifacts/inputs/schemas/...`）

2. 尚未落地（规划中）
- `tools/workflow_runner.py`
- `codex_skills/codex-tdd-devflow/`
- 规划中要求的 schema、state、registry、events 等核心文件

3. 结论
- 当前仓库的“可运行能力”仍以 Django API 主工程为主。
- `codex_devflow_scaffold` 目前是规范先行状态，不应被视为已可执行流程。

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
- Skill 实施规划：[skill_implementation_plan.md](codex_devflow_scaffold/skill_implementation_plan.md)

## 近期路线（与代码现状对齐）

1. 业务前端（未开始）
- [ ] 登录与会话管理
- [ ] 无人机台账页面
- [ ] 无人机分配页面
- [ ] 菜单/按钮级权限展示

2. 业务域扩展（未开始）
- [ ] 航线域（routes/waypoints）
- [ ] 任务域（missions）
- [ ] 飞行记录与媒体域（flight_records/media_files）

3. IAM 增强（部分已做）
- [ ] 矩阵导出接口
- [ ] 权限差异对比视图
- [ ] 授权链完整性一键校验命令

4. Skill 流程落地（未开始）
- [ ] 实现 `workflow_runner`
- [ ] 落地 `codex-tdd-devflow` skill 目录
- [ ] 按规划补齐 schemas/registry/state 机器校验闭环
