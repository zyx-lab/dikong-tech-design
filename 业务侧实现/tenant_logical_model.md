# 租户逻辑模型

- generated_at: 2026-03-14
- entity: tenant

## 数据库

PostgreSQL

---

## 实体主表

| 表名 | 说明 |
|------|------|
| tenants | 租户主表 |
| tenant_members | 租户成员关系 |
| roles | 平台角色目录 |
| permissions | 平台权限目录 |
| role_permission_grants | 角色默认权限映射 |
| qualification_types | 平台资质类型目录 |
| tenant_member_roles | 成员角色绑定 |
| tenant_member_qualifications | 成员资质记录 |

### tenants
- 主键: id (BigAutoField)
- 状态字段: status

### tenant_members
- 主键: id (BigAutoField)
- 状态字段: status
- 邀请关键字段: invitation_token / invited_at / expires_at / responded_at / joined_at

### tenant_member_roles
- 主键: id (BigAutoField)
- 状态字段: status

### tenant_member_qualifications
- 主键: id (BigAutoField)
- 状态字段: status
- 有效期字段: valid_from / valid_until

---

## 状态机

### Tenant.status

| 值 | 含义 | 可转换到 |
|----|------|----------|
| 0 (DISABLED) | 禁用 | 1 (ACTIVE) |
| 1 (ACTIVE) | 启用 | 0 (DISABLED) |

### TenantMember.status

| 值 | 含义 | 可转换到 |
|----|------|----------|
| 0 (INVITED) | 已发出邀请，等待接受 | 1 (ACTIVE), 2 (REJECTED), 3 (EXPIRED), 4 (REVOKED) |
| 1 (ACTIVE) | 已加入租户 | 5 (DISABLED) |
| 2 (REJECTED) | 用户已拒绝邀请 | - |
| 3 (EXPIRED) | 邀请已过期 | - |
| 4 (REVOKED) | 邀请被管理员撤销 | - |
| 5 (DISABLED) | 成员已禁用 | 1 (ACTIVE) |

### TenantMemberRole.status

| 值 | 含义 |
|----|------|
| 0 (REVOKED) | 角色绑定无效 |
| 1 (GRANTED) | 角色绑定有效 |

### TenantMemberQualification.status

| 值 | 含义 |
|----|------|
| 1 (ACTIVE) | 记录结构合法，可参与业务资格判定 |
| 2 (INVALID) | 关键字段缺失或结构非法，不参与业务判定 |
| 3 (REVOKED) | 记录已撤销，不参与业务判定 |

---

## 关系与约束

### 授权链

```text
User(ACTIVE)
  -> Tenant(ACTIVE)
  -> TenantMember(ACTIVE)
  -> TenantMemberRole(GRANTED)
  -> Role(ACTIVE)
  -> RolePermissionGrant
  -> Permission(ACTIVE)
```

### 业务资格链

```text
User(ACTIVE)
  -> Tenant(ACTIVE)
  -> TenantMember(ACTIVE)
  -> TenantMemberQualification(ACTIVE 且有效)
  -> QualificationType(ACTIVE)
```

### 业务约束

1. `tenants.code` 全局唯一。
2. `tenant_members` 上 `(tenant_id, user_id)` 唯一。
3. `tenant_members` 上 `(tenant_id, member_no)` 在 `member_no` 非空时唯一。
4. `tenant_members.invitation_token` 在非空时唯一。
5. `tenant_member_roles` 上 `(tenant_member_id, system_role_id)` 唯一。
6. `role_permission_grants` 上 `(role_id, permission_id)` 唯一。
7. `tenant_member_qualifications.valid_from <= valid_until` 在两者同时存在时必须满足。
8. 岗位概念已收敛为角色，业务代码通过 `role.code` 判断“飞手/调度员/租户管理员”等身份。

---

## 生命周期入口

- `POST /internal/auth/tenants`
- `POST /internal/auth/tenants/{id}/disable`
- `POST /internal/auth/tenants/{id}/enable`
- `POST /internal/auth/tenants/{id}/initialize-admin`
- `POST /internal/auth/tenants/{id}/set-plan`
- `POST /internal/auth/users/register`
- `POST /internal/auth/users/register/by-phone`
- `POST /internal/auth/tenant-members`
- `POST /internal/auth/tenant-members/invite`
- `POST /internal/auth/tenant-members/confirm-invitation`
- `POST /internal/auth/me/invitations/reject`
- `POST /internal/auth/tenant-members/{id}/disable`
- `POST /internal/auth/tenant-members/{id}/enable`
- `POST /internal/auth/tenant-members/{id}/roles`
- `GET /internal/auth/me/invitations`
- `GET /internal/auth/me/tenants`
- `GET /internal/auth/me/permissions`
- `GET /internal/auth/tenant-audit-logs`

---

## 接口语义

### 邀请租户成员
- 功能：向已存在平台账号发起加入租户邀请，并预绑定角色。
- 路径：`/internal/auth/tenant-members/invite`
- 方法：`POST`
- 状态流转：不存在 -> `INVITED`
- 有效状态：租户为 `ACTIVE`，目标账号未加入该租户
- 无效状态：租户不存在、账号不存在、成员关系已存在、角色编码非法
- 业务码：`SUCCESS / INVALID_PARAMS / RESOURCE_NOT_FOUND / STATE_CONFLICT`

### 确认租户邀请
- 功能：被邀请用户接受邀请并激活成员关系。
- 路径：`/internal/auth/tenant-members/confirm-invitation`
- 方法：`POST`
- 状态流转：`INVITED -> ACTIVE`
- 有效状态：邀请属于当前用户，且仍处于 `INVITED`
- 无效状态：邀请不存在、邀请不属于当前用户、邀请已过期或已不在 `INVITED`
- 业务码：`SUCCESS / INVALID_PARAMS / RESOURCE_NOT_FOUND / PERMISSION_DENIED / STATE_CONFLICT`

### 拒绝租户邀请
- 功能：被邀请用户拒绝邀请。
- 路径：`/internal/auth/me/invitations/reject`
- 方法：`POST`
- 状态流转：`INVITED -> REJECTED`
- 有效状态：邀请属于当前用户，且仍处于 `INVITED`
- 无效状态：邀请不存在、邀请不属于当前用户、邀请已过期或已不在 `INVITED`
- 业务码：`SUCCESS / INVALID_PARAMS / RESOURCE_NOT_FOUND / PERMISSION_DENIED / STATE_CONFLICT`

### 停用租户成员
- 功能：管理员停用已激活成员，或撤销尚未接受的邀请。
- 路径：`/internal/auth/tenant-members/{id}/disable`
- 方法：`POST`
- 状态流转：`ACTIVE -> DISABLED`；`INVITED -> REVOKED`
- 有效状态：目标成员存在且状态为 `ACTIVE` 或 `INVITED`
- 无效状态：目标成员不存在；已是 `DISABLED / REVOKED`；状态不允许停用
- 业务码：`SUCCESS / RESOURCE_NOT_FOUND / IDEMPOTENT_DUPLICATE / STATE_CONFLICT`

### 启用租户成员
- 功能：重新启用被停用成员。
- 路径：`/internal/auth/tenant-members/{id}/enable`
- 方法：`POST`
- 状态流转：`DISABLED -> ACTIVE`
- 有效状态：目标成员存在且状态为 `DISABLED`
- 无效状态：目标成员不存在；已是 `ACTIVE`；状态不允许启用
- 业务码：`SUCCESS / RESOURCE_NOT_FOUND / IDEMPOTENT_DUPLICATE / STATE_CONFLICT`
