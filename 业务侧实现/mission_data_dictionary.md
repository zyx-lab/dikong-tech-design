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
| tenant_id | bigint | FK, NOT NULL | - | 租户 ID |
| name | varchar(100) | NOT NULL | - | 任务名称 |
| route_id | bigint | FK, NOT NULL | - | 航线 ID |
| route_name | varchar(100) | - | "" | 航线名称（冗余） |
| drone_id | bigint | FK, NOT NULL | - | 无人机 ID |
| drone_name | varchar(100) | - | "" | 无人机名称（冗余） |
| pilot_id | bigint | FK, NOT NULL | - | 飞手成员 ID（TenantMember） |
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
1. `route`、`drone`、`pilot` 必须属于当前租户。
2. 创建任务时 `route` 必须为 `ACTIVE`，`drone` 必须为 `ENABLED`。
3. `pilot` 必须是当前租户下的 `ACTIVE TenantMember`，其账号需存在在职 `staff_profile`，且成员已绑定 `pilot_operator`。
4. status 不可通过 PATCH 直接修改，需通过状态动作接口。

---

## 2. 与实现对应

1. 模型：`apps/mission/models.py`
2. 序列化与校验：`apps/mission/serializers.py`
3. 接口：`apps/mission/views.py`

---

## 3. 业务响应契约（Business API）

说明：业务 API 响应体统一包含 `code`、`msg`、`data` 三个字段。

| 字段 | 类型 | 说明 |
| ------ | ------ | ------ |
| code | string | 业务码。成功固定为 `00000` |
| msg | string | 响应消息。成功通常为 `success` |
| data | object / array / null | 业务数据；失败时为错误上下文 |

| code | 典型 HTTP | 语义 |
| ------ | ------ | ------ |
| 00000 | 200 / 201 | 业务处理成功 |
| A0401 | 401 | 未登录或登录已失效 |
| A0403 | 403 | 无操作权限 |
| B0001 | 400 | 请求参数校验失败 |
| C0201 | 409 | 当前状态不允许操作 |
| C0404 | 404 | 目标资源不存在 |
| E0001 | 500 | 系统异常 |
