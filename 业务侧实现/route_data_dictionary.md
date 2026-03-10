# 低空智能巡检平台 - 航线

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

本数据字典覆盖当前已落地的业务表：`routes`。

---

## 1. routes（航线表）

**说明**：存储航线基础信息与状态。

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 主键 |
| name | varchar(100) | NOT NULL | - | 航线名称 |
| route_type | smallint | NOT NULL | 0 | 航线类型扩展位 |
| drone_type_id | bigint | - | - | 适用无人机类型 ID |
| total_distance | decimal(12,2) | - | - | 航线总长度（米） |
| estimated_duration | int | - | - | 预计飞行时长（秒） |
| waypoint_count | int | - | - | 航点数量 |
| creator_name | varchar(50) | - | "" | 创建人姓名 |
| status | smallint | NOT NULL | 1 | 状态：0=禁用, 1=正常 |
| created_at | timestamp | NOT NULL | now() | 创建时间 |
| updated_at | timestamp | NOT NULL | now() | 更新时间 |

**status 状态值**：

| 值 | 含义 |
|----|------|
| 0 | 禁用 |
| 1 | 正常 |

**route_type 枚举值**：

| 值 | 含义 |
|----|------|
| 0 | 待扩展 |

**业务规则**：
1. 删除航线时，若已被任务引用，则软禁用（status=0）而非物理删除。
2. 显式启用/禁用航线通过状态动作接口完成，不能通过 PATCH 直接改写 status。
3. `POST /api/v1/routes/{id}/disable` 只修改 `routes.status`，不删除航点、不解绑任务，请求体必须为空。
4. 已处于禁用态的航线重复执行 `disable` 按幂等成功返回当前状态。

---

## 2. 与实现对应

1. 模型：`apps/route/models.py`
2. 序列化与校验：`apps/route/serializers.py`
3. 接口：`apps/route/views.py`

---

## 3. 业务响应码字典（Business API）

说明：业务 API 响应体包含 `business_code`（主业务码）与 `business_detail_code`（细分原因码）。

| business_code | 典型 HTTP | 语义 |
| ------ | ------ | ------ |
| SUCCESS | 200 / 201 | 业务处理成功 |
| INVALID_PARAMS | 400 | 请求参数校验失败 |
| PERMISSION_DENIED | 401 / 403 | 身份或权限不足 |
| RESOURCE_NOT_FOUND | 404 | 目标资源不存在 |
| STATE_CONFLICT | 409 | 状态机冲突 |

| business_detail_code | 语义 |
| ------ | ------ |
| OK | 成功 |
| NOT_AUTHENTICATED | 未登录或认证信息缺失 |
| FORBIDDEN | 已登录但无权限 |
| NOT_FOUND | 资源不存在 |
| VALIDATION_ERROR | 参数校验失败 |
| STATE_CONFLICT | 业务状态冲突 |
