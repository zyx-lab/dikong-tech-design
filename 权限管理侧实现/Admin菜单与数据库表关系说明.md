# Admin 菜单与数据库表关系说明

你在 Admin 左侧看到的条目只有几行，这是正常现象。
**Admin 菜单显示的是“已注册且允许显示的模型”，不是数据库全部表。**

---

## 1. 为什么会“菜单少、表很多”

数据库里除了权限与账号主模型，还会有：
1. Django 框架自带表（迁移、内容类型、Session、Admin 操作日志）。
2. M2M 关系中间表（ManyToMany 自动生成或 Django 权限体系自带）。
3. 只作为正式 API / 服务层支撑、但没有注册到 Admin 的模型或业务表。

所以你会看到：
- Admin 菜单：少量“平台治理与排障时需要直接查看/维护”的模型。
- 数据库：完整的系统运行表集合。

---

## 2. 当前系统的表清单（以当前代码模型为准）

### 2.1 权限系统核心表（当前数据库实例）
1. `auth_users`：账号主表（自定义 `User`）
2. `staff_profiles`：人员档案（1:1 账号）
3. `tenants`：租户主表
4. `tenant_members`：租户成员关系
5. `tenant_member_roles`：租户成员角色绑定
6. `tenant_member_qualifications`：租户成员资质记录
7. `roles`：平台角色目录
8. `permissions`：平台权限目录
9. `role_permission_grants`：角色到权限与 scope 的映射
10. `qualification_types`：平台资质类型目录
11. `auth_audit_logs`：权限与关键操作审计日志

补充：
- 代码中还定义了 `auth_sessions`（正式 Bearer 会话，对应 `apps/access/models.py` 和 `apps/access/migrations/0012_authsession.py`）。
- 当前工作库尚未看到该表；若执行对应迁移，表数量会在本节基础上再增加 1 张。

### 2.2 Django Auth 兼容表
1. `auth_group`：Django Group（当前正式授权链不使用）
2. `auth_permission`：Django 权限字典
3. `auth_group_permissions`：Group 与 Permission 的 M2M 中间表
4. `auth_users_groups`：User 与 Group 的 M2M 中间表（本系统禁用直绑，但表仍存在）
5. `auth_users_user_permissions`：User 与 Permission 的 M2M 中间表（同上）

### 2.3 Django 框架运行表
1. `django_admin_log`：Admin 操作日志
2. `django_content_type`：内容类型映射
3. `django_migrations`：迁移记录
4. `django_session`：Session 会话

### 2.4 业务与 DJI 适配表（当前已落地）
1. `drones`：无人机台账
2. `drone_assignments`：无人机分配关系
3. `routes`：航线
4. `waypoints`：航线内部航点表
5. `missions`：任务
6. `flight_records`：飞行记录
7. `media_files`：媒体文件
8. `dji_workspace_configs`：DJI workspace 与系统执行账号配置
9. `dji_device_indexes`：DJI 共享设备池索引
10. `tenant_route_indexes`：route 发布索引
11. `tenant_mission_indexes`：任务同步索引
12. `tenant_media_indexes`：媒体同步索引

---

## 3. 为什么 Admin 看不到这些表

### 3.1 中间表默认不会作为菜单项出现
例如：
- `auth_group_permissions`
- `auth_users_groups`
- `auth_users_user_permissions`

它们通过模型关系间接维护，不需要单独菜单。

### 3.2 当前权限侧 Admin 只注册了平台治理需要直接查看的模型
在 `apps/access/admin.py` 中，当前注册到 Admin 左侧菜单的模型是：
1. `User`
2. `StaffProfile`
3. `Tenant`
4. `TenantMember`
5. `TenantMemberRole`
6. `TenantMemberQualification`
7. `Role`
8. `Permission`
9. `RolePermissionGrant`
10. `QualificationType`
11. `AuditLog`

其中：
- `TenantMemberRole`、`Role`、`Permission`、`RolePermissionGrant`、`AuditLog` 等使用只读 Admin 展示。
- 关系明细大量通过 inline 展示，例如：`Tenant` 下看成员、`Role` 下看权限映射和成员绑定、`User` 下看 `StaffProfile` 与租户成员关系。

### 3.3 Django 兼容表、框架运行表和业务表未注册到权限 Admin 菜单
例如：
- `auth_group`、`auth_permission` 等 Django 兼容表
- `django_*` 运行表
- `drones`、`missions`、`media_files`、`dji_*`、`tenant_*_indexes` 等业务与适配表

这些表当前不在 `apps/access/admin.py` 的左侧菜单注册范围内，因此不会显示。

---

## 4. 当前左侧菜单条目的来源

当前权限后台左侧菜单来自这些“已注册且允许展示”的模型（菜单名称对应模型 `verbose_name_plural`）：
1. `账号`
2. `人员档案`
3. `租户`
4. `租户成员`
5. `成员角色绑定`
6. `租户成员资质`
7. `平台角色`
8. `平台权限`
9. `角色权限映射`
10. `资质类型`
11. `审计日志`

这些条目是当前 `apps/access/admin.py` 明确注册的结果，不是数据库全部表清单。

---

## 5. 如何自己快速核对（推荐）

```bash
python manage.py shell -c "from django.db import connection; print('\n'.join(sorted(connection.introspection.table_names())))"
```

如果你看到当前数据库里的表集合与上面分类基本一致，就是正常状态。
