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

### tenants（租户主表）
- 主键: id (BigAutoField)
- 状态字段: status

---

## 状态机

### Tenant 状态机

| status | 含义 | 可转换到 |
|--------|------|----------|
| 0 (DISABLED) | 禁用 | 1 (ENABLED) |
| 1 (ENABLED) | 启用 | 0 (DISABLED) |

---

## 关系与约束

### 业务约束
1. `code` 全局唯一
2. 租户禁用后，该租户所有业务数据不可通过 `X-Tenant-Code` 访问

### 跨表约束
- 所有业务表（drones, routes, missions, flight_records, media_files, drone_assignments）通过 `tenant_id` 关联到 `tenants`
- 业务表的外键必须在同一租户内

---

## 生命周期入口

- `POST /internal/auth/tenants`: 创建租户

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

---

## 权限设计

### 平台级权限
| 权限码 | 说明 |
|--------|------|
| access.view_tenant | 查看租户 |
| access.manage_tenant | 管理租户（创建、修改、禁用） |

### 权限归属
- `platform_admin`: 拥有 access.manage_tenant
- 租户管理员不直接管理租户（租户由平台管理）

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
