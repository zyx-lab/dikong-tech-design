# 低空智能巡检平台 - 任务

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

本数据字典覆盖当前已落地的业务表：`missions`。

---

## 1. missions（任务表）

**说明**：存储任务执行计划信息。

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 主键 |
| name | varchar(100) | NOT NULL | - | 任务名称 |
| route_id | bigint | FK, NOT NULL | - | 航线 ID |
| route_name | varchar(100) | - | "" | 航线名称（冗余） |
| drone_id | bigint | FK, NOT NULL | - | 无人机 ID |
| drone_name | varchar(100) | - | "" | 无人机名称（冗余） |
| pilot_id | bigint | FK, NOT NULL | - | 飞手 ID |
| pilot_name | varchar(50) | - | "" | 飞手姓名（冗余） |
| scheduled_at | timestamp | - | - | 计划执行时间 |
| remark | varchar(500) | - | "" | 任务备注 |
| status | smallint | NOT NULL | 0 | 任务状态 |
| created_at | timestamp | NOT NULL | now() | 创建时间 |
| updated_at | timestamp | NOT NULL | now() | 更新时间 |

**status 状态值**：

| 值 | 含义 |
|----|------|
| 0 | 待执行 |
| 1 | 执行中 |
| 2 | 已暂停 |
| 3 | 已完成 |
| 4 | 已取消 |
| 5 | 执行失败 |

**业务规则**：
1. 创建任务时 route 必须为 ACTIVE，drone 必须为 ENABLED，pilot 必须为 pilot_operator 且在职。
2. status 不可通过 PATCH 直接修改，需通过状态动作接口。

---

## 2. 与实现对应

1. 模型：`apps/mission/models.py`
2. 序列化与校验：`apps/mission/serializers.py`
3. 接口：`apps/mission/views.py`

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
