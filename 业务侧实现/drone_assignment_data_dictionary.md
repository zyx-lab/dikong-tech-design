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

**说明**：维护无人机与租户成员的分配关系，是 `ASSIGNED` 范围授权的事实来源。

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 主键 |
| tenant_id | bigint | FK, NOT NULL | - | 当前租户 ID |
| drone_id | bigint | FK, NOT NULL | - | 关联无人机 ID |
| tenant_member_id | bigint | FK, NOT NULL | - | 关联租户成员 ID |
| status | varchar(16) | NOT NULL | ACTIVE | 分配状态 |
| start_at | timestamp | NOT NULL | now() | 分配生效时间 |
| end_at | timestamp | - | - | 分配结束时间 |
| created_by_tenant_member_id | bigint | - | - | 操作人 TenantMember ID |
| created_at | timestamp | NOT NULL | now() | 创建时间 |
| updated_at | timestamp | NOT NULL | now() | 更新时间 |

**status 状态值**：

| 值 | 含义 |
|----|------|
| ACTIVE | 生效中 |
| INACTIVE | 已失效 |

**约束与规则**：
1. 唯一约束：同一 `(drone_id, tenant_member_id)` 在 `ACTIVE` 状态下唯一。
2. `tenant_id`、`drone_id`、`tenant_member_id` 必须属于同一租户。
3. 仅允许分配给 `ACTIVE` 的租户成员，且其账号必须存在在职 `staff_profile`。
4. 仅允许分配给已绑定 `pilot_operator` 角色的成员。
5. 取消分配采用软失效（`ACTIVE -> INACTIVE`），不物理删除。
6. `ACTIVE` 记录不得写入 `end_at`；`INACTIVE` 记录必须写入 `end_at`。
7. `OWN / ASSIGNED` 口径统一以 `TenantMember.id` 命中，不再使用全局 `staff_id`。
8. 已是 `INACTIVE` 的记录再次取消仍返回成功态（幂等）。
9. 支持恢复分配：`INACTIVE -> ACTIVE`，并清空 `end_at`。
10. 已是 `ACTIVE` 的记录再次恢复仍返回成功态（幂等）。

---

## 2. 外键关系

1. `drone_assignments.tenant_id -> tenants.id`
2. `drone_assignments.drone_id -> drones.id`
3. `drone_assignments.tenant_member_id -> tenant_members.id`

---

## 3. 与实现对应

1. 模型：`apps/drone_assignment/models.py`
2. 序列化与校验：`apps/drone_assignment/serializers.py`
3. 接口：`apps/drone_assignment/views.py`

---

## 4. 业务响应契约（Business API）

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
