# 租户逻辑模型

- generated_at: 2026-03-12
- entity: tenant

## 数据库

PostgreSQL

---

## 文档格式说明

本文档为 **逻辑模型** 类型文档，记录实体关系、状态机、生命周期、接口语义等。

### 更新本文档的指南（大模型用）

当需要更新此文档时，请遵循以下格式：

```
## 实体主表
- table: {表名}
- 主键: {主键定义}

## 状态机
- {状态字段}: {状态值列表}

## 关系与约束
- {外键关系}
- {业务约束}

## 生命周期入口
- {HTTP方法} {路径}: {功能描述}

## 接口语义
### {API名称}
- 功能：{功能描述}
- 路径：{API路径}
- 方法：{HTTP方法}
- 状态流转：{状态变化}
- 有效状态：{允许执行该操作的状态}
- 无效状态：{禁止执行该操作的状态列表}
- 业务码：{返回的业务码}
```

---

## 实体主表

| 表名 | 说明 |
|------|------|
| tenants | 租户主表 |
| tenant_members | 租户成员身份 |
| tenant_member_roles | 租户成员角色绑定 |

### tenants（租户主表）
- 主键: id (BigAutoField)
- 状态字段: status

### tenant_members（租户成员身份）
- 主键: id (BigAutoField)
- 状态字段: status
- 邀请确认字段: invitation_token

---

## 状态机

### Tenant 状态机

| status | 含义 | 可转换到 |
|--------|------|----------|
| 0 (DISABLED) | 禁用 | 1 (ENABLED) |
| 1 (ENABLED) | 启用 | 0 (DISABLED) |

### TenantMember 状态机

| status | 含义 | 可转换到 |
|--------|------|----------|
| 0 (PENDING) | 已发出邀请，等待确认 | 1 (ACTIVE), 2 (DISABLED) |
| 1 (ACTIVE) | 已加入租户 | 2 (DISABLED) |
| 2 (DISABLED) | 已禁用 | 1 (ACTIVE) |

---

## 关系与约束

### 业务约束
1. `code` 全局唯一
2. 租户禁用后，该租户所有业务数据不可通过 `X-Tenant-Code` 访问
3. `tenant_members` 上 `(tenant_id, user_id)` 唯一，同一账号不能重复加入同一租户
4. 邀请制成员在确认前必须处于 `PENDING`，且保留 `invitation_token`

### 跨表约束
- 所有业务表（drones, routes, missions, flight_records, media_files, drone_assignments）通过 `tenant_id` 关联到 `tenants`
- 业务表的外键必须在同一租户内
- `tenant_member_roles` 必须绑定到有效的 `tenant_members`

---

## 生命周期入口

- `POST /internal/auth/tenants`: 创建租户
- `POST /internal/auth/tenants/{id}/disable`: 停用租户
- `POST /internal/auth/tenants/{id}/enable`: 启用租户
- `POST /internal/auth/tenants/{id}/initialize-admin`: 初始化租户管理员
- `POST /internal/auth/tenants/{id}/set-plan`: 配置租户套餐
- `POST /internal/auth/users/register`: 平台注册账号
- `POST /internal/auth/users/register/by-phone`: 手机号注册平台账号（mock 验证码）
- `POST /internal/auth/tenant-members/invite`: 邀请租户成员
- `POST /internal/auth/tenant-members/confirm-invitation`: 用户确认租户邀请
- `POST /internal/auth/tenant-members/{id}/disable`: 停用租户成员
- `POST /internal/auth/tenant-members/{id}/enable`: 启用租户成员
- `GET /internal/auth/me/invitations`: 获取当前用户待确认邀请列表
- `GET /internal/auth/me/tenants`: 获取当前用户可进入的租户列表
- `GET /internal/auth/tenant-audit-logs`: 查看租户级审计日志

---

## 接口语义

### 创建租户
- 功能：创建一个新租户
- 路径：`/internal/auth/tenants`
- 方法：`POST`
- 状态流转：N/A（新建即 ENABLED）
- 有效状态：N/A
- 业务码：
  - SUCCESS: 创建成功
  - INVALID_PARAMS: 参数校验失败（缺少必填字段、code 重复）

### 停用租户
- 功能：平台管理员停用指定租户
- 路径：`/internal/auth/tenants/{id}/disable`
- 方法：`POST`
- 状态流转：ENABLED -> DISABLED
- 有效状态：目标租户存在且当前状态为 ENABLED
- 无效状态：
  - 目标租户不存在
  - 目标租户已经是 DISABLED，重复停用
- 业务码：
  - SUCCESS: 停用成功
  - RESOURCE_NOT_FOUND: 租户不存在
  - IDEMPOTENT_DUPLICATE: 租户已停用
  - PERMISSION_DENIED: 当前操作者未登录或不具备平台租户管理权限

### 启用租户
- 功能：平台管理员启用指定租户
- 路径：`/internal/auth/tenants/{id}/enable`
- 方法：`POST`
- 状态流转：DISABLED -> ENABLED
- 有效状态：目标租户存在且当前状态为 DISABLED
- 无效状态：
  - 目标租户不存在
  - 目标租户已经是 ENABLED，重复启用
- 业务码：
  - SUCCESS: 启用成功
  - RESOURCE_NOT_FOUND: 租户不存在
  - IDEMPOTENT_DUPLICATE: 租户已启用
  - PERMISSION_DENIED: 当前操作者未登录或不具备平台租户管理权限

### 初始化租户管理员
- 功能：平台管理员为指定租户初始化首个 `tenant_admin`
- 路径：`/internal/auth/tenants/{id}/initialize-admin`
- 方法：`POST`
- 状态流转：成员不存在 / PENDING / DISABLED -> ACTIVE，并绑定 `tenant_admin`
- 有效状态：
  - 目标租户存在且状态为 ENABLED
  - 当前租户尚未存在有效的 `tenant_admin`
- 无效状态：
  - 目标租户不存在
  - 请求体缺少必要字段
  - 目标租户不是 ENABLED
  - 当前租户已经存在有效 `tenant_admin`
- 业务码：
  - SUCCESS: 初始化成功
  - INVALID_PARAMS: 请求体缺少必要字段
  - RESOURCE_NOT_FOUND: 租户或用户不存在
  - STATE_CONFLICT: 目标租户当前状态不允许初始化管理员
  - IDEMPOTENT_DUPLICATE: 当前租户已完成管理员初始化
  - PERMISSION_DENIED: 当前操作者未登录或不具备平台租户管理权限

### 配置租户套餐
- 功能：平台管理员为指定租户设置或变更套餐标识
- 路径：`/internal/auth/tenants/{id}/set-plan`
- 方法：`POST`
- 状态流转：租户状态不变，仅更新 `plan`
- 有效状态：目标租户存在，且请求体提供新的非空套餐值
- 无效状态：
  - 目标租户不存在
  - 请求体缺少 `plan` 或为空
  - 新套餐值与当前套餐值相同
- 业务码：
  - SUCCESS: 配置成功
  - INVALID_PARAMS: 请求体缺少有效 `plan`
  - RESOURCE_NOT_FOUND: 租户不存在
  - IDEMPOTENT_DUPLICATE: 套餐值未变化
  - PERMISSION_DENIED: 当前操作者未登录或不具备平台租户管理权限

### 平台注册账号
- 功能：终端用户在平台侧自助注册账号，等待后续租户邀请加入
- 路径：`/internal/auth/users/register`
- 方法：`POST`
- 状态流转：N/A（创建平台账号与占位人员档案；待邀请状态通过“尚无 ACTIVE 租户成员关系”表达）
- 有效状态：请求体提供新的 `username`、`password`、`name`、`phone`
- 无效状态：
  - `username` 已存在
  - 请求体缺少必要字段或字段为空
- 业务码：
  - SUCCESS: 注册成功
  - INVALID_PARAMS: 请求体字段缺失或校验失败
  - IDEMPOTENT_DUPLICATE: `username` 已存在

### 手机号注册平台账号
- 功能：终端用户通过手机号与短信验证码完成平台注册，等待后续租户邀请加入
- 路径：`/internal/auth/users/register/by-phone`
- 方法：`POST`
- 状态流转：N/A（创建平台账号与占位人员档案；待邀请状态通过“尚无 ACTIVE 租户成员关系”表达）
- 有效状态：请求体提供新的 `phone` 与有效 `sms_code`
- 无效状态：
  - `phone` 已存在
  - 请求体缺少必要字段或字段为空
  - 当前 mock 短信验证码不等于固定值 `123456`
- 业务码：
  - SUCCESS: 注册成功
  - INVALID_PARAMS: 请求体字段缺失、短信验证码非法或校验失败
  - IDEMPOTENT_DUPLICATE: `phone` 已存在
- 说明：
  - 当前实现仅为 mock 验证码方案，使用固定验证码 `123456`
  - 当前实现会将手机号写入平台账号 `username`，并生成内部随机密码；真实短信服务、正式手机号登录/设密流程待后续扩展

### 邀请租户成员
- 功能：向已注册账号发起加入租户邀请
- 路径：`/internal/auth/tenant-members/invite`
- 方法：`POST`
- 状态流转：不存在 -> PENDING
- 有效状态：租户存在且启用；目标账号尚未是该租户成员
- 无效状态：
  - 目标租户不存在
  - 目标账号不存在
  - `(tenant_id, user_id)` 已存在成员关系
- 业务码：
  - SUCCESS: 邀请发送成功
  - INVALID_PARAMS: 请求体缺少必填字段或角色编码非法
  - RESOURCE_NOT_FOUND: 租户或用户不存在
  - STATE_CONFLICT: 目标账号已在租户内存在成员关系

### 确认租户邀请
- 功能：被邀请用户确认 invitation token，激活自己的租户成员身份
- 路径：`/internal/auth/tenant-members/confirm-invitation`
- 方法：`POST`
- 状态流转：PENDING -> ACTIVE
- 有效状态：`invitation_token` 命中当前用户自己的 pending 成员记录
- 无效状态：
  - invitation token 不存在
  - invitation token 不属于当前登录用户
  - 成员已经是 active，重复确认
  - 成员不是 pending（如 disabled）
- 业务码：
  - SUCCESS: 确认成功
  - INVALID_PARAMS: 未提供 invitation_token
  - PERMISSION_DENIED: 登录用户不是该邀请的目标账号
  - RESOURCE_NOT_FOUND: invitation token 不存在
  - IDEMPOTENT_DUPLICATE: 邀请已确认
  - STATE_CONFLICT: 邀请记录已不处于 pending 状态

### 停用租户成员
- 功能：停用指定租户成员，使其失去当前租户内的有效成员身份
- 路径：`/internal/auth/tenant-members/{id}/disable`
- 方法：`POST`
- 状态流转：PENDING -> DISABLED 或 ACTIVE -> DISABLED
- 有效状态：目标成员存在，且当前状态为 PENDING 或 ACTIVE
- 无效状态：
  - 目标成员不存在
  - 成员已经是 DISABLED，重复停用
- 业务码：
  - SUCCESS: 停用成功
  - RESOURCE_NOT_FOUND: 租户成员不存在
  - IDEMPOTENT_DUPLICATE: 租户成员已停用
  - PERMISSION_DENIED: 当前操作者未登录或不具备成员管理权限

### 启用租户成员
- 功能：启用指定租户成员，使其恢复当前租户内的有效成员身份
- 路径：`/internal/auth/tenant-members/{id}/enable`
- 方法：`POST`
- 状态流转：DISABLED -> ACTIVE
- 有效状态：目标成员存在，且当前状态为 DISABLED
- 无效状态：
  - 目标成员不存在
  - 成员已经是 ACTIVE，重复启用
- 业务码：
  - SUCCESS: 启用成功
  - RESOURCE_NOT_FOUND: 租户成员不存在
  - IDEMPOTENT_DUPLICATE: 租户成员已启用
  - PERMISSION_DENIED: 当前操作者未登录或不具备成员管理权限

### 获取当前用户租户列表
- 功能：已登录用户读取自己当前可进入的租户列表
- 路径：`/internal/auth/me/tenants`
- 方法：`GET`
- 状态流转：无状态变更，只读取当前 ACTIVE 成员关系
- 有效状态：用户已登录，且可存在 0..N 条 ACTIVE 租户成员关系
- 无效状态：
  - 当前请求未认证
- 业务码：
  - SUCCESS: 返回租户列表与 default_tenant
  - PERMISSION_DENIED: 未登录

### 获取当前用户待确认邀请列表
- 功能：已登录用户读取自己所有仍待确认的租户邀请
- 路径：`/internal/auth/me/invitations`
- 方法：`GET`
- 状态流转：无状态变更，只读取当前用户的 pending 邀请记录
- 有效状态：用户已登录，且可存在 0..N 条带 `invitation_token` 的 `PENDING` 成员记录
- 无效状态：
  - 当前请求未认证
- 业务码：
  - SUCCESS: 返回待确认邀请列表
  - PERMISSION_DENIED: 未登录

### 查看租户级审计日志
- 功能：在当前租户上下文内读取租户治理审计日志
- 路径：`/internal/auth/tenant-audit-logs`
- 方法：`GET`
- 状态流转：无状态变更，只读取当前租户下的审计记录
- 有效状态：
  - 请求已认证
  - `X-Tenant-Code` 解析出有效租户
  - 当前用户是该租户的 `ACTIVE` 成员
  - 当前成员角色包含 `tenant_admin` 或 `business_admin`
- 无效状态：
  - 当前请求未认证
  - 未提供租户上下文
  - 当前用户不是该租户的有效成员
  - 当前成员角色不允许查看租户审计
- 业务码：
  - SUCCESS: 返回当前租户的审计日志列表
  - PERMISSION_DENIED: 未登录、缺少租户上下文、成员身份无效或角色不足

---

## 权限设计

### 平台级权限
| 权限码 | 说明 |
|--------|------|
| access.view_tenant | 查看租户 |
| access.manage_tenant | 管理租户（创建、修改、禁用） |
| access.view_tenant_member | 查看租户成员 |
| access.manage_tenant_member | 管理租户成员 |

### 权限归属
- `platform_admin`: 拥有 access.manage_tenant
- 租户管理员不直接管理租户（租户由平台管理）
- 租户管理员初始化接口位于平台侧 `internal/auth/tenants/*` 平面，由具备 `access.manage_tenant` 的操作者执行
- 当前实现中的租户邀请接口仍位于 `internal/auth` 平面，由具备 `access.manage_tenant_member` 的系统操作者发起
- 邀请确认接口同样保留在 `internal/auth` 平面，但调用者切换为被邀请的已登录账号
- 租户成员停用接口同样位于 `internal/auth` 平面，当前由具备 `access.manage_tenant_member` 的系统操作者触发
- 当前用户租户列表接口位于 `internal/auth/me/*` 平面，作为认证入口后的租户上下文读取补充
- 租户级审计接口同样位于 `internal/auth` 平面，但其数据边界由 `X-Tenant-Code` 与租户成员关系共同决定

---

## 与其他实体的关系

```
Tenant (平台级)
  │
  ├── 1:N ──► TenantMember (租户成员)
  │              │
  │              └── N:N ──► SystemRole (角色)
  │
  └── 1:N ──► 业务表 (drones, routes, missions, ...)
              │
              └── 业务表通过 tenant_id 关联回 Tenant
```

---

## 多租户隔离机制

1. **租户上下文解析**：`TenantContextMiddleware` 解析 `X-Tenant-Code` header
2. **查询过滤**：所有业务 ViewSet 默认按 `tenant_id` 过滤
3. **写入校验**：创建业务数据时必须指定 `tenant_id`
4. **跨租户防护**：
   - 外键约束确保关联对象同租户
   - 唯一约束改为租户内唯一（如 `tenant_id + code`）
5. **邀请链路**：邀请制通过 `TenantMember.invitation_token` 持有待确认状态，确认前不写入 `joined_at`
