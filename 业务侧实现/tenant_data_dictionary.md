# 低空智能巡检平台 - 租户

## 数据库

PostgreSQL

---

## 文档格式说明

本文档为 **数据字典** 类型文档，记录数据库表结构、字段定义、约束和业务规则。

### 更新本文档的指南（大模型用）

当需要更新此文档时，请遵循以下格式：

```
## N. {表名中文名}

**说明**：{表用途简述}

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| {字段名} | {PostgreSQL类型} | {约束} | {默认值} | {字段说明} |

**{某字段} 状态值**：

| 值 | 含义 |
|----|------|
| {枚举值} | {含义} |

**业务规则**：
1. {规则1}
2. {规则2}
```

---

## 阅读说明

本数据字典覆盖当前已落地的业务表：`tenants`、`tenant_members`、`tenant_member_roles`。

---

## 1. tenants（租户主表）

**说明**：存储租户（买家）基本信息，平台级模型，不包含 tenant_id。

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 主键 |
| code | varchar(64) | NOT NULL, UNIQUE | - | 租户编码（唯一） |
| name | varchar(128) | NOT NULL | - | 租户名称 |
| status | smallint | NOT NULL | 1 | 租户状态 |
| plan | varchar(64) | - | - | 套餐类型（预留） |
| remark | varchar(500) | - | - | 备注 |
| created_at | timestamp | NOT NULL | now() | 创建时间 |
| updated_at | timestamp | NOT NULL | now() | 更新时间 |

**status 状态值**：

| 值 | 含义 |
|----|------|
| 0 | 禁用 |
| 1 | 启用 |

**业务规则**：
1. `code` 全局唯一，用于 X-Tenant-Code header 标识。
2. 租户状态为禁用时，该租户所有业务数据不可访问。
3. 租户删除采用软删除（status=0）。

---

## 2. tenant_members（租户成员身份）

**说明**：表示某个账号在某个租户中的成员身份，支持邀请制入租流程。

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 主键 |
| tenant_id | bigint | NOT NULL, FK -> tenants.id | - | 所属租户 |
| user_id | bigint | NOT NULL, FK -> auth_users.id | - | 平台账号 |
| display_name | varchar(128) | NOT NULL | - | 租户内显示名称 |
| staff_no | varchar(64) | - | '' | 工号 |
| phone | varchar(32) | - | '' | 手机号 |
| email | varchar(254) | - | '' | 邮箱 |
| invitation_token | varchar(64) | UNIQUE，可空 | null | 邀请确认令牌 |
| status | smallint | NOT NULL | 0 | 成员状态 |
| joined_at | timestamp | 可空 | null | 确认加入时间 |
| created_at | timestamp | NOT NULL | now() | 创建时间 |
| updated_at | timestamp | NOT NULL | now() | 更新时间 |

**status 状态值**：

| 值 | 含义 |
|----|------|
| 0 | 待确认（pending） |
| 1 | 已加入（active） |
| 2 | 已禁用（disabled） |

**业务规则**：
1. `(tenant_id, user_id)` 唯一，同一账号不能重复加入同一租户。
2. 邀请制创建成员时状态为 `pending`，并生成 `invitation_token`。
3. `joined_at` 仅在成员确认加入后写入。

---

## 3. tenant_member_roles（租户成员角色绑定）

**说明**：记录租户成员与平台固定角色之间的绑定关系。

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 主键 |
| tenant_member_id | bigint | NOT NULL, FK -> tenant_members.id | - | 租户成员 |
| system_role_id | bigint | NOT NULL, FK -> system_roles.id | - | 平台固定角色 |
| status | smallint | NOT NULL | 1 | 绑定状态 |
| created_at | timestamp | NOT NULL | now() | 创建时间 |
| updated_at | timestamp | NOT NULL | now() | 更新时间 |

**status 状态值**：

| 值 | 含义 |
|----|------|
| 0 | 禁用 |
| 1 | 启用 |

**业务规则**：
1. `(tenant_member_id, system_role_id)` 唯一。
2. 邀请接口会在创建 pending 成员时预写入角色绑定。

---

## 4. 关系与外键

1. `tenant_members.tenant_id -> tenants.id`
2. `tenant_members.user_id -> auth_users.id`
3. `tenant_member_roles.tenant_member_id -> tenant_members.id`
4. `tenant_member_roles.system_role_id -> system_roles.id`

---

## 5. 与实现对应

1. 模型：`apps/access/models.py` - `Tenant`
2. 模型：`apps/access/models.py` - `TenantMember` / `TenantMemberRole`
3. 序列化与校验：`apps/access/serializers.py`
4. 接口：`apps/access/views.py` - `TenantViewSet` / `TenantMemberInviteView`

---

## 6. 业务响应码字典（Business API）

说明：业务 API 响应体包含 `business_code`（主业务码）与 `business_detail_code`（细分原因码）。

| business_code | 典型 HTTP | 语义 |
| ------ | ------ | ------ |
| SUCCESS | 200 / 201 | 业务处理成功 |
| INVALID_PARAMS | 400 | 请求参数校验失败 |
| PERMISSION_DENIED | 401 / 403 | 身份或权限不足 |
| RESOURCE_NOT_FOUND | 404 | 目标资源不存在 |
| STATE_CONFLICT | 409 | 状态机冲突 |
| IDEMPOTENT_DUPLICATE | 409 | 幂等重复提交（如唯一键冲突） |

| business_detail_code | 语义 |
| ------ | ------ |
| OK | 成功 |
| NOT_AUTHENTICATED | 未登录或认证信息缺失 |
| FORBIDDEN | 已登录但无权限 |
| NOT_FOUND | 资源不存在 |
| VALIDATION_ERROR | 参数校验失败 |
| STATE_CONFLICT | 业务状态冲突 |
| DUPLICATE_REQUEST | 幂等重复请求 |
| TENANT_NOT_FOUND | 邀请时目标租户不存在 |
| USER_NOT_FOUND | 邀请时目标用户不存在 |
| TENANT_MEMBER_EXISTS | 租户成员关系已存在 |
| INVITATION_NOT_FOUND | invitation token 不存在 |
| INVITATION_NOT_ALLOWED | invitation token 不属于当前登录用户 |
| INVITATION_ALREADY_CONFIRMED | 邀请已确认，重复提交 |
| INVITATION_STATUS_INVALID | invitation 对应成员状态不是 pending |

当前实现中，`POST /internal/auth/tenants`、`POST /internal/auth/tenant-members/invite` 与 `POST /internal/auth/tenant-members/confirm-invitation` 都返回 `business_code + business_detail_code`。
