# Django 内部表附录（权限系统）

## 目的
这个附录只解释“框架运行辅助表”，用于排查数据库来源和理解 Django 机制。  
与授权主链路直接相关的 Django 内建表（`auth_group`、`auth_permission`、`auth_group_permissions`）已经纳入主文档：
- [`authz_schema.dbml`](./authz_schema.dbml)
- [`authz_logical_model.md`](./authz_logical_model.md)

## 什么时候需要看这个附录
1. 你在排查“为什么数据库里出现这些表”。  
2. 你在改 Django 底层（认证、会话、迁移、管理后台）代码。  
3. 你在做框架级排障（不是业务授权规则排障）。

## 1) `django_content_type`
- 作用：记录“应用 + 模型”的类型字典。
- 典型字段：`app_label`、`model`。
- 与权限关系：`auth_permission.content_type_id` 依赖它。

## 2) `django_migrations`
- 作用：记录迁移执行历史。
- 用途：判断当前数据库结构处于哪个迁移版本。

## 3) `django_session`
- 作用：存储会话数据（Session 登录态）。
- 用途：浏览器登录 Django Admin、DRF Session 鉴权时会用到。

## 4) `django_admin_log`
- 作用：记录 Django Admin 的对象变更日志。
- 用途：审计后台“谁改了什么”，和业务审计日志互补。

## 5) 说明
1. 这些表属于框架运行基础设施，不是授权模型主干。  
2. 修改权限模型时，优先看主文档，不需要先理解本附录全部内容。
