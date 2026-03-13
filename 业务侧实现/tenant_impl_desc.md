# 租户实现说明

- generated_at: 2026-03-12
- updated_at: 2026-03-12
- entity: tenant

## 数据库

PostgreSQL

---

## 文档格式说明

本文档为 **实现描述** 类型文档，记录 API 实现、数据模型、审计动作等。

### 更新本文档的指南（大模型用）

当需要更新此文档时，请遵循以下格式：

```
## N. {模块名}

### N.X API / 功能名称
- 功能：{功能描述}
- 路径：{API路径}
- 方法：{HTTP方法}
- 权限：{所需权限}
- 请求体：{请求格式}
- 响应：{响应格式}
- 业务码：{返回的业务码}
```

---

## 数据模型

### Tenant 表 (tenants)

| 字段 | 类型 | 说明 |
|-----|------|------|
| id | BigAutoField | 主键 |
| code | CharField(64) | 租户编码，唯一 |
| name | CharField(128) | 租户名称 |
| status | SmallIntegerField | 状态：0=禁用，1=启用 |
| plan | CharField(64) | 套餐类型（预留） |
| remark | CharField(500) | 备注 |
| created_at | DateTimeField | 创建时间 |
| updated_at | DateTimeField | 更新时间 |

### TenantStatus 枚举
- DISABLED = 0, "disabled"
- ENABLED = 1, "enabled"

### TenantMember 表 (tenant_members)

| 字段 | 类型 | 说明 |
|-----|------|------|
| id | BigAutoField | 主键 |
| tenant | ForeignKey(Tenant) | 所属租户 |
| user | ForeignKey(User) | 被邀请或已加入的账号 |
| display_name | CharField(128) | 租户内显示名称 |
| staff_no | CharField(64) | 工号 |
| phone | CharField(32) | 手机号 |
| email | EmailField | 邮箱 |
| invitation_token | CharField(64) | 邀请确认令牌，邀请制流程使用 |
| status | SmallIntegerField | 状态：0=pending，1=active，2=disabled |
| joined_at | DateTimeField | 确认加入时间，pending 时为空 |
| created_at | DateTimeField | 创建时间 |
| updated_at | DateTimeField | 更新时间 |

### TenantMemberStatus 枚举
- PENDING = 0, "pending"
- ACTIVE = 1, "active"
- DISABLED = 2, "disabled"

---

## API 实现

### 1. 创建租户

- 功能：创建一个新租户
- 路径：`/internal/auth/tenants`
- 方法：`POST`
- 权限：`access.manage_tenant`
- 请求体：
```json
{
  "code": "tenant_code",
  "name": "租户名称"
}
```
- 响应（成功，201）：
```json
{
  "business_code": "SUCCESS",
  "business_detail_code": "OK",
  "id": 1,
  "code": "tenant_code",
  "name": "租户名称",
  "status": 1
}
```
- 响应（参数错误，400）：
```json
{
  "business_code": "INVALID_PARAMS",
  "business_detail_code": "VALIDATION_ERROR"
}
```

### 2. 停用租户

- 功能：平台管理员停用指定租户
- 路径：`/internal/auth/tenants/{id}/disable`
- 方法：`POST`
- 权限：`access.manage_tenant`
- 请求体：空
- 响应（成功，200）：
```json
{
  "business_code": "SUCCESS",
  "business_detail_code": "OK",
  "id": 1,
  "code": "tenant_code",
  "name": "租户名称",
  "status": 0
}
```
- 响应（资源不存在，404）：
```json
{
  "business_code": "RESOURCE_NOT_FOUND",
  "business_detail_code": "TENANT_NOT_FOUND"
}
```
- 响应（幂等重复，409）：
```json
{
  "business_code": "IDEMPOTENT_DUPLICATE",
  "business_detail_code": "TENANT_ALREADY_DISABLED"
}
```
- 实现说明：
  1. 按 `id` 查找目标租户
  2. 仅允许 `ENABLED -> DISABLED`
  3. 已禁用租户重复停用返回幂等重复
  4. 成功停用后记录 `TENANT_DISABLE` 平台级审计日志

### 3. 启用租户

- 功能：平台管理员启用指定租户
- 路径：`/internal/auth/tenants/{id}/enable`
- 方法：`POST`
- 权限：`access.manage_tenant`
- 请求体：空
- 响应（成功，200）：
```json
{
  "business_code": "SUCCESS",
  "business_detail_code": "OK",
  "id": 1,
  "code": "tenant_code",
  "name": "租户名称",
  "status": 1
}
```
- 响应（资源不存在，404）：
```json
{
  "business_code": "RESOURCE_NOT_FOUND",
  "business_detail_code": "TENANT_NOT_FOUND"
}
```
- 响应（幂等重复，409）：
```json
{
  "business_code": "IDEMPOTENT_DUPLICATE",
  "business_detail_code": "TENANT_ALREADY_ENABLED"
}
```
- 实现说明：
  1. 按 `id` 查找目标租户
  2. 仅允许 `DISABLED -> ENABLED`
  3. 已启用租户重复启用返回幂等重复
  4. 成功启用后记录 `TENANT_ENABLE` 平台级审计日志

### 4. 初始化租户管理员

- 功能：平台管理员为指定租户初始化首个 `tenant_admin`
- 路径：`/internal/auth/tenants/{id}/initialize-admin`
- 方法：`POST`
- 权限：`access.manage_tenant`
- 请求体：
```json
{
  "user_id": 2,
  "display_name": "租户管理员A",
  "staff_no": "TA-001",
  "phone": "13800000000",
  "email": "admin@example.com"
}
```
- 响应（成功，200）：
```json
{
  "business_code": "SUCCESS",
  "business_detail_code": "OK",
  "member_id": 1,
  "tenant_id": 1,
  "user_id": 2,
  "roles": ["tenant_admin"],
  "status": 1,
  "message": "租户管理员初始化成功"
}
```
- 响应（参数错误，400）：
```json
{
  "business_code": "INVALID_PARAMS",
  "business_detail_code": "VALIDATION_ERROR"
}
```
- 响应（资源不存在，404）：
```json
{
  "business_code": "RESOURCE_NOT_FOUND",
  "business_detail_code": "TENANT_NOT_FOUND"
}
```
- 响应（状态冲突，409）：
```json
{
  "business_code": "STATE_CONFLICT",
  "business_detail_code": "TENANT_STATUS_INVALID"
}
```
- 响应（幂等重复，409）：
```json
{
  "business_code": "IDEMPOTENT_DUPLICATE",
  "business_detail_code": "TENANT_ADMIN_ALREADY_INITIALIZED"
}
```
- 实现说明：
  1. 平台侧按 `tenant_id` 定位租户，且要求租户处于 `ENABLED`
  2. 仅允许在当前租户尚未存在有效 `tenant_admin` 时执行一次初始化
  3. 若目标用户已存在成员记录，则直接激活成员并绑定 `tenant_admin`
  4. 若目标用户尚未入租，则创建 `TenantMember(status=ACTIVE)` 并绑定 `tenant_admin`
  5. 成功后记录 `TENANT_ADMIN_INITIALIZE` 平台级审计日志

### 5. 配置租户套餐

- 功能：平台管理员为指定租户设置套餐标识
- 路径：`/internal/auth/tenants/{id}/set-plan`
- 方法：`POST`
- 权限：`access.manage_tenant`
- 请求体：
```json
{
  "plan": "enterprise"
}
```
- 响应（成功，200）：
```json
{
  "business_code": "SUCCESS",
  "business_detail_code": "OK",
  "id": 1,
  "code": "tenant_code",
  "name": "租户名称",
  "status": 1,
  "plan": "enterprise"
}
```
- 响应（参数错误，400）：
```json
{
  "business_code": "INVALID_PARAMS",
  "business_detail_code": "VALIDATION_ERROR"
}
```
- 响应（资源不存在，404）：
```json
{
  "business_code": "RESOURCE_NOT_FOUND",
  "business_detail_code": "TENANT_NOT_FOUND"
}
```
- 响应（幂等重复，409）：
```json
{
  "business_code": "IDEMPOTENT_DUPLICATE",
  "business_detail_code": "TENANT_PLAN_UNCHANGED"
}
```
- 实现说明：
  1. 平台侧按 `tenant_id` 定位租户
  2. 请求体必须提供非空 `plan`
  3. 若提交的套餐值与当前值相同，则返回幂等重复
  4. 成功更新后记录 `TENANT_PLAN_CHANGE` 平台级审计日志

### 6. 平台注册账号

- 功能：终端用户在平台侧自助注册账号，等待后续租户邀请加入
- 路径：`/internal/auth/users/register`
- 方法：`POST`
- 权限：匿名可调用
- 请求体：
```json
{
  "username": "zhangsan",
  "password": "xxx",
  "name": "张三",
  "phone": "13800138000"
}
```
- 响应（成功，201）：
```json
{
  "business_code": "SUCCESS",
  "business_detail_code": "OK",
  "user_id": 1,
  "username": "zhangsan",
  "message": "注册成功，请等待租户管理员邀请"
}
```
- 响应（参数错误，400）：
```json
{
  "business_code": "INVALID_PARAMS",
  "business_detail_code": "VALIDATION_ERROR"
}
```
- 响应（幂等重复，409）：
```json
{
  "business_code": "IDEMPOTENT_DUPLICATE",
  "business_detail_code": "USERNAME_ALREADY_EXISTS"
}
```
- 实现说明：
  1. 接口只创建平台账号，不直接创建任何租户成员关系
  2. 当前实现会同步创建占位 `StaffProfile`，并挂到零权限的 `pending_user` staff type
  3. “待邀请”状态通过用户尚无 `ACTIVE` 租户成员关系表达，不额外引入新的用户状态枚举
  4. 成功后记录 `USER_REGISTER` 平台级审计日志

### 7. 手机号注册平台账号

- 功能：终端用户通过手机号与短信验证码完成平台注册
- 路径：`/internal/auth/users/register/by-phone`
- 方法：`POST`
- 权限：匿名可调用
- 请求体：
```json
{
  "phone": "13800138000",
  "sms_code": "123456"
}
```
- 响应（成功，201）：
```json
{
  "business_code": "SUCCESS",
  "business_detail_code": "OK",
  "user_id": 1,
  "username": "13800138000",
  "message": "手机号注册成功，请等待租户管理员邀请"
}
```
- 响应（参数错误，400）：
```json
{
  "business_code": "INVALID_PARAMS",
  "business_detail_code": "VALIDATION_ERROR"
}
```
- 响应（幂等重复，409）：
```json
{
  "business_code": "IDEMPOTENT_DUPLICATE",
  "business_detail_code": "PHONE_ALREADY_EXISTS"
}
```
- 实现说明：
  1. 当前实现为 mock 验证码方案，固定接受 `sms_code=123456`
  2. 接口会将手机号写入 `username`，并同步创建占位 `StaffProfile`
  3. 当前实现会生成内部随机密码，仅用于打通注册链路；真实短信服务、正式手机号登录/设密流程待后续扩展
  4. 成功后同样记录 `USER_REGISTER` 平台级审计日志，并在审计数据中标记 `register_channel=phone`

### 8. 租户上下文解析

- 功能：从 HTTP Header 解析当前租户
- 中间件：`TenantContextMiddleware`
- Header：`X-Tenant-Code`
- 实现逻辑：
  1. 读取 `X-Tenant-Code` header
  2. 查询 `Tenant` 表，条件：`code=header值 AND status=ENABLED`
  3. 找到则设置 `request.tenant_context = tenant`
  4. 未找到或已禁用则返回 403

### 9. 邀请租户成员

- 功能：向已注册平台账号发起加入租户邀请，并预绑定初始角色
- 路径：`/internal/auth/tenant-members/invite`
- 方法：`POST`
- 权限：`access.manage_tenant_member`
- 请求体：
```json
{
  "tenant_id": 1,
  "user_id": 2,
  "display_name": "张三",
  "roles": ["pilot_operator"]
}
```
- 响应（成功，201）：
```json
{
  "business_code": "SUCCESS",
  "business_detail_code": "OK",
  "member_id": 1,
  "message": "邀请发送成功"
}
```
- 响应（参数错误，400）：
```json
{
  "business_code": "INVALID_PARAMS",
  "business_detail_code": "VALIDATION_ERROR"
}
```
- 响应（资源不存在，404）：
```json
{
  "business_code": "RESOURCE_NOT_FOUND",
  "business_detail_code": "TENANT_NOT_FOUND"
}
```
- 响应（状态冲突，409）：
```json
{
  "business_code": "STATE_CONFLICT",
  "business_detail_code": "TENANT_MEMBER_EXISTS"
}
```
- 实现说明：
  1. 校验 `tenant_id` 与 `user_id` 对应资源存在
  2. 校验当前 `(tenant_id, user_id)` 尚未存在成员关系
  3. 创建 `TenantMember(status=PENDING)`，并生成 `invitation_token`
  4. 为成员写入初始 `TenantMemberRole`
  5. 记录 `TENANT_MEMBER_INVITE` 租户级审计日志

### 10. 确认租户邀请

- 功能：被邀请用户确认 invitation token，正式加入租户并激活已有角色绑定
- 路径：`/internal/auth/tenant-members/confirm-invitation`
- 方法：`POST`
- 权限：已登录用户
- 请求体：
```json
{
  "invitation_token": "token-confirm-001"
}
```
- 响应（成功，200）：
```json
{
  "business_code": "SUCCESS",
  "business_detail_code": "OK",
  "member_id": 1,
  "roles": ["route_planner"],
  "message": "您已成功加入租户"
}
```
- 响应（资源不存在，404）：
```json
{
  "business_code": "RESOURCE_NOT_FOUND",
  "business_detail_code": "INVITATION_NOT_FOUND"
}
```
- 响应（权限不足，403）：
```json
{
  "business_code": "PERMISSION_DENIED",
  "business_detail_code": "INVITATION_NOT_ALLOWED"
}
```
- 响应（幂等重复，409）：
```json
{
  "business_code": "IDEMPOTENT_DUPLICATE",
  "business_detail_code": "INVITATION_ALREADY_CONFIRMED"
}
```
- 响应（状态冲突，409）：
```json
{
  "business_code": "STATE_CONFLICT",
  "business_detail_code": "INVITATION_STATUS_INVALID"
}
```
- 实现说明：
  1. 用 `invitation_token` 定位租户成员
  2. 仅允许被邀请本人确认当前邀请
  3. `pending -> active` 时写入 `joined_at`
  4. 返回当前已预绑定的角色编码列表
  5. 记录 `TENANT_MEMBER_CONFIRM_INVITATION` 租户级审计日志

### 11. 停用租户成员

- 功能：停用指定租户成员
- 路径：`/internal/auth/tenant-members/{id}/disable`
- 方法：`POST`
- 权限：`access.manage_tenant_member`
- 请求体：空
- 响应（成功，200）：
```json
{
  "business_code": "SUCCESS",
  "business_detail_code": "OK",
  "member_id": 1,
  "tenant_id": 1,
  "user_id": 2,
  "status": 2
}
```
- 响应（资源不存在，404）：
```json
{
  "business_code": "RESOURCE_NOT_FOUND",
  "business_detail_code": "TENANT_MEMBER_NOT_FOUND"
}
```
- 响应（幂等重复，409）：
```json
{
  "business_code": "IDEMPOTENT_DUPLICATE",
  "business_detail_code": "TENANT_MEMBER_ALREADY_DISABLED"
}
```
- 实现说明：
  1. 按 `id` 查找目标 `TenantMember`
  2. 允许 `pending -> disabled` 与 `active -> disabled`
  3. 已禁用成员重复停用返回幂等重复
  4. 成功停用后记录 `TENANT_MEMBER_DISABLE` 租户级审计日志

### 12. 启用租户成员

- 功能：启用指定租户成员
- 路径：`/internal/auth/tenant-members/{id}/enable`
- 方法：`POST`
- 权限：`access.manage_tenant_member`
- 请求体：空
- 响应（成功，200）：
```json
{
  "business_code": "SUCCESS",
  "business_detail_code": "OK",
  "member_id": 1,
  "tenant_id": 1,
  "user_id": 2,
  "status": 1
}
```
- 响应（资源不存在，404）：
```json
{
  "business_code": "RESOURCE_NOT_FOUND",
  "business_detail_code": "TENANT_MEMBER_NOT_FOUND"
}
```
- 响应（幂等重复，409）：
```json
{
  "business_code": "IDEMPOTENT_DUPLICATE",
  "business_detail_code": "TENANT_MEMBER_ALREADY_ENABLED"
}
```
- 实现说明：
  1. 按 `id` 查找目标 `TenantMember`
  2. 仅允许 `disabled -> active`
  3. 已启用成员重复启用返回幂等重复
  4. 若 `joined_at` 为空，则启用时补写当前时间
  5. 成功启用后记录 `TENANT_MEMBER_ENABLE` 租户级审计日志

### 13. 获取当前用户租户列表

- 功能：已登录用户读取自己当前可进入的租户列表及默认租户
- 路径：`/internal/auth/me/tenants`
- 方法：`GET`
- 权限：已登录用户
- 响应（成功，200）：
```json
{
  "business_code": "SUCCESS",
  "business_detail_code": "OK",
  "username": "zhangsan",
  "tenants": [
    {
      "tenant_id": 1,
      "tenant_code": "tenant_a",
      "tenant_name": "租户A",
      "roles": ["tenant_admin"]
    }
  ],
  "default_tenant": 1
}
```
- 响应（未认证，403）：
```json
{
  "business_code": "PERMISSION_DENIED",
  "business_detail_code": "NOT_AUTHENTICATED"
}
```
- 实现说明：
  1. 只返回当前用户 `TenantMember.status=ACTIVE` 的租户成员关系
  2. 过滤掉 `pending` 或停用租户下的成员关系
  3. 每个租户返回当前有效角色编码列表
  4. `default_tenant` 默认取排序后的首个有效租户；若无有效租户则返回 `null`

### 14. 查看租户级审计日志

- 功能：在租户上下文内查看当前租户的治理审计日志
- 路径：`/internal/auth/tenant-audit-logs`
- 方法：`GET`
- 权限：已登录用户，且必须是当前租户的 `tenant_admin` 或 `business_admin`
- 请求头：
```http
X-Tenant-Code: tenant_a
```
- 响应（成功，200）：
```json
{
  "business_code": "SUCCESS",
  "business_detail_code": "OK",
  "count": 1,
  "data": [
    {
      "id": 1,
      "action": "TENANT_MEMBER_INVITE",
      "target_type": "tenant_member",
      "target_id": "101"
    }
  ]
}
```
- 响应（权限不足，403）：
```json
{
  "business_code": "PERMISSION_DENIED",
  "business_detail_code": "TENANT_AUDIT_FORBIDDEN"
}
```
- 实现说明：
  1. 依赖 `TenantContextMiddleware` 从 `X-Tenant-Code` 解析当前租户
  2. 只允许当前租户下 `ACTIVE` 成员访问
  3. 成员角色必须包含 `tenant_admin` 或 `business_admin`
  4. 只返回 `AuditLog.tenant = request.tenant_context` 的记录
  5. 过滤掉其他租户与平台级审计日志

---

## 审计动作

| 动作 | 说明 | 记录内容 |
|------|------|----------|
| TENANT_CREATE | 创建租户 | tenant id, code, name |
| TENANT_UPDATE | 更新租户 | tenant id, 更新字段 |
| TENANT_DISABLE | 禁用租户 | tenant id |
| TENANT_ENABLE | 启用租户 | tenant id |
| TENANT_ADMIN_INITIALIZE | 初始化租户管理员 | member id, tenant id, user id, roles |
| TENANT_PLAN_CHANGE | 配置租户套餐 | tenant id, plan |
| USER_REGISTER | 平台注册账号 | user id, username, register_channel |
| TENANT_MEMBER_INVITE | 邀请租户成员 | member id, tenant id, user id |
| TENANT_MEMBER_CONFIRM_INVITATION | 确认租户邀请 | member id, tenant id, user id, status |
| TENANT_MEMBER_DISABLE | 停用租户成员 | member id, tenant id, user id, status |
| TENANT_MEMBER_ENABLE | 启用租户成员 | member id, tenant id, user id, status |

---

## 权限模型

租户为平台级模型，不关联具体租户。

- 租户管理权限：`access.manage_tenant`
- 租户查看权限：`access.view_tenant`
- 租户成员管理权限：`access.manage_tenant_member`
- 租户成员查看权限：`access.view_tenant_member`

---

## 与其他模块的关系

1. **业务表关联**：所有业务表（Drone、Route、Mission 等）通过 `tenant_id` 外键关联到 `Tenant`
2. **租户成员**：`TenantMember` 关联 `Tenant` 与 `User`
3. **租户隔离**：`TenantContextMiddleware` 强制解析租户上下文
