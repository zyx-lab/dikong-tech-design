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

本数据字典覆盖当前已落地的业务表：`tenants`。

---

## 1. tenants（租户主表）

**说明**：存储租户（买家）基本信息，平台级模型，不包含 tenant_id。

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 主键 |
| code | varchar(64) | NOT NULL, UNIQUE | - | 租户编码（唯一） |
| name | varchar(128) | NOT NULL | - | 租户名称 |
| status | smallint | NOT NULL | 1 | 租户状态 |
| plan | varchar(32) | - | - | 套餐类型（预留） |
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

## 2. 关系与外键

无外键引用（平台级模型）。

---

## 3. 与实现对应

1. 模型：`apps/access/models.py` - `Tenant`
2. 序列化与校验：`apps/access/serializers.py`
3. 接口：`apps/access/views.py` - `TenantViewSet`

---

## 4. 业务响应码字典（Business API）

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

当前实现中，`POST /internal/auth/tenants` 返回 `business_code + business_detail_code`。
