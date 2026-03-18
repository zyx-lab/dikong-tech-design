# 租户逻辑模型

- generated_at: 2026-03-19
- entity: tenant

## 数据库

PostgreSQL

---

## 实体主表

| 表名 | 说明 |
|------|------|
| `tenants` | 租户主表 |
| `tenant_members` | 租户成员关系 |
| `roles` | 平台角色目录 |
| `permissions` | 平台权限目录 |
| `role_permission_grants` | 角色默认权限映射 |
| `qualification_types` | 平台资质类型目录 |
| `tenant_member_roles` | 成员角色绑定 |
| `tenant_member_qualifications` | 成员资质记录 |
| `auth_sessions` | 正式 IAM Bearer 会话 |

### tenants
- 主键: `id (BigAutoField)`
- 业务主标识: `code`
- 状态字段: `status`

### tenant_members
- 主键: `id (BigAutoField)`
- 关系唯一键: `(tenant_id, user_id)`
- 租户内编号: `member_no`
- 状态字段: `status`
- 当前正式 API 暴露状态只使用 `ACTIVE / DISABLED`

### tenant_member_roles
- 主键: `id (BigAutoField)`
- 角色字段: `system_role_id`（当前代码字段名，对应 `roles.id`）
- 状态字段: `status`

### tenant_member_qualifications
- 主键: `id (BigAutoField)`
- 状态字段: `status`
- 有效期字段: `valid_from / valid_until`

### auth_sessions
- 主键: `id (BigAutoField)`
- 关联账号: `user_id`
- 会话类型: `session_type`
- Token 生命周期字段:
  - `access_token_expires_at`
  - `refresh_token_expires_at`

---

## 运行态

### 正式 IAM 运行态

| 值 | 含义 |
|----|------|
| `unassigned` | 已注册、可登录，但还没有任何租户成员关系 |
| `tenant_member` | 至少拥有一条租户成员关系 |
| `platform_operator` | 平台工作态账号，只进入 `platform/*` |

补充说明：

1. `superuser` 是技术 root，不属于正式 IAM 运行态集合。
2. `platform_operator` 通过 `User.is_platform_admin = true` 承载。
3. `tenant_member` 与 `platform_operator` 互斥。

---

## 状态机

### Tenant.status

| 值 | 含义 | 可转换到 |
|----|------|----------|
| `0 (DISABLED)` | 禁用 | `1 (ACTIVE)` |
| `1 (ACTIVE)` | 启用 | `0 (DISABLED)` |

### TenantMember.status

| 值 | 含义 | 当前正式 API 是否直接使用 |
|----|------|--------------------------|
| `0 (INVITED)` | 已发出邀请，等待接受 | 否 |
| `1 (ACTIVE)` | 已加入租户 | 是 |
| `2 (REJECTED)` | 用户拒绝邀请 | 否 |
| `3 (EXPIRED)` | 邀请过期 | 否 |
| `4 (REVOKED)` | 邀请撤销 | 否 |
| `5 (DISABLED)` | 成员停用 | 是 |

说明：

1. 当前模型仍保留邀请态枚举与字段。
2. 当前正式 API 已不开放邀请链路。
3. `POST /api/v1/iam/tenant/members` 与 `POST /api/v1/iam/platform/tenants/{tenantId}/initialize-admin` 都直接写入 `ACTIVE`。

### TenantMemberRole.status

| 值 | 含义 |
|----|------|
| `0 (REVOKED)` | 角色绑定无效 |
| `1 (GRANTED)` | 角色绑定有效 |

### TenantMemberQualification.status

| 值 | 含义 |
|----|------|
| `1 (ACTIVE)` | 记录结构合法，可参与业务资格判定 |
| `2 (INVALID)` | 关键字段缺失或结构非法，不参与业务判定 |
| `3 (REVOKED)` | 记录已撤销，不参与业务判定 |

---

## 关系与约束

### 平台目录与租户运行数据

```text
User
 └─1:1─ StaffProfile

Tenant
 └─1:N─ TenantMember
          ├─1:N─ TenantMemberRole ── N:1 ── Role
          └─1:N─ TenantMemberQualification ── N:1 ── QualificationType

Role
 └─1:N─ RolePermissionGrant ── N:1 ── Permission
```

### 授权链

```text
TenantMember(ACTIVE)
  -> TenantMemberRole(GRANTED)
  -> Role(ACTIVE)
  -> RolePermissionGrant
  -> Permission(ACTIVE)
```

### 平台工作态授权链

```text
User(is_platform_admin=true)
  -> Role(code=platform_admin)
  -> RolePermissionGrant
  -> Permission(ACTIVE)
```

### 业务资格链

```text
TenantMember(ACTIVE)
  -> TenantMemberQualification(ACTIVE 且有效)
  -> QualificationType(ACTIVE)
```

### 业务约束

1. `tenants.code` 全局唯一。
2. `tenant_members` 上 `(tenant_id, user_id)` 唯一。
3. `tenant_members` 上 `(tenant_id, member_no)` 在 `member_no` 非空时唯一。
4. `tenant_member_roles` 上 `(tenant_member_id, system_role_id)` 唯一。
5. `role_permission_grants` 上 `(role_id, permission_id)` 唯一。
6. `tenant_member_qualifications.valid_from <= valid_until` 在两者同时存在时必须满足。
7. `platform_admin` 不能分配给租户成员。
8. 业务身份统一通过 `role.code` 判断，例如 `tenant_admin / business_admin / pilot_operator`。
9. `PUT /api/v1/iam/tenant/members/{memberId}/roles` 是角色集合全量替换，不在请求里的角色会被移除。
10. 任何操作都不能让当前租户失去最后一个有效 `tenant_admin`。

---

## 生命周期入口

- `POST /api/v1/iam/session/register`
- `POST /api/v1/iam/session/register-by-phone`
- `GET /api/v1/iam/me/profile`
- `GET /api/v1/iam/me/tenants`
- `GET /api/v1/iam/tenant/me`
- `GET /api/v1/iam/tenant/members`
- `POST /api/v1/iam/tenant/members`
- `GET /api/v1/iam/tenant/members/{memberId}`
- `PATCH /api/v1/iam/tenant/members/{memberId}`
- `PUT /api/v1/iam/tenant/members/{memberId}/roles`
- `POST /api/v1/iam/tenant/members/{memberId}/enable`
- `POST /api/v1/iam/tenant/members/{memberId}/disable`
- `GET /api/v1/iam/tenant/roles`
- `GET /api/v1/iam/tenant/audit-logs`
- `GET /api/v1/iam/platform/permissions`
- `GET /api/v1/iam/platform/roles`
- `GET /api/v1/iam/platform/roles/{roleId}`
- `GET /api/v1/iam/platform/audit-logs`
- `GET /api/v1/iam/platform/tenants`
- `POST /api/v1/iam/platform/tenants`
- `GET /api/v1/iam/platform/tenants/{tenantId}`
- `POST /api/v1/iam/platform/tenants/{tenantId}/enable`
- `POST /api/v1/iam/platform/tenants/{tenantId}/disable`
- `POST /api/v1/iam/platform/tenants/{tenantId}/initialize-admin`

---

## 接口语义

### 创建租户成员
- 功能：把已存在的全局用户直接加入当前租户。
- 路径：`/api/v1/iam/tenant/members`
- 方法：`POST`
- 状态流转：不存在 -> `ACTIVE`
- 有效状态：租户 `ACTIVE`，目标账号已存在且可入租，且当前租户不存在同一 `(tenant, user)` 关系
- 无效状态：账号不存在、账号不可入租、成员关系已存在、角色编码非法
- 业务码：`00000 / C0103 / C0404 / C0201`

### 更新成员显示名
- 功能：更新当前租户成员的租户内显示名称。
- 路径：`/api/v1/iam/tenant/members/{memberId}`
- 方法：`PATCH`
- 有效状态：目标成员属于当前租户
- 无效状态：成员不存在、越租户访问
- 业务码：`00000 / C0404 / A0403`

### 全量替换成员角色
- 功能：把成员角色集合直接替换成请求中的 `roleCodes`
- 路径：`/api/v1/iam/tenant/members/{memberId}/roles`
- 方法：`PUT`
- 有效状态：成员存在，角色编码全部合法
- 无效状态：成员不存在、角色编码非法、会移除最后一个有效租户管理员
- 业务码：`00000 / C0203 / C0404`

### 停用租户成员
- 功能：停用当前租户内成员
- 路径：`/api/v1/iam/tenant/members/{memberId}/disable`
- 方法：`POST`
- 状态流转：`ACTIVE -> DISABLED`
- 有效状态：目标成员存在且当前为 `ACTIVE`
- 无效状态：成员不存在、已停用、会移除最后一个有效租户管理员
- 业务码：`00000 / C0201 / C0203 / C0404`

### 启用租户成员
- 功能：重新启用已停用成员
- 路径：`/api/v1/iam/tenant/members/{memberId}/enable`
- 方法：`POST`
- 状态流转：`DISABLED -> ACTIVE`
- 有效状态：目标成员存在且当前为 `DISABLED`
- 无效状态：成员不存在、已启用
- 业务码：`00000 / C0201 / C0404`

### 初始化租户管理员
- 功能：平台侧为租户补齐首个有效 `tenant_admin`
- 路径：`/api/v1/iam/platform/tenants/{tenantId}/initialize-admin`
- 方法：`POST`
- 状态流转：不存在或非有效管理员 -> `ACTIVE tenant_admin`
- 有效状态：租户 `ACTIVE` 且当前没有任何有效 `tenant_admin`
- 无效状态：租户不存在、租户未启用、租户已存在有效管理员、目标账号不可入租
- 业务码：`00000 / C0201 / C0203 / C0404`

### 已移除的正式语义

以下语义已不再属于正式 API：

- 邀请租户成员
- 确认邀请
- 拒绝邀请
- `me/permissions`
- `set-plan`
- 平台侧全局用户目录
