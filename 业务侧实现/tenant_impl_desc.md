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

### 2. 租户上下文解析

- 功能：从 HTTP Header 解析当前租户
- 中间件：`TenantContextMiddleware`
- Header：`X-Tenant-Code`
- 实现逻辑：
  1. 读取 `X-Tenant-Code` header
  2. 查询 `Tenant` 表，条件：`code=header值 AND status=ENABLED`
  3. 找到则设置 `request.tenant_context = tenant`
  4. 未找到或已禁用则返回 403

### 3. 邀请租户成员

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

### 4. 确认租户邀请

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

### 5. 获取当前用户租户列表

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

---

## 审计动作

| 动作 | 说明 | 记录内容 |
|------|------|----------|
| TENANT_CREATE | 创建租户 | tenant id, code, name |
| TENANT_UPDATE | 更新租户 | tenant id, 更新字段 |
| TENANT_DISABLE | 禁用租户 | tenant id |
| TENANT_ENABLE | 启用租户 | tenant id |
| TENANT_MEMBER_INVITE | 邀请租户成员 | member id, tenant id, user id |
| TENANT_MEMBER_CONFIRM_INVITATION | 确认租户邀请 | member id, tenant id, user id, status |

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
