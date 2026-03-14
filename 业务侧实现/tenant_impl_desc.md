# 租户实现说明

- generated_at: 2026-03-14
- entity: tenant

## 数据模型

当前实现已经收敛到“平台目录 + 租户运行数据”两层：

### 平台目录

- `Role`
- `Permission`
- `RolePermissionGrant`
- `QualificationType`

平台目录由平台统一维护，租户管理员不可修改。

### 租户运行数据

- `Tenant`
- `TenantMember`
- `TenantMemberRole`
- `TenantMemberQualification`

授权链只认：

```text
TenantMember(ACTIVE) -> TenantMemberRole(GRANTED) -> RolePermissionGrant -> Permission
```

业务资格链只认：

```text
TenantMember(ACTIVE) -> TenantMemberQualification(有效) -> QualificationType
```

---

## API 实现

### 1. 创建租户
- 功能：创建租户。
- 路径：`/internal/auth/tenants`
- 方法：`POST`
- 权限：`access.manage_tenant`
- 请求体：`code`、`name` 必填
- 响应：创建成功返回租户基本信息
- 业务码：`SUCCESS / INVALID_PARAMS`

### 2. 停用 / 启用租户
- 功能：切换租户可用状态。
- 路径：`/internal/auth/tenants/{id}/disable`
- 路径：`/internal/auth/tenants/{id}/enable`
- 方法：`POST`
- 权限：`access.manage_tenant`
- 业务码：`SUCCESS / RESOURCE_NOT_FOUND / IDEMPOTENT_DUPLICATE`

### 3. 初始化租户管理员
- 功能：为指定租户创建或激活首个 `tenant_admin` 成员，并绑定角色。
- 路径：`/internal/auth/tenants/{id}/initialize-admin`
- 方法：`POST`
- 权限：`access.manage_tenant`
- 请求体：`user_id` 必填；`display_name`、`member_no`、`qualifications` 可选
- 响应：返回 `member_id / tenant_id / user_id / roles / status`
- 业务码：`SUCCESS / INVALID_PARAMS / RESOURCE_NOT_FOUND / STATE_CONFLICT / IDEMPOTENT_DUPLICATE`

### 4. 配置租户套餐
- 功能：设置租户套餐标识。
- 路径：`/internal/auth/tenants/{id}/set-plan`
- 方法：`POST`
- 权限：`access.manage_tenant`
- 请求体：`plan`
- 业务码：`SUCCESS / INVALID_PARAMS / RESOURCE_NOT_FOUND / IDEMPOTENT_DUPLICATE`

### 5. 平台账号注册
- 功能：创建平台账号与全局 `StaffProfile`。
- 路径：`/internal/auth/users/register`
- 方法：`POST`
- 权限：公开接口
- 请求体：`username / password / name / phone`
- 业务码：`SUCCESS / INVALID_PARAMS / IDEMPOTENT_DUPLICATE`

### 6. 手机号注册
- 功能：通过 mock 验证码完成平台注册。
- 路径：`/internal/auth/users/register/by-phone`
- 方法：`POST`
- 权限：公开接口
- 请求体：`phone / sms_code`
- 说明：当前仅支持固定验证码 `123456`
- 业务码：`SUCCESS / INVALID_PARAMS / IDEMPOTENT_DUPLICATE`

### 7. 直接创建租户成员
- 功能：跳过邀请，直接创建 `ACTIVE` 成员。
- 路径：`/internal/auth/tenant-members`
- 方法：`POST`
- 权限：`access.manage_tenant_member`
- 请求体：`tenant_id / user_id` 必填；可附带 `display_name / member_no / role_codes / qualifications`
- 业务码：`SUCCESS / INVALID_PARAMS`

### 8. 邀请租户成员
- 功能：向已存在平台账号发出租户邀请，并预绑定角色。
- 路径：`/internal/auth/tenant-members/invite`
- 方法：`POST`
- 权限：`access.manage_tenant_member`
- 请求体：`tenant_id / user_id` 必填；可附带 `display_name / role_codes / qualifications`
- 实现要点：
  1. 仅允许邀请已存在 `User`
  2. 新建 `TenantMember(status=INVITED)`
  3. 写入 `invitation_token / invited_at / expires_at`
  4. 预绑定 `TenantMemberRole(status=GRANTED)`
- 业务码：`SUCCESS / INVALID_PARAMS / RESOURCE_NOT_FOUND / STATE_CONFLICT`

### 9. 确认邀请
- 功能：当前登录用户接受邀请。
- 路径：`/internal/auth/tenant-members/confirm-invitation`
- 方法：`POST`
- 权限：登录用户
- 请求体：`invitation_token`
- 状态流转：`INVITED -> ACTIVE`
- 实现要点：
  1. 先检查邀请是否属于当前用户
  2. 先执行过期清理
  3. 激活后清空邀请元数据
  4. 保留预绑定角色
- 业务码：`SUCCESS / INVALID_PARAMS / RESOURCE_NOT_FOUND / PERMISSION_DENIED / STATE_CONFLICT`

### 10. 拒绝邀请
- 功能：当前登录用户拒绝邀请。
- 路径：`/internal/auth/me/invitations/reject`
- 方法：`POST`
- 权限：登录用户
- 请求体：`invitation_token`
- 状态流转：`INVITED -> REJECTED`
- 实现要点：
  1. 只清空 `invitation_token`
  2. 保留 `invited_by_user / invited_at / expires_at`
  3. 写入 `responded_at`

### 11. 停用 / 启用成员
- 功能：管理员停用成员，或重新启用被停用成员。
- 路径：`/internal/auth/tenant-members/{id}/disable`
- 路径：`/internal/auth/tenant-members/{id}/enable`
- 方法：`POST`
- 权限：`access.manage_tenant_member`
- 状态流转：
  - `ACTIVE -> DISABLED`
  - `INVITED -> REVOKED`
  - `DISABLED -> ACTIVE`

### 12. 当前用户租户列表
- 功能：获取当前用户所有 `ACTIVE` 成员关系对应的租户列表。
- 路径：`/internal/auth/me/tenants`
- 方法：`GET`
- 权限：登录用户

### 13. 当前用户待处理邀请列表
- 功能：获取当前用户所有 `INVITED` 邀请。
- 路径：`/internal/auth/me/invitations`
- 方法：`GET`
- 权限：登录用户
- 实现要点：请求前会先将已过期邀请批量切成 `EXPIRED`

### 14. 当前租户权限快照
- 功能：返回当前租户上下文下，当前用户聚合后的权限列表与 scope。
- 路径：`/internal/auth/me/permissions`
- 方法：`GET`
- 权限：登录用户
- 响应核心字段：
  - `tenant_code`
  - `roles`
  - `items[].permission`
  - `items[].scope`

### 15. 平台目录只读接口
- 功能：查看平台权限目录与角色目录。
- 路径：`/internal/auth/permissions`
- 路径：`/internal/auth/roles`
- 方法：`GET`
- 权限：
  - `access.view_permission_catalog`
  - `access.view_role`

---

## 审计动作

当前实现会为以下动作写入审计日志：

- `USER_REGISTER`
- `USER_REGISTER_BY_PHONE`
- `USER_CREATE`
- `USER_UPDATE`
- `TENANT_CREATE`
- `TENANT_DISABLE`
- `TENANT_ENABLE`
- `TENANT_PLAN_CHANGE`
- `TENANT_ADMIN_INITIALIZE`
- `TENANT_MEMBER_CREATE`
- `TENANT_MEMBER_INVITE`
- `TENANT_MEMBER_CONFIRM_INVITATION`
- `TENANT_MEMBER_REJECT_INVITATION`
- `TENANT_MEMBER_DISABLE`
- `TENANT_MEMBER_ENABLE`
- `TENANT_MEMBER_DELETE`

---

## 实现约束

1. 不再使用 `GroupPermissionScope / SystemRoleGroup / TenantMemberPosition`。
2. 岗位判断统一通过 `role.code` 完成。
3. 资质判断统一通过 `QualificationType + TenantMemberQualification` 完成。
4. 平台管理员和租户管理员负责修正脏数据，不提供复杂的自动兼容层。
