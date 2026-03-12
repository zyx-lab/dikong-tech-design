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
| plan | CharField(32) | 套餐类型（预留） |
| created_at | DateTimeField | 创建时间 |
| updated_at | DateTimeField | 更新时间 |

### TenantStatus 枚举
- DISABLED = 0, "disabled"
- ENABLED = 1, "enabled"

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

---

## 审计动作

| 动作 | 说明 | 记录内容 |
|------|------|----------|
| TENANT_CREATE | 创建租户 | tenant id, code, name |
| TENANT_UPDATE | 更新租户 | tenant id, 更新字段 |
| TENANT_DISABLE | 禁用租户 | tenant id |
| TENANT_ENABLE | 启用租户 | tenant id |

---

## 权限模型

租户为平台级模型，不关联具体租户。

- 租户管理权限：`access.manage_tenant`
- 租户查看权限：`access.view_tenant`

---

## 与其他模块的关系

1. **业务表关联**：所有业务表（Drone、Route、Mission 等）通过 `tenant_id` 外键关联到 `Tenant`
2. **租户成员**：`TenantMember` 关联 `Tenant` 与 `User`
3. **租户隔离**：`TenantContextMiddleware` 强制解析租户上下文
