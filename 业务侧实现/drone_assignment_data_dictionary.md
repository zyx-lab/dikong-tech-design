# 低空智能巡检平台 - 无人机分配

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

本数据字典覆盖当前已落地的业务表：`drone_assignments`。

---

## 1. drone_assignments（无人机分配表）

**说明**：维护无人机与飞手的分配关系，是 `ASSIGNED` 范围授权的事实来源。

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 主键 |
| drone_id | bigint | FK, NOT NULL | - | 关联无人机 ID |
| staff_id | bigint | FK, NOT NULL | - | 关联飞手 ID |
| status | varchar(16) | NOT NULL | ACTIVE | 分配状态 |
| start_at | timestamp | NOT NULL | now() | 分配生效时间 |
| end_at | timestamp | - | - | 分配结束时间 |
| created_by_staff_id | bigint | - | - | 操作人 staff ID |
| created_at | timestamp | NOT NULL | now() | 创建时间 |
| updated_at | timestamp | NOT NULL | now() | 更新时间 |

**status 状态值**：

| 值 | 含义 |
|----|------|
| ACTIVE | 生效中 |
| INACTIVE | 已失效 |

**约束与规则**：
1. 唯一约束：同一 `(drone_id, staff_id)` 在 `ACTIVE` 状态下唯一。
2. 取消分配采用软失效（`ACTIVE -> INACTIVE`），不物理删除。
3. 已是 `INACTIVE` 的记录再次取消仍返回成功态（幂等）。
4. 支持恢复分配：`INACTIVE -> ACTIVE`，并清空 `end_at`。
5. 已是 `ACTIVE` 的记录再次恢复仍返回成功态（幂等）。

---

## 2. 外键关系

1. `drone_assignments.drone_id -> drones.id`
2. `drone_assignments.staff_id -> staff_profiles.id`

---

## 3. 与实现对应

1. 模型：`apps/drone_assignment/models.py`
2. 序列化与校验：`apps/drone_assignment/serializers.py`
3. 接口：`apps/drone_assignment/views.py`

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

| business_detail_code | 语义 |
| ------ | ------ |
| OK | 成功 |
| NOT_AUTHENTICATED | 未登录或认证信息缺失 |
| FORBIDDEN | 已登录但无权限 |
| NOT_FOUND | 资源不存在 |
| VALIDATION_ERROR | 参数校验失败 |
| STATE_CONFLICT | 业务状态冲突 |
