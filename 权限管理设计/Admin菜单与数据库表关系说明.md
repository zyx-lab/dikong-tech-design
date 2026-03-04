# Admin 菜单与数据库表关系说明

你在 Admin 左侧看到的条目只有几行，这是正常现象。  
**Admin 菜单显示的是“已注册且允许显示的模型”，不是数据库全部表。**

---

## 1. 为什么会“菜单少、表很多”

数据库里除了业务表，还会有：
1. Django 框架自带表（会话、迁移、内容类型、admin 操作日志）。
2. M2M 关系中间表（ManyToMany 自动或显式生成）。
3. 被我们故意隐藏的模型（`get_model_perms` 返回 `{}`）。

所以你会看到：
- Admin 菜单：少量“运营需要直接维护”的模型。
- 数据库：完整的系统运行表集合。

---

## 2. 当前系统的表清单（共 17 张）

### 2.1 权限系统核心表（我们设计的）
1. `auth_users`：账号主表（自定义 User）
2. `staff_profiles`：人员档案（1:1 账号）
3. `staff_types`：身份类型
4. `staff_type_groups`：身份类型到能力组映射
5. `auth_group_permission_scopes`：Group+Permission 的 scope
6. `auth_audit_logs`：授权变更审计日志

### 2.2 Django Auth/RBAC 基础表
1. `auth_group`：能力组（Group）
2. `auth_permission`：权限字典
3. `auth_group_permissions`：Group 与 Permission 的 M2M 中间表
4. `auth_users_groups`：User 与 Group 的 M2M 中间表（本系统禁用直绑，但表仍存在）
5. `auth_users_user_permissions`：User 与 Permission 的 M2M 中间表（同上）

### 2.3 Django 框架运行表
1. `django_admin_log`：Admin 操作日志
2. `django_content_type`：内容类型映射（Permission 依赖）
3. `django_migrations`：迁移记录
4. `django_session`：Session 会话

### 2.4 业务表（当前已落地）
1. `drones`：无人机台账
2. `drone_assignments`：无人机与飞手分配关系

---

## 3. 为什么 Admin 看不到这些表

### 3.1 中间表默认不会作为菜单项出现
例如：
- `auth_group_permissions`
- `auth_users_groups`
- `auth_users_user_permissions`

它们通过 `User` 或 `Group` 编辑页间接维护，不需要单独菜单。

### 3.2 我们主动隐藏了部分模型
在 `apps/access/admin.py` 中，以下 Admin 类返回 `get_model_perms = {}`，因此不在左侧菜单显示：
1. `StaffTypeGroupAdmin`
2. `GroupPermissionScopeAdmin`

原因：降低菜单噪音，改在 `StaffType` / `Group` 页面用 inline 维护。

### 3.3 业务表未注册 Admin 时也不会显示
例如当前 `drones`、`drone_assignments` 主要通过 Business API 维护，未注册到 Admin 菜单。

---

## 4. 你截图中条目的来源

你看到的：
1. `Audit logs`
2. `Staff types`
3. `Users`
4. `组`（`auth_group`）

这些都是“已注册且允许展示”的模型，属于有意设计，不是缺表。

---

## 5. 如何自己快速核对（推荐）

```bash
python manage.py shell -c "from django.db import connection; print('\n'.join(sorted(connection.introspection.table_names())))"
```

如果你看到 17 张表且包含上面清单，就是正常状态。
