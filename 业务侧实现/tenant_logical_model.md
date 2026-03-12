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
- `POST /internal/auth/tenant-members/invite`: 邀请租户成员
- `POST /internal/auth/tenant-members/confirm-invitation`: 用户确认租户邀请
- `GET /internal/auth/me/tenants`: 获取当前用户可进入的租户列表

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
- 当前实现中的租户邀请接口仍位于 `internal/auth` 平面，由具备 `access.manage_tenant_member` 的系统操作者发起
- 邀请确认接口同样保留在 `internal/auth` 平面，但调用者切换为被邀请的已登录账号
- 当前用户租户列表接口位于 `internal/auth/me/*` 平面，作为认证入口后的租户上下文读取补充

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
